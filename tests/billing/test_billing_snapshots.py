import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.billing import Billing


def test_create_billing_snapshot_and_auto_compute_total_spend(client):
    """Superadmin creates a billing snapshot and server auto-computes total_spend_calculated."""
    # Setup Superadmin
    User.create(role_id=1, organization_id=None, department_id=None, name="Super Admin", email="super@billing.com", password_raw="Password2026!")
    login_res = client.post("/api/v1/auth/login", data={"username": "super@billing.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    org_id = Organization.create(name="Billing Org", slug="billing-org")

    payload = {
        "organization_id": org_id,
        "tier_at_billing": "growth",
        "infra_fixed_cost_charged": 100.0,
        "per_minute_cost_charged": 0.50,
        "total_minutes_consumed": 120.0,
        "billing_period_start": "2026-06-01",
        "billing_period_end": "2026-06-30"
    }

    res = client.post(
        "/api/v1/billing/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["status"] == "success"
    assert "id" in data
    # 100.0 + (0.50 * 120.0) = 160.0
    assert data["total_spend_calculated"] == 160.0


def test_list_and_get_billing_snapshots(client):
    """List billing snapshots filtered by organization_id and optional payment_status, and fetch single snapshot."""
    org_id = Organization.create(name="List Org", slug="list-org")
    User.create(role_id=2, organization_id=org_id, department_id=None, name="Org Admin", email="admin@listorg.com", password_raw="Password2026!")

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@listorg.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    snap1_id = Billing.create_snapshot(
        organization_id=org_id,
        tier_at_billing="free",
        infra_fixed_cost_charged=0.0,
        per_minute_cost_charged=0.0,
        total_minutes_consumed=10.0,
        total_spend_calculated=0.0,
        billing_period_start="2026-05-01",
        billing_period_end="2026-05-31",
        payment_status="paid"
    )

    snap2_id = Billing.create_snapshot(
        organization_id=org_id,
        tier_at_billing="growth",
        infra_fixed_cost_charged=50.0,
        per_minute_cost_charged=0.25,
        total_minutes_consumed=40.0,
        total_spend_calculated=60.0,
        billing_period_start="2026-06-01",
        billing_period_end="2026-06-30",
        payment_status="unpaid"
    )

    # List all for org
    res = client.get(
        f"/api/v1/billing/snapshots?organization_id={org_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_200_OK
    snapshots = res.json()["snapshots"]
    assert len(snapshots) == 2

    # Filter by payment_status
    res_paid = client.get(
        f"/api/v1/billing/snapshots?organization_id={org_id}&payment_status=paid",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_paid.status_code == status.HTTP_200_OK
    paid_snaps = res_paid.json()["snapshots"]
    assert len(paid_snaps) == 1
    assert paid_snaps[0]["id"] == snap1_id
    assert paid_snaps[0]["payment_status"] == "paid"

    # Get single snapshot detail
    res_single = client.get(
        f"/api/v1/billing/snapshots/{snap2_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_single.status_code == status.HTTP_200_OK
    single_snap = res_single.json()
    assert single_snap["id"] == snap2_id
    assert single_snap["tier_at_billing"] == "growth"
    assert single_snap["total_spend_calculated"] == 60.0


def test_update_snapshot_payment_status(client):
    """PUT /api/v1/billing/snapshots/{id} updates payment_status only."""
    org_id = Organization.create(name="Update Org", slug="update-org")
    User.create(role_id=2, organization_id=org_id, department_id=None, name="Org Admin", email="admin@updateorg.com", password_raw="Password2026!")

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@updateorg.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    snap_id = Billing.create_snapshot(
        organization_id=org_id,
        tier_at_billing="enterprise",
        infra_fixed_cost_charged=500.0,
        per_minute_cost_charged=0.10,
        total_minutes_consumed=1000.0,
        total_spend_calculated=600.0,
        billing_period_start="2026-06-01",
        billing_period_end="2026-06-30",
        payment_status="unpaid"
    )

    # Update to paid
    res = client.put(
        f"/api/v1/billing/snapshots/{snap_id}",
        json={"payment_status": "paid"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["status"] == "success"

    # Verify update in DB via GET
    res_snap = client.get(
        f"/api/v1/billing/snapshots/{snap_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_snap.json()["payment_status"] == "paid"

    # Invalid payment status raises 400
    res_invalid = client.put(
        f"/api/v1/billing/snapshots/{snap_id}",
        json={"payment_status": "invalid_status"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_invalid.status_code == status.HTTP_400_BAD_REQUEST


def test_billing_snapshots_rbac_scoping(client):
    """Cross-tenant access to snapshots is blocked with 403."""
    org1_id = Organization.create(name="Org One", slug="org-one")
    org2_id = Organization.create(name="Org Two", slug="org-two")

    User.create(role_id=2, organization_id=org1_id, department_id=None, name="Admin One", email="admin1@orgone.com", password_raw="Password2026!")

    login_res = client.post("/api/v1/auth/login", data={"username": "admin1@orgone.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    snap2_id = Billing.create_snapshot(
        organization_id=org2_id,
        tier_at_billing="growth",
        infra_fixed_cost_charged=100.0,
        per_minute_cost_charged=0.20,
        total_minutes_consumed=50.0,
        total_spend_calculated=110.0,
        billing_period_start="2026-06-01",
        billing_period_end="2026-06-30",
        payment_status="unpaid"
    )

    # Admin 1 attempts to list snapshots for Org 2 -> 403
    res_list = client.get(
        f"/api/v1/billing/snapshots?organization_id={org2_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_list.status_code == status.HTTP_403_FORBIDDEN

    # Admin 1 attempts to view snapshot for Org 2 -> 403
    res_get = client.get(
        f"/api/v1/billing/snapshots/{snap2_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_get.status_code == status.HTTP_403_FORBIDDEN

    # Admin 1 attempts to update snapshot for Org 2 -> 403
    res_put = client.put(
        f"/api/v1/billing/snapshots/{snap2_id}",
        json={"payment_status": "paid"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_put.status_code == status.HTTP_403_FORBIDDEN
