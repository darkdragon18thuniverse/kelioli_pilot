import pytest
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.core.database import init_database


@pytest.fixture(autouse=True)
def seed_environment():
    superadmin_id = User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Global Superadmin",
        email="superadmin@curigon.com",
        password_raw="SuperPass2026!"
    )
    org_id = Organization.create(
        name="Field Test Org",
        slug="field-test-org",
        billing_email="billing@fieldtest.com",
        tier="growth"
    )
    User.create(
        role_id=2,
        organization_id=org_id,
        department_id=None,
        name="Tenant Admin",
        email="admin@fieldtest.com",
        password_raw="AdminPass2026!"
    )


def get_superadmin_token(client):
    res = client.post("/api/v1/auth/login", data={"username": "superadmin@curigon.com", "password": "SuperPass2026!"})
    return res.json()["access_token"]


# --- 🟡 HTTP SCHEMA & FIELD VALIDATION TESTS ---

def test_organization_create_missing_required_fields(client):
    """Creating an organization without name/slug fails with 422 Unprocessable Entity."""
    token = get_superadmin_token(client)
    res = client.post(
        "/api/v1/admin/organizations",
        json={"tier": "growth"}, # Missing 'name' and 'slug'
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 422


def test_organization_create_invalid_email_format(client):
    """Providing a malformed billing email string returns 422."""
    token = get_superadmin_token(client)
    res = client.post(
        "/api/v1/admin/organizations",
        json={
            "name": "Bad Email Corp",
            "slug": "bad-email",
            "billing_email": "not-an-email"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 422


def test_organization_update_invalid_status_enum(client):
    """Updating an organization with an unmapped status value is caught at the schema/controller layer."""
    token = get_superadmin_token(client)
    orgs_res = client.get("/api/v1/admin/organizations", headers={"Authorization": f"Bearer {token}"})
    org_id = orgs_res.json()["organizations"][0]["id"]

    res = client.put(
        f"/api/v1/admin/organizations/{org_id}",
        json={"name": "Valid Name"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200


def test_user_create_invalid_password_length(client):
    """Creating a user with a password under 8 characters returns 422."""
    token = get_superadmin_token(client)
    res = client.post(
        "/api/v1/admin/users",
        json={
            "role_id": 1,
            "name": "Short Password User",
            "email": "short@curigon.com",
            "password": "short" # Under 8 chars
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 422


def test_get_nonexistent_organization_404(client):
    """Fetching an unknown organization ID returns 404 Not Found."""
    token = get_superadmin_token(client)
    res = client.get(
        "/api/v1/admin/organizations/999999",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 404


def test_get_nonexistent_user_404(client):
    """Fetching an unknown user ID returns 404 Not Found."""
    token = get_superadmin_token(client)
    res = client.get(
        "/api/v1/admin/users/999999",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 404


# --- 🟡 target_compliance_score: create/update round-trip + migration idempotency ---

def test_organization_create_default_target_compliance_score(client):
    """Creating an org without specifying target_compliance_score defaults to 85.0."""
    token = get_superadmin_token(client)
    res = client.post(
        "/api/v1/admin/organizations",
        json={"name": "Default Target Org", "slug": "default-target-org"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    org_id = res.json()["id"]
    org_db = Organization.get_by_id(org_id)
    assert org_db["target_compliance_score"] == 85.0


def test_organization_create_and_update_custom_target_compliance_score(client):
    """Superadmin can set a custom target_compliance_score at create time, and change it via update."""
    token = get_superadmin_token(client)

    res_create = client.post(
        "/api/v1/admin/organizations",
        json={
            "name": "Custom Target Org",
            "slug": "custom-target-org",
            "target_compliance_score": 92.5
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_create.status_code == 201
    org_id = res_create.json()["id"]
    org_db = Organization.get_by_id(org_id)
    assert org_db["target_compliance_score"] == 92.5

    res_update = client.put(
        f"/api/v1/admin/organizations/{org_id}",
        json={"target_compliance_score": 70.0},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_update.status_code == 200
    org_db_updated = Organization.get_by_id(org_id)
    assert org_db_updated["target_compliance_score"] == 70.0


def test_organization_create_target_compliance_score_out_of_bounds_rejected(client):
    """target_compliance_score outside [0, 100] is rejected with 422."""
    token = get_superadmin_token(client)
    res = client.post(
        "/api/v1/admin/organizations",
        json={
            "name": "Bad Target Org",
            "slug": "bad-target-org",
            "target_compliance_score": 150.0
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 422


def test_target_compliance_score_migration_idempotent_on_populated_db():
    """Running init_database() twice against a DB that already has organizations
    with a non-default target_compliance_score must not error and must not
    overwrite the existing value (mirrors the llm_provider/prepaid migration tests)."""
    org_id = Organization.create(
        name="Migration Target Org", slug="migration-target-org",
        target_compliance_score=77.0
    )

    init_database()
    init_database()

    org_row = Organization.get_by_id(org_id)
    assert org_row["target_compliance_score"] == 77.0
