"""ORM models for the VERITAS dual-portal extension.

Tables are additive — they do not touch or alter any existing Redis-based
data structures. Foreign key relationships are explicit to maintain
referential integrity. All timestamps are UTC epoch floats for consistency
with the existing Redis run objects.
"""
import time
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, Boolean, Column, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


# ─────────────────────────── Enumerations ────────────────────────────────────

class UserRole(str, PyEnum):
    EMPLOYEE = "EMPLOYEE"
    HR_ADMIN = "HR_ADMIN"


class ApplicationStatus(str, PyEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    AI_PROCESSING = "AI_PROCESSING"
    PENDING_HR_REVIEW = "PENDING_HR_REVIEW"
    MORE_INFO_REQUIRED = "MORE_INFO_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED_LEGAL = "ESCALATED_LEGAL"
    COMPLETED = "COMPLETED"


class HRDecision(str, PyEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MORE_INFO_REQUIRED = "MORE_INFO_REQUIRED"
    ESCALATED_LEGAL = "ESCALATED_LEGAL"
    UNDER_REVIEW = "UNDER_REVIEW"


# ─────────────────────────── Models ──────────────────────────────────────────

class Organization(Base):
    """Represents a company / employer organization. HR admins belong to one org."""
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(128), unique=True, nullable=False, index=True)
    created_at = Column(Float, default=time.time, nullable=False)
    updated_at = Column(Float, default=time.time, onupdate=time.time, nullable=False)

    users = relationship("User", back_populates="organization")
    employee_profiles = relationship("EmployeeProfile", back_populates="organization")

    def __repr__(self):
        return f"<Organization id={self.id} name={self.name!r}>"


class User(Base):
    """Authentication entity. One user ↔ one role ↔ optionally one EmployeeProfile."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(254), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(16), nullable=False, default=UserRole.EMPLOYEE)
    is_active = Column(Boolean, default=True, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    created_at = Column(Float, default=time.time, nullable=False)
    updated_at = Column(Float, default=time.time, onupdate=time.time, nullable=False)
    last_login_at = Column(Float, nullable=True)

    organization = relationship("Organization", back_populates="users")
    employee_profile = relationship("EmployeeProfile", back_populates="user", uselist=False)
    hr_reviews = relationship("HRReview", back_populates="reviewer")
    audit_logs = relationship("AuditLog", back_populates="user")

    __table_args__ = (
        Index("ix_users_role", "role"),
    )

    def __repr__(self):
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


class EmployeeProfile(Base):
    """Extended profile for employees — links a User to their employment details."""
    __tablename__ = "employee_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    employee_id_code = Column(String(64), nullable=True)  # company-assigned employee ID
    department = Column(String(128), nullable=True)
    designation = Column(String(128), nullable=True)
    date_of_joining = Column(String(10), nullable=True)   # ISO date string: YYYY-MM-DD
    employment_type = Column(String(32), nullable=True)   # permanent / fixed_term / contractual / other
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(Float, default=time.time, nullable=False)
    updated_at = Column(Float, default=time.time, onupdate=time.time, nullable=False)

    user = relationship("User", back_populates="employee_profile")
    organization = relationship("Organization", back_populates="employee_profiles")
    applications = relationship("GratuityApplication", back_populates="employee_profile")

    __table_args__ = (
        Index("ix_emp_profile_org", "organization_id"),
        UniqueConstraint("organization_id", "employee_id_code", name="uq_org_emp_code"),
    )

    def __repr__(self):
        return f"<EmployeeProfile id={self.id} user_id={self.user_id} code={self.employee_id_code!r}>"


class GratuityApplication(Base):
    """A gratuity eligibility assessment submitted by an employee or initiated by HR.

    `run_id` links to the Redis run object produced by the existing VERITAS AI engine.
    The application status tracks both AI processing and HR review lifecycle.
    """
    __tablename__ = "gratuity_applications"

    id = Column(Integer, primary_key=True, index=True)
    # Who owns this application
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    employee_profile_id = Column(Integer, ForeignKey("employee_profiles.id"), nullable=True, index=True)
    # HR who initiated (if applicable)
    initiated_by_hr_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Existing VERITAS AI engine run ID (Redis key: run:{run_id})
    run_id = Column(String(64), nullable=True, index=True)
    # Application lifecycle
    status = Column(String(32), nullable=False, default=ApplicationStatus.DRAFT)
    # Form data as JSON string (employee-submitted details)
    form_data = Column(Text, nullable=False, default="{}")
    # AI result summary (cached from Redis for quick display)
    ai_route = Column(String(64), nullable=True)       # answer-with-citation / legal-counsel-review / etc.
    ai_summary = Column(Text, nullable=True)
    ai_completed_at = Column(Float, nullable=True)
    # Timestamps
    created_at = Column(Float, default=time.time, nullable=False)
    updated_at = Column(Float, default=time.time, onupdate=time.time, nullable=False)
    submitted_at = Column(Float, nullable=True)

    employee_profile = relationship("EmployeeProfile", back_populates="applications")
    hr_reviews = relationship("HRReview", back_populates="application", order_by="HRReview.created_at")
    history = relationship("ApplicationHistory", back_populates="application", order_by="ApplicationHistory.created_at")

    __table_args__ = (
        Index("ix_app_status", "status"),
        Index("ix_app_user_id", "user_id"),
    )

    def __repr__(self):
        return f"<GratuityApplication id={self.id} status={self.status} run_id={self.run_id!r}>"


class HRReview(Base):
    """A review/decision recorded by an HR admin on a gratuity application."""
    __tablename__ = "hr_reviews"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("gratuity_applications.id"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    decision = Column(String(32), nullable=False)         # HRDecision enum value
    comments = Column(Text, nullable=False)
    created_at = Column(Float, default=time.time, nullable=False)

    application = relationship("GratuityApplication", back_populates="hr_reviews")
    reviewer = relationship("User", back_populates="hr_reviews")

    def __repr__(self):
        return f"<HRReview id={self.id} application_id={self.application_id} decision={self.decision}>"


class ApplicationHistory(Base):
    """Audit trail of every status transition for a GratuityApplication."""
    __tablename__ = "application_history"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("gratuity_applications.id"), nullable=False, index=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    changed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(Float, default=time.time, nullable=False)

    application = relationship("GratuityApplication", back_populates="history")

    def __repr__(self):
        return f"<ApplicationHistory id={self.id} app={self.application_id} {self.from_status}→{self.to_status}>"


class AuditLog(Base):
    """Security audit log for all sensitive HR actions."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(128), nullable=False)
    target_type = Column(String(64), nullable=True)
    target_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)   # JSON string
    ip_address = Column(String(45), nullable=True)
    created_at = Column(Float, default=time.time, nullable=False)

    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_user_action", "user_id", "action"),
        Index("ix_audit_created", "created_at"),
    )

    def __repr__(self):
        return f"<AuditLog id={self.id} action={self.action!r} user={self.user_id}>"
