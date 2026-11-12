#!/usr/bin/env python3
"""Database initialization and seed script.

Run once to:
1. Create all PostgreSQL tables (idempotent — safe to re-run)
2. Seed a demo Organization, demo Employee account and demo HR Admin account

Usage (from the desk/ directory):
    python scripts/init_db.py

Or via Docker:
    docker compose run --rm db-init

Demo credentials (change in production):
    Employee: employee@demo.veritas / Demo@Employee1
    HR Admin: hr@demo.veritas / Demo@HRAdmin1
"""
import os
import sys
import time

# Allow running from the desk/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password
from app.db import SessionLocal, engine, init_db
from app.models import EmployeeProfile, Organization, User, UserRole

DEMO_ORG_NAME = os.environ.get("DEMO_ORG_NAME", "VERITAS Demo Organization")
DEMO_EMP_EMAIL = os.environ.get("DEMO_EMPLOYEE_EMAIL", "employee@demo.veritas")
DEMO_EMP_PASSWORD = os.environ.get("DEMO_EMPLOYEE_PASSWORD", "Demo@Employee1")
DEMO_EMP_NAME = os.environ.get("DEMO_EMPLOYEE_NAME", "Arjun Sharma")
DEMO_HR_EMAIL = os.environ.get("DEMO_HR_EMAIL", "hr@demo.veritas")
DEMO_HR_PASSWORD = os.environ.get("DEMO_HR_PASSWORD", "Demo@HRAdmin1")
DEMO_HR_NAME = os.environ.get("DEMO_HR_NAME", "Priya Menon")


def main():
    print("=" * 60)
    print("VERITAS — Database Initialization")
    print("=" * 60)

    # 1. Create tables
    print("\n[1/4] Creating database tables...")
    try:
        init_db()
        print("      ✓ Tables created / verified")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")
        sys.exit(1)

    db = SessionLocal()
    try:
        # 2. Create demo organization
        print("\n[2/4] Seeding demo organization...")
        slug = DEMO_ORG_NAME.lower().strip().replace(" ", "-")[:128]
        org = db.query(Organization).filter(Organization.slug == slug).first()
        if not org:
            org = Organization(
                name=DEMO_ORG_NAME,
                slug=slug,
                created_at=time.time(),
                updated_at=time.time(),
            )
            db.add(org)
            db.flush()
            print(f"      ✓ Created org: '{org.name}' (id={org.id})")
        else:
            print(f"      ✓ Org already exists: '{org.name}' (id={org.id})")

        # 3. Create demo employee
        print("\n[3/4] Seeding demo employee account...")
        emp_user = db.query(User).filter(User.email == DEMO_EMP_EMAIL.lower()).first()
        if not emp_user:
            now = time.time()
            emp_user = User(
                email=DEMO_EMP_EMAIL.lower(),
                hashed_password=hash_password(DEMO_EMP_PASSWORD),
                full_name=DEMO_EMP_NAME,
                role=UserRole.EMPLOYEE,
                organization_id=org.id,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(emp_user)
            db.flush()
            emp_profile = EmployeeProfile(
                user_id=emp_user.id,
                organization_id=org.id,
                employee_id_code="EMP-001",
                department="Engineering",
                designation="Senior Software Engineer",
                date_of_joining="2020-06-15",
                employment_type="permanent",
                created_at=now,
                updated_at=now,
            )
            db.add(emp_profile)
            print(f"      ✓ Created employee: {DEMO_EMP_EMAIL}")
        else:
            print(f"      ✓ Employee already exists: {DEMO_EMP_EMAIL}")

        # 4. Create demo HR admin
        print("\n[4/4] Seeding demo HR admin account...")
        hr_user = db.query(User).filter(User.email == DEMO_HR_EMAIL.lower()).first()
        if not hr_user:
            now = time.time()
            hr_user = User(
                email=DEMO_HR_EMAIL.lower(),
                hashed_password=hash_password(DEMO_HR_PASSWORD),
                full_name=DEMO_HR_NAME,
                role=UserRole.HR_ADMIN,
                organization_id=org.id,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(hr_user)
            print(f"      ✓ Created HR admin: {DEMO_HR_EMAIL}")
        else:
            print(f"      ✓ HR admin already exists: {DEMO_HR_EMAIL}")

        db.commit()

    except Exception as e:
        db.rollback()
        print(f"\n✗ Seed failed: {e}")
        raise
    finally:
        db.close()

    print("\n" + "=" * 60)
    print("✓ Initialization complete!")
    print("")
    print("Demo accounts:")
    print(f"  Employee  → {DEMO_EMP_EMAIL}  /  {DEMO_EMP_PASSWORD}")
    print(f"  HR Admin  → {DEMO_HR_EMAIL}   /  {DEMO_HR_PASSWORD}")
    print("")
    print("⚠  Change these credentials before any public deployment.")
    print("=" * 60)


if __name__ == "__main__":
    main()
