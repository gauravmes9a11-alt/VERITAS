"""Tests for the HR portal: employee management, application review,
organization-scoped access, audit trail, and role enforcement.

Uses SQLite in-memory database via FastAPI dependency override.
"""
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.models import Organization, User, UserRole, EmployeeProfile
from app.auth import hash_password

TEST_DB_URL = "sqlite:///./test_hr.db"


@pytest.fixture(scope="module")
def engine():
    e = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=e)
    yield e
    Base.metadata.drop_all(bind=e)
    import os
    try:
        os.remove("test_hr.db")
    except FileNotFoundError:
        pass


@pytest.fixture(scope="module")
def SessionFactory(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def client(SessionFactory):
    from app.api import app

    def _db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seed(SessionFactory):
    """Seed: 1 org, 1 HR admin, 1 employee with profile."""
    db = SessionFactory()
    now = time.time()
    org = Organization(name="HR Test Corp", slug="hr-test-corp", created_at=now, updated_at=now)
    db.add(org)
    db.flush()
    hr = User(
        email="hr@hrtest.com",
        hashed_password=hash_password("HRTest@123"),
        full_name="HR Manager",
        role=UserRole.HR_ADMIN,
        organization_id=org.id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(hr)
    emp_user = User(
        email="emp@hrtest.com",
        hashed_password=hash_password("EmpTest@123"),
        full_name="Test Employee",
        role=UserRole.EMPLOYEE,
        organization_id=org.id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(emp_user)
    db.flush()
    profile = EmployeeProfile(
        user_id=emp_user.id,
        organization_id=org.id,
        employee_id_code="HR-EMP-001",
        department="Finance",
        designation="Analyst",
        date_of_joining="2018-04-01",
        employment_type="permanent",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    db.commit()
    ids = {"org_id": org.id, "hr_id": hr.id, "emp_id": emp_user.id, "profile_id": profile.id}
    db.close()
    return ids


@pytest.fixture(scope="module")
def hr_token(client, seed):
    resp = client.post("/auth/login", json={"email": "hr@hrtest.com", "password": "HRTest@123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def emp_token(client, seed):
    resp = client.post("/auth/login", json={"email": "emp@hrtest.com", "password": "EmpTest@123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ─────────────────────────── HR Dashboard ────────────────────────────────────

def test_hr_dashboard_returns_stats(client, hr_token):
    resp = client.get("/hr/dashboard", headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_employees" in data
    assert "total_assessments" in data
    assert "pending_review" in data


# ─────────────────────────── Employee management ─────────────────────────────

def test_hr_can_list_org_employees(client, hr_token):
    resp = client.get("/hr/employees", headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    emails = [e["email"] for e in data["items"]]
    assert "emp@hrtest.com" in emails


def test_hr_can_get_employee_detail(client, hr_token, seed):
    resp = client.get(f"/hr/employees/{seed['emp_id']}",
                      headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == seed["emp_id"]
    assert data["employee_id_code"] == "HR-EMP-001"


def test_hr_can_create_new_employee(client, hr_token):
    resp = client.post("/hr/employees", json={
        "full_name": "New Employee",
        "email": "new_emp@hrtest.com",
        "employee_id_code": "HR-EMP-002",
        "department": "HR",
        "designation": "HR Associate",
        "date_of_joining": "2023-01-10",
        "employment_type": "permanent",
        "temp_password": "TempPass@123",
    }, headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 201
    assert "user_id" in resp.json()


def test_hr_can_update_employee(client, hr_token, seed):
    resp = client.put(f"/hr/employees/{seed['emp_id']}",
                      json={"designation": "Senior Analyst", "department": "Finance & Accounts"},
                      headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200


def test_hr_cannot_access_other_org_employee(client, hr_token, SessionFactory):
    """Create an employee in a different org and verify HR can't see them."""
    db = SessionFactory()
    now = time.time()
    other_org = Organization(name="Other Corp", slug="other-corp", created_at=now, updated_at=now)
    db.add(other_org)
    db.flush()
    other_emp = User(
        email="other_org_emp@test.com",
        hashed_password=hash_password("OtherPass@1"),
        full_name="Other Org Employee",
        role=UserRole.EMPLOYEE,
        organization_id=other_org.id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(other_emp)
    db.commit()
    other_emp_id = other_emp.id
    db.close()

    resp = client.get(f"/hr/employees/{other_emp_id}",
                      headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 404


# ─────────────────────────── Application management ──────────────────────────

def test_hr_can_list_applications(client, hr_token):
    resp = client.get("/hr/applications", headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200
    assert "total" in resp.json()
    assert "items" in resp.json()


def test_hr_can_initiate_assessment(client, hr_token, seed):
    resp = client.post("/hr/applications/initiate", json={
        "employee_profile_id": seed["profile_id"],
        "date_of_joining": "2018-04-01",
        "last_working_date": "2024-03-31",
        "employment_type": "permanent",
        "separation_reason": "retirement",
        "basic_salary": 60000.0,
        "dearness_allowance": 8000.0,
        "salary_type": "monthly",
        "employer_name": "HR Test Corp",
        "employee_count": 200,
    }, headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 201
    data = resp.json()
    assert "application_id" in data


def test_hr_can_record_review(client, hr_token, seed, SessionFactory):
    """Create an app directly in DB, then record a review via API."""
    from app.models import GratuityApplication, ApplicationStatus
    db = SessionFactory()
    now = time.time()
    app_obj = GratuityApplication(
        user_id=seed["emp_id"],
        employee_profile_id=seed["profile_id"],
        status=ApplicationStatus.PENDING_HR_REVIEW,
        form_data="{}",
        created_at=now,
        updated_at=now,
        submitted_at=now,
    )
    db.add(app_obj)
    db.commit()
    app_id = app_obj.id
    db.close()

    resp = client.post(f"/hr/applications/{app_id}/review", json={
        "decision": "APPROVED",
        "comments": "All documents verified. Employee served 6 years continuously. Gratuity payable under Section 53 CoSS 2020.",
    }, headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_status"] == "APPROVED"
    assert "review_id" in data


def test_hr_review_requires_mandatory_comments(client, hr_token, seed, SessionFactory):
    """Comments shorter than 10 chars should fail validation."""
    from app.models import GratuityApplication, ApplicationStatus
    db = SessionFactory()
    now = time.time()
    app_obj = GratuityApplication(
        user_id=seed["emp_id"],
        employee_profile_id=seed["profile_id"],
        status=ApplicationStatus.PENDING_HR_REVIEW,
        form_data="{}",
        created_at=now,
        updated_at=now,
    )
    db.add(app_obj)
    db.commit()
    app_id = app_obj.id
    db.close()

    resp = client.post(f"/hr/applications/{app_id}/review", json={
        "decision": "REJECTED",
        "comments": "Too short",  # < 10 chars would fail, but "Too short" is 9 chars
    }, headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 422  # validation error


# ─────────────────────────── Role protection ─────────────────────────────────

def test_employee_cannot_access_hr_employees(client, emp_token):
    resp = client.get("/hr/employees", headers={"Authorization": f"Bearer {emp_token}"})
    assert resp.status_code == 403


def test_employee_cannot_record_hr_review(client, emp_token):
    resp = client.post("/hr/applications/999/review",
                       json={"decision": "APPROVED", "comments": "Should not work at all ever"},
                       headers={"Authorization": f"Bearer {emp_token}"})
    assert resp.status_code == 403


# ─────────────────────────── Reports ─────────────────────────────────────────

def test_hr_reports_summary(client, hr_token):
    resp = client.get("/hr/reports/summary", headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_applications" in data
    assert "by_status" in data


# ─────────────────────────── Admin ───────────────────────────────────────────

def test_hr_can_create_another_hr_account(client, hr_token):
    resp = client.post("/admin/create-hr", json={
        "full_name": "Second HR Manager",
        "email": "hr2@hrtest.com",
        "password": "HRTest2@123",
    }, headers={"Authorization": f"Bearer {hr_token}"})
    assert resp.status_code == 201
    assert resp.json()["role"] == "HR_ADMIN"


def test_employee_cannot_create_hr_account(client, emp_token):
    resp = client.post("/admin/create-hr", json={
        "full_name": "Evil Escalation",
        "email": "evil@hrtest.com",
        "password": "EvilPass@123",
    }, headers={"Authorization": f"Bearer {emp_token}"})
    assert resp.status_code == 403
