"""Employee portal API router.

All routes require a valid EMPLOYEE JWT. Employees can only access their own
data — cross-user access raises 403/404. AI assessments are submitted to the
existing VERITAS compliance workflow (POST /v1/advisories) and results are
retrieved via GET /v1/advisories/{run_id}.
"""
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import audit, require_role
from ..config import settings
from ..db import get_db
from ..gratuity_calc import GratuityCalcRequest, GratuityCalcResponse, compute_gratuity
from ..models import (
    ApplicationHistory, ApplicationStatus, EmployeeProfile,
    GratuityApplication, HRReview, User, UserRole,
)

L = logging.getLogger("employee_router")
router = APIRouter(prefix="/employee", tags=["employee-portal"])

_emp_dep = require_role(UserRole.EMPLOYEE)

# ─────────────────────────── Pydantic schemas ────────────────────────────────

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, max_length=255)
    employee_id_code: Optional[str] = Field(None, max_length=64)
    department: Optional[str] = Field(None, max_length=128)
    designation: Optional[str] = Field(None, max_length=128)
    date_of_joining: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    employment_type: Optional[str] = Field(None, max_length=32)
    organization_name: Optional[str] = Field(None, max_length=255)


class GratuityFormData(BaseModel):
    """Gratuity assessment form submitted by an employee."""
    # Employee details
    full_name: str = Field(..., min_length=2, max_length=255)
    employee_id_code: Optional[str] = Field(None, max_length=64)
    employer_name: str = Field(..., min_length=2, max_length=255)
    # Employment dates
    date_of_joining: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    last_working_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # Classification
    employment_type: str = Field(..., pattern=r"^(permanent|fixed_term|contractual|other)$")
    separation_reason: str = Field(..., pattern=r"^(resignation|retirement|superannuation|termination|contract_expiry|death|disablement|other)$")
    # Salary
    basic_salary: float = Field(..., ge=0, description="Monthly basic salary in INR")
    dearness_allowance: float = Field(0.0, ge=0, description="Monthly DA in INR")
    salary_type: str = Field("monthly", pattern=r"^(monthly|daily)$")
    # Additional
    establishment_type: Optional[str] = Field(None, max_length=64)
    employee_count: Optional[int] = Field(None, ge=1)
    additional_notes: Optional[str] = Field(None, max_length=1000)


class ApplicationResponse(BaseModel):
    id: int
    status: str
    run_id: Optional[str]
    ai_route: Optional[str]
    ai_summary: Optional[str]
    ai_completed_at: Optional[float]
    created_at: float
    submitted_at: Optional[float]
    updated_at: float
    form_data: Dict[str, Any]
    hr_reviews: List[Dict[str, Any]]


# ─────────────────────────── Helpers ─────────────────────────────────────────

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def _build_veritas_request(form: GratuityFormData, user: User) -> dict:
    """Convert the employee form into the AskRequest schema for the existing AI engine."""
    from datetime import date
    doj = date.fromisoformat(form.date_of_joining)
    lwd = date.fromisoformat(form.last_working_date) if form.last_working_date else date.today()
    years = max((lwd - doj).days / 365.25, 0)

    # Map employment_type to existing contract enum values
    emp_type_map = {
        "permanent": "permanent",
        "fixed_term": "fixed_term",
        "contractual": "contract_labour",
        "other": "permanent",
    }
    # Map separation_reason to existing enum values
    sep_map = {
        "resignation": "resignation",
        "retirement": "retirement",
        "superannuation": "superannuation",
        "termination": "termination",
        "contract_expiry": "contract_expiry",
        "death": "death",
        "disablement": "disablement",
        "other": "resignation",
    }

    # Build a natural-language question for the AI engine
    emp_type_str = form.employment_type.replace("_", "-")
    sep_reason_str = form.separation_reason.replace("_", " ")
    wage = form.basic_salary + form.dearness_allowance
    question = (
        f"A {emp_type_str} employee joined on {form.date_of_joining} "
        f"and is leaving on {lwd.isoformat()} due to {sep_reason_str} "
        f"after {round(years, 2)} years of service. "
        f"Last drawn eligible monthly wages (basic + DA): INR {wage:,.0f}. "
        f"Employer: {form.employer_name}. "
        f"Is gratuity payable and what is the applicable amount?"
    )

    # Employment block
    employment: dict = {
        "employment_type": emp_type_map.get(form.employment_type, "permanent"),
        "start_date": form.date_of_joining,
        "end_date": lwd.isoformat(),
        "years_of_service": round(years, 2),
        "separation_reason": sep_map.get(form.separation_reason, "resignation"),
    }

    # Establishment block
    establishment: dict = {}
    if form.employee_count:
        establishment["employee_count"] = form.employee_count
    if form.establishment_type:
        est_map = {"factory": "factory", "shop": "shop_establishment", "it": "it_services",
                   "mine": "mine", "plantation": "plantation"}
        establishment["establishment_type"] = est_map.get(
            form.establishment_type.lower(), "other"
        )

    payload: dict = {
        "client_id": f"emp-{user.id}",
        "question": question[:2000],
        "employment": employment,
        "as_of_date": lwd.isoformat(),
    }
    if establishment:
        payload["establishment"] = establishment
    if form.additional_notes:
        payload["notes"] = form.additional_notes[:500]
    return payload


