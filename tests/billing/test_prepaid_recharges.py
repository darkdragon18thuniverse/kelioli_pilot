import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.call import Call
from src.app.models.prepaid import Prepaid


def _login(client, email, password="Password2026!"):
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.json()["access_token"]


def _superadmin(client, email="super_recharge@test.com"):
    User.create(role_id=1, organization_id=None, department_id=None, name="Super Admin", email=email, password_raw="Password2026!")
    return _login(client, email)


def _org_admin(client, org_id, email="admin_recharge@test.com"):
    User.create(role_id=2, organization_id=org_id, department_id=None, name="Org Admin", email=email, password_raw="Password2026!")
    return _login(client, email)


# ------------------------------------------------------------------
# Server-computed amounts (never trust client-sent amount — there is no
# amount field on the request schema at all, which is the real guarantee)
# ------------------------------------------------------------------

def test_create_minutes_recharge_amount_is_server_computed(client):
    org_id = Organization.create(name="Recharge Org", slug="recharge-org", per_minute_cost=0.75, infra_fixed_cost=200.0)
    token = _superadmin(client)

    res = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 1000, "payment_reference": "REF-001"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["unit_price_at_purchase"] == 0.75
    assert data["amount_charged"] == 750.0  # 0.75 * 1000, server-computed
    assert data["new_minute_balance"] == 1000.0
    assert data["new_state"] == "ok"


def test_create_infra_recharge_amount_and_period(client):
    org_id = Organization.create(name="Infra Org", slug="infra-org", per_minute_cost=0.5, infra_fixed_cost=150.0)
    token = _superadmin(client, email="super_infra@test.com")

    res = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "infra", "months_purchased": 3, "payment_reference": "REF-INFRA"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["unit_price_at_purchase"] == 150.0
    assert data["amount_charged"] == 450.0  # 150 * 3
    assert data["infra_period_end"] is not None


def test_create_recharge_rejects_invalid_infra_months(client):
    org_id = Organization.create(name="Bad Infra Org", slug="bad-infra-org", infra_fixed_cost=100.0)
    token = _superadmin(client, email="super_bad_infra@test.com")

    res = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "infra", "months_purchased": 5},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


def test_create_recharge_rejects_zero_minutes(client):
    org_id = Organization.create(name="Bad Minutes Org", slug="bad-minutes-org", per_minute_cost=0.5)
    token = _superadmin(client, email="super_bad_min@test.com")

    res = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 0},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


def test_stacked_infra_recharges_via_api(client):
    org_id = Organization.create(name="Stack Org", slug="stack-org", infra_fixed_cost=100.0)
    token = _superadmin(client, email="super_stack@test.com")

    first = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "infra", "months_purchased": 3},
        headers={"Authorization": f"Bearer {token}"}
    ).json()

    second = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "infra", "months_purchased": 6},
        headers={"Authorization": f"Bearer {token}"}
    ).json()

    assert second["infra_period_end"] > first["infra_period_end"]


# ------------------------------------------------------------------
# Void
# ------------------------------------------------------------------

