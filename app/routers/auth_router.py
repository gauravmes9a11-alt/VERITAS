"""Authentication router: registration, login, current-user, and logout."""
import time
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth import audit, create_access_token, get_current_user, hash_password, verify_password
from ..db import get_db
from ..models import EmployeeProfile, Organization, User, UserRole

L = logging.getLogger("auth_router")
router = APIRouter(prefix="/auth", tags=["auth"])


# ─────────────────────────── Pydantic schemas ────────────────────────────────

class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    organization_name: Optional[str] = Field(None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int
    full_name: str
    email: str
    organization_id: Optional[int] = None


class MeResponse(BaseModel):
    user_id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    organization_id: Optional[int]
    organization_name: Optional[str]
    employee_profile_id: Optional[int]
    created_at: float


# ─────────────────────────── Helpers ─────────────────────────────────────────

def _get_or_create_org(db: Session, name: str) -> Organization:
    """Find or create an organization by name (case-insensitive)."""
    slug = name.lower().strip().replace(" ", "-")[:128]
    org = db.query(Organization).filter(Organization.slug == slug).first()
    if not org:
        org = Organization(name=name.strip(), slug=slug)
        db.add(org)
        db.flush()
    return org


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    return (forwarded.split(",")[0].strip() if forwarded
            else (request.client.host if request.client else "unknown"))


# ─────────────────────────── Routes ──────────────────────────────────────────

@router.post("/register", status_code=201, response_model=TokenResponse)
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Employee self-registration. HR accounts are created by an admin only."""
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Resolve / create organization
    org = None
    if req.organization_name:
        org = _get_or_create_org(db, req.organization_name)

    user = User(
        email=req.email.lower(),
        hashed_password=hash_password(req.password),
        full_name=req.full_name.strip(),
        role=UserRole.EMPLOYEE,
        organization_id=org.id if org else None,
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.add(user)
    db.flush()  # get user.id

    # Create a blank employee profile
    profile = EmployeeProfile(
        user_id=user.id,
        organization_id=org.id if org else None,
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.add(profile)
    db.commit()
    db.refresh(user)

    audit(db, user.id, "register", "User", user.id, {"email": user.email}, _client_ip(request))
    L.info("new employee registered user_id=%s email=%s", user.id, user.email)

    token = create_access_token(user.id, user.role, user.email)
    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        organization_id=user.organization_id,
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.email == req.email.lower(),
        User.is_active == True  # noqa: E712
    ).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    # Update last login
    user.last_login_at = time.time()
    db.commit()

    audit(db, user.id, "login", "User", user.id, {}, _client_ip(request))
    L.info("login user_id=%s role=%s", user.id, user.role)

    token = create_access_token(user.id, user.role, user.email)
    org_id = user.organization_id
    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        organization_id=org_id,
    )


@router.get("/me", response_model=MeResponse)
def me(db: Session = Depends(get_db),
       # inline dependency to avoid circular import at module load time
       credentials=Depends(__import__("fastapi.security", fromlist=["HTTPBearer"]).HTTPBearer(auto_error=True))):
    """Return the current user's profile."""
    from ..auth import decode_token
    payload = decode_token(credentials.credentials)
    user_id = int(payload.get("sub", 0))
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    org_name = user.organization.name if user.organization else None
    ep_id = user.employee_profile.id if user.employee_profile else None
    return MeResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        organization_id=user.organization_id,
        organization_name=org_name,
        employee_profile_id=ep_id,
        created_at=user.created_at,
    )


@router.post("/logout", status_code=200)
def logout():
    """JWT is stateless — client discards the token. Endpoint exists for audit completeness."""
    return {"detail": "Logged out. Please discard the token on the client side."}