def _transition(db: Session, app: GratuityApplication,
                new_status: str, changed_by_id: int, notes: Optional[str] = None):
    old = app.status
    app.status = new_status
    app.updated_at = time.time()
    hist = ApplicationHistory(
        application_id=app.id,
        from_status=old,
        to_status=new_status,
        changed_by_user_id=changed_by_id,
        notes=notes,
        created_at=time.time(),
    )
    db.add(hist)


def _serialize_app(app: GratuityApplication) -> dict:
    reviews = [
        {"id": r.id, "decision": r.decision, "comments": r.comments,
         "reviewer_id": r.reviewer_id, "created_at": r.created_at}
        for r in (app.hr_reviews or [])
    ]
    try:
        fd = json.loads(app.form_data) if app.form_data else {}
    except Exception:
        fd = {}
    return {
        "id": app.id,
        "status": app.status,
        "run_id": app.run_id,
        "ai_route": app.ai_route,
        "ai_summary": app.ai_summary,
        "ai_completed_at": app.ai_completed_at,
        "created_at": app.created_at,
        "submitted_at": app.submitted_at,
        "updated_at": app.updated_at,
        "form_data": fd,
        "hr_reviews": reviews,
    }


# ─────────────────────────── Routes ──────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(_emp_dep)):
    """Employee dashboard summary."""
    profile = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).first()
    apps = db.query(GratuityApplication).filter(
        GratuityApplication.user_id == user.id
    ).order_by(GratuityApplication.created_at.desc()).limit(5).all()

    recent = [{"id": a.id, "status": a.status, "ai_route": a.ai_route,
               "created_at": a.created_at, "submitted_at": a.submitted_at} for a in apps]

    total = db.query(GratuityApplication).filter(GratuityApplication.user_id == user.id).count()

    return {
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "organization_id": user.organization_id,
        "employee_profile": {
            "id": profile.id if profile else None,
            "employee_id_code": profile.employee_id_code if profile else None,
            "department": profile.department if profile else None,
            "designation": profile.designation if profile else None,
            "date_of_joining": profile.date_of_joining if profile else None,
            "employment_type": profile.employment_type if profile else None,
        } if profile else None,
        "total_applications": total,
        "recent_applications": recent,
    }


@router.get("/profile")
def get_profile(db: Session = Depends(get_db), user: User = Depends(_emp_dep)):
    profile = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).first()
    org_name = user.organization.name if user.organization else None
    return {
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "organization_id": user.organization_id,
        "organization_name": org_name,
        "profile": {
            "id": profile.id if profile else None,
            "employee_id_code": profile.employee_id_code if profile else None,
            "department": profile.department if profile else None,
            "designation": profile.designation if profile else None,
            "date_of_joining": profile.date_of_joining if profile else None,
            "employment_type": profile.employment_type if profile else None,
        } if profile else None,
        "created_at": user.created_at,
    }


@router.put("/profile")
def update_profile(
    req: ProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    if req.full_name:
        user.full_name = req.full_name.strip()
        user.updated_at = time.time()

    profile = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user.id).first()
    if not profile:
        profile = EmployeeProfile(user_id=user.id, organization_id=user.organization_id, created_at=time.time())
        db.add(profile)

    if req.employee_id_code is not None:
        profile.employee_id_code = req.employee_id_code
    if req.department is not None:
        profile.department = req.department
    if req.designation is not None:
        profile.designation = req.designation
    if req.date_of_joining is not None:
        profile.date_of_joining = req.date_of_joining
    if req.employment_type is not None:
        profile.employment_type = req.employment_type
    profile.updated_at = time.time()

    db.commit()
    audit(db, user.id, "update_profile", "User", user.id, {}, _client_ip(request))
    return {"detail": "Profile updated", "user_id": user.id}


