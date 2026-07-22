import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.billing import Billing


def test_get_daily_usage_with_totals_aggregation(client):
    """GET /api/v1/billing/usage returns daily rows and aggregated totals block."""
    org_id = Organization.create(name="Usage Org", slug="usage-org")
    dept_id = Department.create(organization_id=org_id, name="Cardiology", slug="cardio")
    user_id = User.create(
        role_id=2, organization_id=org_id, department_id=dept_id,
        name="Usage Admin", email="admin@usageorg.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@usageorg.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    # Seed daily metrics records directly
    Billing.refresh_daily_metrics(
        organization_id=org_id, department_id=dept_id, user_id=user_id,
        usage_date="2026-07-01", total_minutes=15.5, total_calls_processed=10, total_calls_failed=1
    )
    Billing.refresh_daily_metrics(
        organization_id=org_id, department_id=dept_id, user_id=user_id,
        usage_date="2026-07-02", total_minutes=20.0, total_calls_processed=15, total_calls_failed=0
    )

    # Query usage
    res = client.get(
        f"/api/v1/billing/usage?organization_id={org_id}&start_date=2026-07-01&end_date=2026-07-02",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert "usage" in data
    assert "totals" in data

    usage = data["usage"]
    assert len(usage) == 2
    assert usage[0]["usage_date"] == "2026-07-01"
    assert usage[0]["total_minutes"] == 15.5
    assert usage[0]["total_calls_processed"] == 10
    assert usage[0]["total_calls_failed"] == 1

    totals = data["totals"]
    assert totals["total_minutes"] == 35.5
    assert totals["total_calls_processed"] == 25
    assert totals["total_calls_failed"] == 1


def test_daily_usage_filtering_by_department_and_user(client):
    """GET /api/v1/billing/usage supports filtering by department_id and user_id."""
    org_id = Organization.create(name="Usage Org 2", slug="usage-org-2")
    dept1_id = Department.create(organization_id=org_id, name="Dept 1", slug="dept-1")
    dept2_id = Department.create(organization_id=org_id, name="Dept 2", slug="dept-2")

    u1_id = User.create(role_id=2, organization_id=org_id, department_id=dept1_id, name="Admin 1", email="a1@usage2.com", password_raw="Password2026!")
    u2_id = User.create(role_id=4, organization_id=org_id, department_id=dept2_id, name="Agent 2", email="a2@usage2.com", password_raw="Password2026!")

    login_res = client.post("/api/v1/auth/login", data={"username": "a1@usage2.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    Billing.refresh_daily_metrics(org_id, dept1_id, u1_id, "2026-07-10", 10.0, 5, 0)
    Billing.refresh_daily_metrics(org_id, dept2_id, u2_id, "2026-07-10", 30.0, 12, 2)

    # Filter by dept1
    res_dept1 = client.get(
        f"/api/v1/billing/usage?organization_id={org_id}&department_id={dept1_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_dept1.status_code == status.HTTP_200_OK
    data1 = res_dept1.json()
    assert len(data1["usage"]) == 1
    assert data1["usage"][0]["department_id"] == dept1_id
    assert data1["totals"]["total_minutes"] == 10.0

    # Filter by user2
    res_user2 = client.get(
        f"/api/v1/billing/usage?organization_id={org_id}&user_id={u2_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_user2.status_code == status.HTTP_200_OK
    data2 = res_user2.json()
    assert len(data2["usage"]) == 1
    assert data2["usage"][0]["user_id"] == u2_id
    assert data2["totals"]["total_minutes"] == 30.0
