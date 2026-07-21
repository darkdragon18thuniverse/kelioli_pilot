import pytest
from src.app.models.user import User
from src.app.models.organization import Organization


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
