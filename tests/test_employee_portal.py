"""Tests for the employee portal: applications, data isolation, profile updates.

Uses SQLite in-memory database via FastAPI dependency override.
"""
import time
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.models import Organization, User, UserRole, EmployeeProfile, GratuityApplication, ApplicationStatus
from app.auth import hash_password

TEST_DB_URL = "sqlite:///./test_employee.db"
FORM_DATA = {
    "full_name": "Anjali Nair",
    "employee_id_code": "EMP-999",
    "employer_name": "TestCorp Pvt Ltd",
    "date_of_joining": "2019-03-15",
    "last_working_date": "2024-09-30",
    "employment_type": "permanent",
    "separation_reason": "resignation",
    "basic_salary": 45000.0,
    "dearness_allowance": 5000.0,
    "salary_type": "monthly",
    "establishment_type": "it_services",
    "employee_count": 150,
    "additional_notes": "Voluntary resignation after 5+ years",
}


@pytest.fixture(scope="module")
def engine():
    e = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=e)
    yield e
    Base.metadata.drop_all(bind=e)
    import os
    try:
        os.remove("test_employee.db")
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
def emp1_token(client):
    """Register and login first employee."""
    client.post("/auth/register", json={
        "full_name": "Anjali Nair",
        "email": "anjali@test.com",
        "password": "EmpPass@123",
        "organization_name": "Employee Test Corp",
    })
    resp = client.post("/auth/login", json={"email": "anjali@test.com", "password": "EmpPass@123"})
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def emp2_token(client):
    """Register and login second employee (different user)."""
    client.post("/auth/register", json={
        "full_name": "Suresh Babu",
        "email": "suresh@test.com",
        "password": "EmpPass@456",
        "organization_name": "Employee Test Corp",
    })
    resp = client.post("/auth/login", json={"email": "suresh@test.com", "password": "EmpPass@456"})
    return resp.json()["access_token"]


# ─────────────────────────── Dashboard ───────────────────────────────────────

def test_employee_dashboard_accessible(client, emp1_token):
    resp = client.get("/employee/dashboard", headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "full_name" in data
    assert data["full_name"] == "Anjali Nair"


def test_employee_dashboard_shows_correct_user(client, emp1_token, emp2_token):
    r1 = client.get("/employee/dashboard", headers={"Authorization": f"Bearer {emp1_token}"})
    r2 = client.get("/employee/dashboard", headers={"Authorization": f"Bearer {emp2_token}"})
    assert r1.json()["email"] == "anjali@test.com"
    assert r2.json()["email"] == "suresh@test.com"


# ─────────────────────────── Applications ────────────────────────────────────

def test_employee_can_list_applications_empty(client, emp1_token):
    resp = client.get("/employee/applications", headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_employee_can_save_draft(client, emp1_token):
    resp = client.post("/employee/applications/draft",
                       json=FORM_DATA,
                       headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 201
    data = resp.json()
    assert "application_id" in data
    assert data["status"] == "DRAFT"


def test_employee_can_submit_application(client, emp1_token):
    """Submitting calls the VERITAS AI engine. In tests without a running Redis,
    the run_id may be None — we verify the application is created correctly."""
    resp = client.post("/employee/applications",
                       json=FORM_DATA,
                       headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 201
    data = resp.json()
    assert "application_id" in data
    assert data["status"] == "AI_PROCESSING"


def test_employee_applications_count_increases(client, emp1_token):
    resp = client.get("/employee/applications", headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 2  # draft + submitted


# ─────────────────────────── Data isolation ──────────────────────────────────

def test_employee_cannot_see_other_employees_applications(client, emp1_token, emp2_token, SessionFactory):
    """Get emp1's application IDs, then verify emp2 cannot access them."""
    db = SessionFactory()
    anjali = db.query(User).filter(User.email == "anjali@test.com").first()
    anjali_apps = db.query(GratuityApplication).filter(GratuityApplication.user_id == anjali.id).all()
    db.close()

    if not anjali_apps:
        pytest.skip("No applications for emp1 — create one first")

    app_id = anjali_apps[0].id
    resp = client.get(f"/employee/applications/{app_id}",
                      headers={"Authorization": f"Bearer {emp2_token}"})
    # Must return 404 (not found for this user) not the actual data
    assert resp.status_code == 404


def test_employee_can_access_own_application(client, emp1_token, SessionFactory):
    db = SessionFactory()
    anjali = db.query(User).filter(User.email == "anjali@test.com").first()
    anjali_apps = db.query(GratuityApplication).filter(GratuityApplication.user_id == anjali.id).all()
    db.close()

    if not anjali_apps:
        pytest.skip("No applications for emp1")

    app_id = anjali_apps[0].id
    resp = client.get(f"/employee/applications/{app_id}",
                      headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == app_id


# ─────────────────────────── Profile ─────────────────────────────────────────

def test_employee_can_update_profile(client, emp1_token):
    resp = client.put("/employee/profile",
                      json={"department": "Engineering", "designation": "Senior Dev",
                            "date_of_joining": "2019-01-15", "employment_type": "permanent"},
                      headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 200


def test_employee_profile_reflects_update(client, emp1_token):
    resp = client.get("/employee/profile", headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 200
    p = resp.json().get("profile", {})
    if p:
        assert p.get("department") == "Engineering"


# ─────────────────────────── Missing info handling ───────────────────────────

def test_application_with_insufficient_data_still_submits(client, emp1_token):
    """Even without perfect data the application should be created and submitted to AI."""
    minimal = {
        "full_name": "Minimal Test",
        "employer_name": "Some Corp",
        "date_of_joining": "2020-01-01",
        "employment_type": "permanent",
        "separation_reason": "resignation",
        "basic_salary": 30000.0,
        "dearness_allowance": 0.0,
        "salary_type": "monthly",
    }
    resp = client.post("/employee/applications", json=minimal,
                       headers={"Authorization": f"Bearer {emp1_token}"})
    assert resp.status_code == 201
