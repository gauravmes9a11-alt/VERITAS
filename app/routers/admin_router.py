"""Admin router — HR account creation and system administration.

HR accounts cannot self-register. They must be created by an existing
HR_ADMIN user, or by the seed script (scripts/init_db.py) on first run.
This route requires HR_ADMIN role, ensuring only existing HR users can
onboard new HR users.
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth import audit, hash_password, require_role
from ..db import get_db
from ..models import Organization, User, UserRole

L = logging.getLogger("admin_router")
router = APIRouter(prefix="/admin", tags=["admin"])

_hr_dep = require_role(UserRole.HR_ADMIN)


class CreateHRRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    organization_id: Optional[int] = None  # if None, inherits the creator's org


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


@router.post("/create-hr", status_code=201)
def create_hr_account(
    req: CreateHRRequest,
    request: Request,
    db: Session = Depends(get_db),
    creator: User = Depends(_hr_dep),
):
    """Create a new HR_ADMIN account. Only callable by an existing HR admin.
    The new account inherits the creator's organization unless overridden.
    """
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(409, "Email already registered")

    org_id = req.organization_id or creator.organization_id
    if org_id:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            raise HTTPException(404, f"Organization {org_id} not found")

    now = time.time()
    user = User(
        email=req.email.lower(),
        hashed_password=hash_password(req.password),
        full_name=req.full_name.strip(),
        role=UserRole.HR_ADMIN,
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    audit(db, creator.id, "create_hr_account", "User", user.id,
          {"email": user.email, "created_by": creator.id, "org_id": org_id}, _client_ip(request))
    L.info("HR account created user_id=%s by hr=%s", user.id, creator.id)

    return {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "organization_id": user.organization_id,
        "detail": "HR account created. Share the credentials with the new HR user securely.",
    }


@router.post("/create-organization", status_code=201)
def create_organization(
    req: CreateOrgRequest,
    request: Request,
    db: Session = Depends(get_db),
    creator: User = Depends(_hr_dep),
):
    """Create a new organization. HR admins can create organizations."""
    slug = req.name.lower().strip().replace(" ", "-")[:128]
    existing = db.query(Organization).filter(Organization.slug == slug).first()
    if existing:
        raise HTTPException(409, f"Organization with slug '{slug}' already exists")
    org = Organization(name=req.name.strip(), slug=slug, created_at=time.time(), updated_at=time.time())
    db.add(org)
    db.commit()
    db.refresh(org)
    audit(db, creator.id, "create_organization", "Organization", org.id, {"name": org.name}, _client_ip(request))
    return {"organization_id": org.id, "name": org.name, "slug": org.slug}


@router.get("/organizations")
def list_organizations(db: Session = Depends(get_db), hr: User = Depends(_hr_dep)):
    orgs = db.query(Organization).order_by(Organization.name).all()
    return [{"id": o.id, "name": o.name, "slug": o.slug} for o in orgs]