@router.post("/applications", status_code=201)
def submit_application(
    form: GratuityFormData,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    """Submit a gratuity assessment. Calls the existing VERITAS AI engine asynchronously."""
    now = time.time()

    # Persist the application record
    app = GratuityApplication(
        user_id=user.id,
        employee_profile_id=(user.employee_profile.id if user.employee_profile else None),
        status=ApplicationStatus.AI_PROCESSING,
        form_data=form.model_dump_json(),
        created_at=now,
        updated_at=now,
        submitted_at=now,
    )
    db.add(app)
    db.flush()  # get app.id

    # Status history
    hist = ApplicationHistory(
        application_id=app.id,
        from_status=None,
        to_status=ApplicationStatus.AI_PROCESSING,
        changed_by_user_id=user.id,
        notes="Submitted by employee",
        created_at=now,
    )
    db.add(hist)

    # Call existing VERITAS AI engine
    veritas_payload = _build_veritas_request(form, user)
    run_id = None
    try:
        # Internal call to the existing API endpoint
        resp = httpx.post(
            f"{_self_url()}/v1/advisories",
            json=veritas_payload,
            timeout=15.0,
        )
        if resp.status_code in (200, 202):
            data = resp.json()
            run_id = data.get("run_id")
    except Exception as e:
        L.warning("failed to submit to VERITAS engine: %s", e)

    app.run_id = run_id
    db.commit()
    db.refresh(app)

    audit(db, user.id, "submit_application", "GratuityApplication", app.id,
          {"run_id": run_id}, _client_ip(request))
    L.info("application submitted app_id=%s run_id=%s user_id=%s", app.id, run_id, user.id)

    return {
        "application_id": app.id,
        "run_id": run_id,
        "status": app.status,
        "status_url": f"/v1/advisories/{run_id}" if run_id else None,
        "message": "Assessment submitted. Poll status_url for results.",
    }


@router.post("/applications/draft", status_code=201)
def save_draft(
    form: GratuityFormData,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    """Save a draft application without submitting it to the AI engine."""
    now = time.time()
    app = GratuityApplication(
        user_id=user.id,
        employee_profile_id=(user.employee_profile.id if user.employee_profile else None),
        status=ApplicationStatus.DRAFT,
        form_data=form.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return {"application_id": app.id, "status": app.status}


@router.put("/applications/{app_id}/sync-ai")
def sync_ai_result(
    app_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    """Fetch the latest AI result from Redis and cache it on the application record."""
    app = db.query(GratuityApplication).filter(
        GratuityApplication.id == app_id,
        GratuityApplication.user_id == user.id,
    ).first()
    if not app:
        raise HTTPException(404, "Application not found")
    if not app.run_id:
        return {"detail": "No AI run associated", "status": app.status}

    try:
        resp = httpx.get(f"{_self_url()}/v1/advisories/{app.run_id}", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            run_status = data.get("status")
            if run_status == "completed":
                app.ai_route = data.get("route")
                app.ai_summary = data.get("summary")
                app.ai_completed_at = data.get("finished_at")
                if app.status == ApplicationStatus.AI_PROCESSING:
                    _transition(db, app, ApplicationStatus.PENDING_HR_REVIEW, user.id,
                                f"AI assessment complete: {app.ai_route}")
                db.commit()
            return {"status": run_status, "route": data.get("route"), "application_status": app.status}
    except Exception as e:
        L.warning("sync_ai_result failed for app_id=%s: %s", app_id, e)
    return {"detail": "Could not reach AI engine", "status": app.status}


@router.get("/applications")
def list_applications(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    """List the authenticated employee's own applications only."""
    total = db.query(GratuityApplication).filter(GratuityApplication.user_id == user.id).count()
    apps = (
        db.query(GratuityApplication)
        .filter(GratuityApplication.user_id == user.id)
        .order_by(GratuityApplication.created_at.desc())
        .offset(skip).limit(min(limit, 50))
        .all()
    )
    return {"total": total, "items": [_serialize_app(a) for a in apps]}


@router.get("/applications/{app_id}")
def get_application(
    app_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    """Get a single application. Employees can only see their own."""
    app = db.query(GratuityApplication).filter(
        GratuityApplication.id == app_id,
        GratuityApplication.user_id == user.id,
    ).first()
    if not app:
        raise HTTPException(404, "Application not found")
    return _serialize_app(app)


@router.get("/applications/{app_id}/ai-result")
def get_ai_result(
    app_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_emp_dep),
):
    """Proxy through to the existing VERITAS AI result endpoint."""
    app = db.query(GratuityApplication).filter(
        GratuityApplication.id == app_id,
        GratuityApplication.user_id == user.id,
    ).first()
    if not app:
        raise HTTPException(404, "Application not found")
    if not app.run_id:
        return {"status": "no_run", "detail": "No AI assessment has been submitted for this application"}

    try:
        resp = httpx.get(f"{_self_url()}/v1/advisories/{app.run_id}", timeout=15.0)
        data = resp.json()
        # If completed, cache the result
        if data.get("status") == "completed" and app.status == ApplicationStatus.AI_PROCESSING:
            app.ai_route = data.get("route")
            app.ai_summary = data.get("summary")
            app.ai_completed_at = data.get("finished_at")
            _transition(db, app, ApplicationStatus.PENDING_HR_REVIEW, user.id,
                        f"AI complete: {app.ai_route}")
            db.commit()
        return data
    except Exception as e:
        raise HTTPException(502, f"Could not reach AI engine: {e}")


@router.post("/calculate-gratuity", response_model=GratuityCalcResponse)
def employee_calculate_gratuity(
    req: GratuityCalcRequest,
    user: User = Depends(_emp_dep),
):
    """Statutory Gratuity calculation for the authenticated employee."""
    return compute_gratuity(req)


def _self_url() -> str:
    """Return the base URL for calling our own API endpoints internally."""
    return "http://localhost:8000"