def test_void_recharge_via_api_reverses_and_updates_balance(client):
    org_id = Organization.create(name="Void Org", slug="void-org", per_minute_cost=0.5)
    token = _superadmin(client, email="super_void@test.com")

    create_res = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 500},
        headers={"Authorization": f"Bearer {token}"}
    )
    recharge_id = create_res.json()["id"]
    assert create_res.json()["new_minute_balance"] == 500.0

    void_res = client.post(
        f"/api/v1/billing/recharges/{recharge_id}/void",
        json={"reason": "entered by mistake"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert void_res.status_code == status.HTTP_200_OK
    assert void_res.json()["new_minute_balance"] == 0.0


def test_void_recharge_twice_via_api_rejected(client):
    org_id = Organization.create(name="Double Void Org", slug="double-void-org", per_minute_cost=0.5)
    token = _superadmin(client, email="super_double_void@test.com")

    recharge_id = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 500},
        headers={"Authorization": f"Bearer {token}"}
    ).json()["id"]

    first_void = client.post(
        f"/api/v1/billing/recharges/{recharge_id}/void",
        json={"reason": "first"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert first_void.status_code == status.HTTP_200_OK

    second_void = client.post(
        f"/api/v1/billing/recharges/{recharge_id}/void",
        json={"reason": "second"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert second_void.status_code == status.HTTP_400_BAD_REQUEST


# ------------------------------------------------------------------
# List + balance + ledger reads
# ------------------------------------------------------------------

def test_list_recharges_and_get_balance(client):
    org_id = Organization.create(name="List Recharge Org", slug="list-recharge-org", per_minute_cost=0.5)
    token = _superadmin(client, email="super_list_recharge@test.com")

    client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 200},
        headers={"Authorization": f"Bearer {token}"}
    )

    list_res = client.get(f"/api/v1/billing/recharges?organization_id={org_id}", headers={"Authorization": f"Bearer {token}"})
    assert list_res.status_code == status.HTTP_200_OK
    assert list_res.json()["total"] == 1

    balance_res = client.get(f"/api/v1/billing/balance?organization_id={org_id}", headers={"Authorization": f"Bearer {token}"})
    assert balance_res.status_code == status.HTTP_200_OK
    balance_data = balance_res.json()
    assert balance_data["minute_balance"] == 200.0  # minutes_purchased credits minutes directly, not money
    assert balance_data["state"] == "ok"


def test_get_balance_blocked_when_no_recharge_ever(client):
    org_id = Organization.create(name="Never Recharged Org", slug="never-recharged-org")
    token = _org_admin(client, org_id, email="admin_never@test.com")

    res = client.get(f"/api/v1/billing/balance?organization_id={org_id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["state"] == "blocked"
    assert data["minute_balance"] == 0.0


def test_list_ledger_reflects_credits_and_debits(client):
    org_id = Organization.create(name="Ledger Org", slug="ledger-org", per_minute_cost=1.0)
    token = _superadmin(client, email="super_ledger@test.com")

    client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 100},
        headers={"Authorization": f"Bearer {token}"}
    )
    dept_id = Department.create(organization_id=org_id, name="Ledger Dept", slug="ledger-dept")
    call_id = Call.create(organization_id=org_id, department_id=dept_id, audio_url="test.wav")
    Prepaid.debit_call(org_id, call_id=call_id, minutes=10.0)

    ledger_res = client.get(f"/api/v1/billing/ledger?organization_id={org_id}", headers={"Authorization": f"Bearer {token}"})
    assert ledger_res.status_code == status.HTTP_200_OK
    data = ledger_res.json()
    assert data["total"] == 2
    assert data["minute_balance"] == 90.0


# ------------------------------------------------------------------
# RBAC — all 5 endpoints, wrong role and cross-tenant
# ------------------------------------------------------------------

def test_rbac_non_superadmin_cannot_create_recharge(client):
    org_id = Organization.create(name="RBAC Org", slug="rbac-org")
    token = _org_admin(client, org_id, email="admin_rbac1@test.com")

    res = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 100},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_rbac_non_superadmin_cannot_void_recharge(client):
    org_id = Organization.create(name="RBAC Void Org", slug="rbac-void-org", per_minute_cost=0.5)
    super_token = _superadmin(client, email="super_rbac_void@test.com")
    recharge_id = client.post(
        "/api/v1/billing/recharges",
        json={"organization_id": org_id, "recharge_type": "minutes", "minutes_purchased": 100},
        headers={"Authorization": f"Bearer {super_token}"}
    ).json()["id"]

    admin_token = _org_admin(client, org_id, email="admin_rbac2@test.com")
    res = client.post(
        f"/api/v1/billing/recharges/{recharge_id}/void",
        json={"reason": "attempted by non-superadmin"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_rbac_cross_tenant_balance_denied(client):
    org1_id = Organization.create(name="Tenant One", slug="tenant-one")
    org2_id = Organization.create(name="Tenant Two", slug="tenant-two")
    token = _org_admin(client, org1_id, email="admin_tenant1@test.com")

    res = client.get(f"/api/v1/billing/balance?organization_id={org2_id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_rbac_cross_tenant_recharges_list_denied(client):
    org1_id = Organization.create(name="Tenant Three", slug="tenant-three")
    org2_id = Organization.create(name="Tenant Four", slug="tenant-four")
    token = _org_admin(client, org1_id, email="admin_tenant3@test.com")

    res = client.get(f"/api/v1/billing/recharges?organization_id={org2_id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_rbac_cross_tenant_ledger_denied(client):
    org1_id = Organization.create(name="Tenant Five", slug="tenant-five")
    org2_id = Organization.create(name="Tenant Six", slug="tenant-six")
    token = _org_admin(client, org1_id, email="admin_tenant5@test.com")

    res = client.get(f"/api/v1/billing/ledger?organization_id={org2_id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_403_FORBIDDEN
