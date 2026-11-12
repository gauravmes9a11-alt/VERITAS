"""Authentication utilities: password hashing, JWT creation/verification,
and FastAPI dependencies for route protection.

Security design:
- Passwords: bcrypt via passlib (work factor ≥12 is the default)
- Tokens: HS256 JWT, signed with JWT_SECRET_KEY, expire in JWT_EXPIRE_MINUTES
- Role enforcement: require_role(*roles) dependency raises 403 if caller's
  role is not in the permitted set — enforced at the router level
- No session state: all auth is stateless JWT (client discards on logout)
"""
import json
import logging
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import AuditLog, User, UserRole

L = logging.getLogger("auth")

# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
_bearer = HTTPBearer(auto_error=True)


def create_access_token(user_id: int, role: str, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --------------------------------------------------------------------------
# FastAPI dependencies
# --------------------------------------------------------------------------

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Decode JWT and load the User from the database."""
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Malformed token")
    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    return user


def require_role(*roles: UserRole):
    """Dependency factory that restricts a route to specific roles."""
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in [r.value for r in roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role(s): {[r.value for r in roles]}",
            )
        return current_user
    return _check


def optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None for unauthenticated requests."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id:
            return db.query(User).filter(User.id == int(user_id), User.is_active == True).first()  # noqa: E712
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Audit helper
# --------------------------------------------------------------------------

def audit(db: Session, user_id: Optional[int], action: str,
          target_type: Optional[str] = None, target_id: Optional[int] = None,
          details: Optional[dict] = None, ip: Optional[str] = None):
    """Write a single audit log row. Fire-and-forget; errors are logged, not raised."""
    try:
        log = AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=json.dumps(details, default=str) if details else None,
            ip_address=ip,
            created_at=time.time(),
        )
        db.add(log)
        db.commit()
    except Exception as exc:
        L.warning("audit log failed: %s", exc)
