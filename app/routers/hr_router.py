"""HR Admin portal router.

All routes require HR_ADMIN role. Organization-scoped access is enforced on
every query — an HR user can only see employees and applications that belong
to their organization. HR decisions are recorded with mandatory explanations
and a full audit trail. AI assessments are never auto-approved.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import audit, require_role
from ..config import settings
from ..db import get_db
from ..models import (
    ApplicationHistory, ApplicationStatus, EmployeeProfile,
    GratuityApplication, HRDecision, HRReview, Organization,
    User, UserRole,
)

L = logging.getLogger("hr_router")
router = APIRouter(prefix="/hr", tags=["hr-portal"])

_hr_dep = require_role(UserRole.HR_ADMIN)


# ─────────────────────────── Pydantic schemas ────────────────────────────────

class EmployeeCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: str = Field(..., max_length=254)
    employee_id_code: Optional[str] = Field(None, max_length=64)
    department: Optional[str] = Field(None, max_length=128)
    designation: Optional[str] = Field(None, max_length=128)
    date_of_joining: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    employment_type: Optional[str] = Field(None, max_length=32)
    temp_password: str = Field(..., min_length=8, max_length=128)


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = Field(None, max_length=255)
    employee_id_code: Optional[str] = Field(None, max_length=64)
    department: Optional[str] = Field(None, max_length=128)
    designation: Optional[str] = Field(None, max_length=128)
    date_of_joining: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    employment_type: Optional[str] = Field(None, max_length=32)
    is_active: Optional[bool] = None


class HRSubmitAssessment(BaseModel):
    """HR initiates an AI assessment for an existing application."""
    application_id: int
    notes: Optional[str] = Field(None, max_length=500)


class HRReviewRequest(BaseModel):
    decision: str = Field(..., pattern=r"^(APPROVED|REJECTED|MORE_INFO_REQUIRED|ESCALATED_LEGAL|UNDER_REVIEW)$")
    comments: str = Field(..., min_length=10, max_length=2000,
                          description="Mandatory explanation for every HR decision")


class HRInitiateAssessment(BaseModel):
    employee_profile_id: int
    date_of_joining: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    last_working_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    employment_type: str = Field("permanent", max_length=32)
    separation_reason: str = Field("resignation", max_length=32)
    basic_salary: float = Field(..., ge=0)
    dearness_allowance: float = Field(0.0, ge=0)
    salary_type: str = Field("monthly", pattern=r"^(monthly|daily)$")
    employer_name: Optional[str] = Field(None, max_length=255)
    establishment_type: Optional[str] = Field(None, max_length=64)
    employee_count: Optional[int] = Field(None, ge=1)
    additional_notes: Optional[str] = Field(None, max_length=1000)


# ─────────────────────────── Helpers ─────────────────────────────────────────

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def _assert_org(hr_user: User):
    if not hr_user.organization_id:
        raise HTTPException(403, "HR account has no organization assigned. Contact the system administrator.")


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


def _serialize_employee(user: User, profile: Optional[EmployeeProfile]) -> dict:
    from datetime import date, datetime
    doj = profile.date_of_joining if profile else None
    service_years = None
    if doj:
        try:
            d = date.fromisoformat(doj)
            service_years = round((date.today() - d).days / 365.25, 2)
        except Exception:
            pass
    return {
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "is_active": user.is_active,
        "profile_id": profile.id if profile else None,
        "employee_id_code": profile.employee_id_code if profile else None,
        "department": profile.department if profile else None,
        "designation": profile.designation if profile else None,
        "date_of_joining": doj,
        "employment_type": profile.employment_type if profile else None,
        "service_years": service_years,
        "created_at": user.created_at,
    }


def _self_url() -> str:
    return "http://localhost:8000"


def _build_assessment_payload(req: HRInitiateAssessment, hr_user: User, profile: EmployeeProfile) -> dict:
    from datetime import date
    doj = date.fromisoformat(req.date_of_joining)
    lwd = date.fromisoformat(req.last_working_date) if req.last_working_date else date.today()
    years = max((lwd - doj).days / 365.25, 0)
    wage = req.basic_salary + req.dearness_allowance
    emp_type_map = {"permanent": "permanent", "fixed_term": "fixed_term",
                    "contractual": "contract_labour", "other": "permanent"}
    sep_map = {"resignation": "resignation", "retirement": "retirement",
               "superannuation": "superannuation", "termination": "termination",
               "contract_expiry": "contract_expiry", "death": "death",
               "disablement": "disablement", "other": "resignation"}
    question = (
        f"An employee (ID: {profile.employee_id_code or 'unknown'}) "
        f"on a {req.employment_type.replace('_','-')} contract "
        f"joined on {req.date_of_joining} and is leaving on {lwd.isoformat()} "
        f"due to {req.separation_reason.replace('_',' ')} after {round(years, 2)} years. "
        f"Last drawn eligible wages (basic + DA): INR {wage:,.0f} ({req.salary_type}-rated). "
        f"Employer: {req.employer_name or 'not specified'}. "
        "Is gratuity payable and what is the applicable calculation?"
    )
    employment = {
        "employment_type": emp_type_map.get(req.employment_type, "permanent"),
        "start_date": req.date_of_joining,
        "end_date": lwd.isoformat(),
        "years_of_service": round(years, 2),
        "separation_reason": sep_map.get(req.separation_reason, "resignation"),
    }
    establishment = {}
    if req.employee_count:
        establishment["employee_count"] = req.employee_count
    payload = {
        "client_id": f"hr-{hr_user.id}-emp-{profile.id}",
        "question": question[:2000],
        "employment": employment,
        "as_of_date": lwd.isoformat(),
    }
    if establishment:
        payload["establishment"] = establishment
    if req.additional_notes:
        payload["notes"] = req.additional_notes[:500]
    return payload


# ─────────────────────────── Routes — Dashboard ──────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), hr: User = Depends(_hr_dep)):
    _assert_org(hr)
    org_id = hr.organization_id

    total_employees = db.query(User).filter(
        User.organization_id == org_id,
        User.role == UserRole.EMPLOYEE,
        User.is_active == True,  # noqa: E712
    ).count()

    # Applications via employee_profiles in this org
    org_profile_ids = [
        p.id for p in db.query(EmployeeProfile.id)
        .filter(EmployeeProfile.organization_id == org_id).all()
    ]
    total_apps = db.query(GratuityApplication).filter(
        GratuityApplication.employee_profile_id.in_(org_profile_ids)
    ).count()

    pending_review = db.query(GratuityApplication).filter(
        GratuityApplication.employee_profile_id.in_(org_profile_ids),
        GratuityApplication.status == ApplicationStatus.PENDING_HR_REVIEW,
    ).count()

    potentially_eligible = db.query(GratuityApplication).filter(
        GratuityApplication.employee_profile_id.in_(org_profile_ids),
        GratuityApplication.ai_route == "answer-with-citation",
    ).count()

    legal_review = db.query(GratuityApplication).filter(
        GratuityApplication.employee_profile_id.in_(org_profile_ids),
        GratuityApplication.ai_route == "legal-counsel-review",
    ).count()

    return {
        "organization_id": org_id,
        "organization_name": hr.organization.name if hr.organization else None,
        "total_employees": total_employees,
        "total_assessments": total_apps,
        "pending_review": pending_review,
        "potentially_eligible": potentially_eligible,
        "cases_requiring_legal_review": legal_review,
    }


# ─────────────────────────── Routes — Employee Management ────────────────────

@router.get("/employees")
def list_employees(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    _assert_org(hr)
    q = db.query(User).filter(
        User.organization_id == hr.organization_id,
        User.role == UserRole.EMPLOYEE,
    )
    if search:
        like = f"%{search}%"
        q = q.filter(
            (User.full_name.ilike(like)) | (User.email.ilike(like))
        )
    total = q.count()
    users = q.order_by(User.created_at.desc()).offset(skip).limit(min(limit, 100)).all()
    items = [_serialize_employee(u, u.employee_profile) for u in users]
    return {"total": total, "items": items}


@router.get("/employees/{user_id}")
def get_employee(user_id: int, db: Session = Depends(get_db), hr: User = Depends(_hr_dep)):
    _assert_org(hr)
    user = db.query(User).filter(
        User.id == user_id,
        User.organization_id == hr.organization_id,
        User.role == UserRole.EMPLOYEE,
    ).first()
    if not user:
        raise HTTPException(404, "Employee not found in your organization")
    profile = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user_id).first()
    apps = db.query(GratuityApplication).filter(GratuityApplication.user_id == user_id).all()
    return {
        **_serialize_employee(user, profile),
        "applications": [{"id": a.id, "status": a.status, "ai_route": a.ai_route,
                          "created_at": a.created_at} for a in apps],
    }


@router.post("/employees", status_code=201)
def create_employee(
    req: EmployeeCreate,
    request: Request,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    _assert_org(hr)
    from ..auth import hash_password
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(409, "Email already registered")
    now = time.time()
    user = User(
        email=req.email.lower(),
        hashed_password=hash_password(req.temp_password),
        full_name=req.full_name.strip(),
        role=UserRole.EMPLOYEE,
        organization_id=hr.organization_id,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    profile = EmployeeProfile(
        user_id=user.id,
        organization_id=hr.organization_id,
        employee_id_code=req.employee_id_code,
        department=req.department,
        designation=req.designation,
        date_of_joining=req.date_of_joining,
        employment_type=req.employment_type,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    db.commit()
    audit(db, hr.id, "create_employee", "User", user.id,
          {"email": user.email, "created_by_hr": hr.id}, _client_ip(request))
    return {"user_id": user.id, "profile_id": profile.id, "detail": "Employee created"}


@router.put("/employees/{user_id}")
def update_employee(
    user_id: int,
    req: EmployeeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    _assert_org(hr)
    user = db.query(User).filter(
        User.id == user_id,
        User.organization_id == hr.organization_id,
        User.role == UserRole.EMPLOYEE,
    ).first()
    if not user:
        raise HTTPException(404, "Employee not found in your organization")

    if req.full_name is not None:
        user.full_name = req.full_name.strip()
    if req.is_active is not None:
        user.is_active = req.is_active
    user.updated_at = time.time()

    profile = db.query(EmployeeProfile).filter(EmployeeProfile.user_id == user_id).first()
    if not profile:
        profile = EmployeeProfile(user_id=user_id, organization_id=hr.organization_id, created_at=time.time())
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
    audit(db, hr.id, "update_employee", "User", user_id, {}, _client_ip(request))
    return {"detail": "Employee updated", "user_id": user_id}


# ─────────────────────────── Routes — Applications ───────────────────────────

def _org_app_query(db: Session, org_id: int):
    org_profile_ids = [
        p.id for p in db.query(EmployeeProfile.id)
        .filter(EmployeeProfile.organization_id == org_id).all()
    ]
    return db.query(GratuityApplication).filter(
        GratuityApplication.employee_profile_id.in_(org_profile_ids)
    ), org_profile_ids


@router.get("/applications")
def list_applications(
    status_filter: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    _assert_org(hr)
    q, _ = _org_app_query(db, hr.organization_id)
    if status_filter:
        q = q.filter(GratuityApplication.status == status_filter)
    total = q.count()
    apps = q.order_by(GratuityApplication.created_at.desc()).offset(skip).limit(min(limit, 100)).all()

    items = []
    for a in apps:
        owner = db.query(User).filter(User.id == a.user_id).first()
        profile = db.query(EmployeeProfile).filter(EmployeeProfile.id == a.employee_profile_id).first()
        try:
            fd = json.loads(a.form_data) if a.form_data else {}
        except Exception:
            fd = {}
        items.append({
            "id": a.id,
            "employee_name": owner.full_name if owner else "Unknown",
            "employee_email": owner.email if owner else None,
            "employee_id_code": profile.employee_id_code if profile else None,
            "date_of_joining": profile.date_of_joining if profile else None,
            "employment_type": fd.get("employment_type"),
            "last_working_date": fd.get("last_working_date"),
            "status": a.status,
            "ai_route": a.ai_route,
            "ai_summary": a.ai_summary,
            "created_at": a.created_at,
            "submitted_at": a.submitted_at,
            "run_id": a.run_id,
        })
    return {"total": total, "items": items}


@router.get("/applications/{app_id}")
def get_application(app_id: int, db: Session = Depends(get_db), hr: User = Depends(_hr_dep)):
    _assert_org(hr)
    _, org_profile_ids = _org_app_query(db, hr.organization_id)
    app = db.query(GratuityApplication).filter(
        GratuityApplication.id == app_id,
        GratuityApplication.employee_profile_id.in_(org_profile_ids),
    ).first()
    if not app:
        raise HTTPException(404, "Application not found in your organization")

    owner = db.query(User).filter(User.id == app.user_id).first()
    profile = db.query(EmployeeProfile).filter(EmployeeProfile.id == app.employee_profile_id).first()
    try:
        fd = json.loads(app.form_data) if app.form_data else {}
    except Exception:
        fd = {}

    reviews = [{"id": r.id, "decision": r.decision, "comments": r.comments,
                "reviewer_id": r.reviewer_id, "created_at": r.created_at}
               for r in (app.hr_reviews or [])]
    history = [{"from_status": h.from_status, "to_status": h.to_status,
                "changed_by_user_id": h.changed_by_user_id,
                "notes": h.notes, "created_at": h.created_at}
               for h in (app.history or [])]

    return {
        "id": app.id,
        "status": app.status,
        "run_id": app.run_id,
        "ai_route": app.ai_route,
        "ai_summary": app.ai_summary,
        "ai_completed_at": app.ai_completed_at,
        "created_at": app.created_at,
        "submitted_at": app.submitted_at,
        "form_data": fd,
        "employee": _serialize_employee(owner, profile) if owner else None,
        "hr_reviews": reviews,
        "history": history,
    }


@router.post("/applications/{app_id}/ai-result")
def hr_get_ai_result(
    app_id: int,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    """Fetch and return the full AI result from the VERITAS engine for this application."""
    _assert_org(hr)
    _, org_profile_ids = _org_app_query(db, hr.organization_id)
    app = db.query(GratuityApplication).filter(
        GratuityApplication.id == app_id,
        GratuityApplication.employee_profile_id.in_(org_profile_ids),
    ).first()
    if not app:
        raise HTTPException(404, "Application not found")
    if not app.run_id:
        return {"status": "no_run", "detail": "No AI run associated with this application"}
    try:
        resp = httpx.get(f"{_self_url()}/v1/advisories/{app.run_id}", timeout=15.0)
        data = resp.json()
        if data.get("status") == "completed" and not app.ai_route:
            app.ai_route = data.get("route")
            app.ai_summary = data.get("summary")
            app.ai_completed_at = data.get("finished_at")
            if app.status == ApplicationStatus.AI_PROCESSING:
                _transition(db, app, ApplicationStatus.PENDING_HR_REVIEW, hr.id, "AI complete (synced by HR)")
            db.commit()
        return data
    except Exception as e:
        raise HTTPException(502, f"Could not reach AI engine: {e}")


@router.post("/applications/initiate", status_code=201)
def hr_initiate_assessment(
    req: HRInitiateAssessment,
    request: Request,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    """HR initiates a fresh AI assessment for an employee."""
    _assert_org(hr)
    profile = db.query(EmployeeProfile).filter(
        EmployeeProfile.id == req.employee_profile_id,
        EmployeeProfile.organization_id == hr.organization_id,
    ).first()
    if not profile:
        raise HTTPException(404, "Employee profile not found in your organization")

    payload = _build_assessment_payload(req, hr, profile)
    now = time.time()
    form_data = req.model_dump_json()

    app = GratuityApplication(
        user_id=profile.user_id,
        employee_profile_id=profile.id,
        initiated_by_hr_id=hr.id,
        status=ApplicationStatus.AI_PROCESSING,
        form_data=form_data,
        created_at=now,
        updated_at=now,
        submitted_at=now,
    )
    db.add(app)
    db.flush()

    _transition(db, app, ApplicationStatus.AI_PROCESSING, hr.id, "Initiated by HR")

    run_id = None
    try:
        resp = httpx.post(f"{_self_url()}/v1/advisories", json=payload, timeout=15.0)
        if resp.status_code in (200, 202):
            run_id = resp.json().get("run_id")
    except Exception as e:
        L.warning("hr initiate_assessment AI call failed: %s", e)

    app.run_id = run_id
    db.commit()

    audit(db, hr.id, "hr_initiate_assessment", "GratuityApplication", app.id,
          {"run_id": run_id, "employee_profile_id": profile.id}, _client_ip(request))

    return {
        "application_id": app.id,
        "run_id": run_id,
        "status": app.status,
        "status_url": f"/v1/advisories/{run_id}" if run_id else None,
    }


@router.post("/applications/{app_id}/review")
def record_review(
    app_id: int,
    req: HRReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    hr: User = Depends(_hr_dep),
):
    """Record an HR decision. Mandatory comments prevent rubber-stamping AI results."""
    _assert_org(hr)
    _, org_profile_ids = _org_app_query(db, hr.organization_id)
    app = db.query(GratuityApplication).filter(
        GratuityApplication.id == app_id,
        GratuityApplication.employee_profile_id.in_(org_profile_ids),
    ).first()
    if not app:
        raise HTTPException(404, "Application not found in your organization")

    # Map decision to application status
    status_map = {
        HRDecision.APPROVED: ApplicationStatus.APPROVED,
        HRDecision.REJECTED: ApplicationStatus.REJECTED,
        HRDecision.MORE_INFO_REQUIRED: ApplicationStatus.MORE_INFO_REQUIRED,
        HRDecision.ESCALATED_LEGAL: ApplicationStatus.ESCALATED_LEGAL,
        HRDecision.UNDER_REVIEW: ApplicationStatus.PENDING_HR_REVIEW,
    }
    new_status = status_map.get(req.decision, ApplicationStatus.PENDING_HR_REVIEW)

    review = HRReview(
        application_id=app.id,
        reviewer_id=hr.id,
        decision=req.decision,
        comments=req.comments,
        created_at=time.time(),
    )
    db.add(review)
    _transition(db, app, new_status, hr.id, f"HR decision: {req.decision} — {req.comments[:80]}")
    db.commit()

    audit(db, hr.id, "hr_review", "GratuityApplication", app_id,
          {"decision": req.decision, "new_status": new_status}, _client_ip(request))
    L.info("hr_review app_id=%s decision=%s hr=%s", app_id, req.decision, hr.id)

    return {
        "application_id": app_id,
        "new_status": new_status,
        "review_id": review.id,
        "detail": "Review recorded",
    }


# ─────────────────────────── Routes — Reports ────────────────────────────────

@router.get("/reports/summary")
def reports_summary(db: Session = Depends(get_db), hr: User = Depends(_hr_dep)):
    _assert_org(hr)
    q, _ = _org_app_query(db, hr.organization_id)

    statuses = {}
    for row in q.with_entities(GratuityApplication.status, func.count()).group_by(GratuityApplication.status).all():
        statuses[row[0]] = row[1]

    routes = {}
    for row in q.with_entities(GratuityApplication.ai_route, func.count()).group_by(GratuityApplication.ai_route).all():
        if row[0]:
            routes[row[0]] = row[1]

    total = q.count()
    return {
        "total_applications": total,
        "by_status": statuses,
        "by_ai_route": routes,
        "organization_id": hr.organization_id,
        "organization_name": hr.organization.name if hr.organization else None,
    }


def _self_url() -> str:
    return "http://localhost:8000"
