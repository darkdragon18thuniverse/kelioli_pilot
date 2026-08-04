import pytest
from datetime import datetime, timezone
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.call import Call
from src.app.models.base import DatabaseManager


@pytest.fixture
def filter_test_setup(client):
    org_id = Organization.create(name="Filter Test Org", slug="filter-test-org")
    dept_id = Department.create(organization_id=org_id, name="Filter Dept", slug="filter-dept")

    admin_id = User.create(
        role_id=2,
        organization_id=org_id,
        department_id=None,
        name="Filter Admin",
        email="filter_admin@test.com",
        password_raw="FilterPass123!"
    )

    # Create test call records
    c1_id = Call.create(
        organization_id=org_id,
        department_id=dept_id,
        audio_url="http://test.com/audio1.mp3",
        duration_seconds=120.0,
        procedure_enquired="Root Canal Treatment"
    )
    Call.update_evaluation_results(
        call_id=c1_id,
        transcript="Patient called for Root Canal Treatment consultation.",
        duration_seconds=120.0,
        total_checked=5,
        total_passed=5,
        compliance_score_percentage=100.0,
        procedure_enquired="Root Canal Treatment",
        processing_status="completed"
    )

    c2_id = Call.create(
        organization_id=org_id,
        department_id=dept_id,
        audio_url="http://test.com/audio2.mp3",
        duration_seconds=180.0,
        procedure_enquired="Dental Implant Consultation"
    )
    Call.update_evaluation_results(
        call_id=c2_id,
        transcript="Patient requested info about Dental Implant cost and procedure.",
        duration_seconds=180.0,
        total_checked=5,
        total_passed=4,
        compliance_score_percentage=80.0,
        procedure_enquired="Dental Implant Consultation",
        processing_status="completed"
    )

    # Set specific created_at dates for c1 and c2 in database
    DatabaseManager.execute_update(
        "UPDATE calls SET created_at = '2026-08-01 10:00:00' WHERE id = ?;",
        (c1_id,)
    )
    DatabaseManager.execute_update(
        "UPDATE calls SET created_at = '2026-08-03 14:00:00' WHERE id = ?;",
        (c2_id,)
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "filter_admin@test.com", "password": "FilterPass123!"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    return {
        "org_id": org_id,
        "dept_id": dept_id,
        "headers": headers,
        "c1_id": c1_id,
        "c2_id": c2_id
    }


def test_dashboard_custom_date_range_filtering(client, filter_test_setup):
    headers = filter_test_setup["headers"]

    # Filter dashboard for 2026-08-01 to 2026-08-02 (should include only c1)
    res1 = client.get("/api/v1/admin/dashboard?start_date=2026-08-01&end_date=2026-08-02", headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["period"]["label"] == "2026-08-01 to 2026-08-02"
    assert data1["kpis"]["calls_audited_count"] == 1

    # Filter dashboard for 2026-08-01 to 2026-08-04 (should include both c1 and c2)
    res2 = client.get("/api/v1/admin/dashboard?start_date=2026-08-01&end_date=2026-08-04", headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["kpis"]["calls_audited_count"] == 2


def test_calls_list_date_range_filtering(client, filter_test_setup):
    headers = filter_test_setup["headers"]

    # Filter calls for 2026-08-01 to 2026-08-02 -> expect 1 call (c1)
    res1 = client.get("/api/v1/calls?start_date=2026-08-01&end_date=2026-08-02", headers=headers)
    assert res1.status_code == 200
    calls1 = res1.json()["calls"]
    assert len(calls1) == 1
    assert calls1[0]["id"] == filter_test_setup["c1_id"]

    # Filter calls for 2026-08-03 to 2026-08-04 -> expect 1 call (c2)
    res2 = client.get("/api/v1/calls?start_date=2026-08-03&end_date=2026-08-04", headers=headers)
    assert res2.status_code == 200
    calls2 = res2.json()["calls"]
    assert len(calls2) == 1
    assert calls2[0]["id"] == filter_test_setup["c2_id"]


def test_calls_list_search_keyword_filtering(client, filter_test_setup):
    headers = filter_test_setup["headers"]

    # Search for "Root Canal" -> expect c1
    res1 = client.get("/api/v1/calls?search=Root%20Canal", headers=headers)
    assert res1.status_code == 200
    calls1 = res1.json()["calls"]
    assert len(calls1) == 1
    assert calls1[0]["id"] == filter_test_setup["c1_id"]

    # Search for "Implant" -> expect c2
    res2 = client.get("/api/v1/calls?search=Implant", headers=headers)
    assert res2.status_code == 200
    calls2 = res2.json()["calls"]
    assert len(calls2) == 1
    assert calls2[0]["id"] == filter_test_setup["c2_id"]

    # Search for non-existent keyword -> expect 0 calls
    res3 = client.get("/api/v1/calls?search=NonExistentProcedure123", headers=headers)
    assert res3.status_code == 200
    assert len(res3.json()["calls"]) == 0
