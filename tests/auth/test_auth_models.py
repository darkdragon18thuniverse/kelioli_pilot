import pytest
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.models.department import Department


# --- 🔵 DIRECT MODEL & DATABASE RULE TESTS ---

def test_superadmin_role_boundary_constraints():
    """Superadmins must not be assigned to an organization or department."""
    # Success Path: None for both org and dept
    user_id = User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Superadmin",
        email="super@curigon.com",
        password_raw="Password123!"
    )
    assert user_id > 0


def test_tenant_admin_and_manager_require_organization():
    """Tenant Admins (role 2) and Managers (role 3) must have a valid organization_id."""
    with pytest.raises(ValueError, match="Non-superadmin records must specify a valid organization mapping"):
        User.create(
            role_id=2,
            organization_id=None,
            department_id=None,
            name="Orphan Admin",
            email="orphan_admin@curigon.com",
            password_raw="Password123!"
        )


def test_agent_requires_organization_and_department():
    """Agents (role 4) require both an organization_id AND a department_id."""
    org_id = Organization.create(
        name="Test Org",
        slug="test-org",
        billing_email="billing@test.com"
    )

    # Missing department_id
    with pytest.raises(ValueError, match="Agent accounts must be assigned to an active department framework"):
        User.create(
            role_id=4,
            organization_id=org_id,
            department_id=None,
            name="Orphan Agent",
            email="orphan_agent@curigon.com",
            password_raw="Password123!"
        )


def test_department_must_belong_to_same_organization():
    """An Agent cannot be created with a department belonging to a different organization."""
    org1_id = Organization.create(name="Org One", slug="org-one")
    org2_id = Organization.create(name="Org Two", slug="org-two")
    dept_in_org2 = Department.create(organization_id=org2_id, name="Sales", slug="sales")

    # Attempting to assign dept_in_org2 while user is assigned to org1_id
    with pytest.raises(ValueError, match="Department does not belong to organization or is inactive"):
        User.create(
            role_id=4,
            organization_id=org1_id,
            department_id=dept_in_org2,
            name="Cross Tenant Agent",
            email="crosstenant@curigon.com",
            password_raw="Password123!"
        )


def test_duplicate_active_email_rejected():
    """Creating two active users with the same email raises an Account Conflict error."""
    User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="User One",
        email="duplicate@curigon.com",
        password_raw="Password123!"
    )

    with pytest.raises(ValueError, match="Account Conflict"):
        User.create(
            role_id=1,
            organization_id=None,
            department_id=None,
            name="User Two",
            email="duplicate@curigon.com",
            password_raw="Password123!"
        )


def test_soft_deleted_user_reactivation_loop():
    """Re-creating a soft-deleted user updates and reactivates their profile without duplicate key errors."""
    # 1. Create and suspend user
    user_id = User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Initial User",
        email="revival@curigon.com",
        password_raw="Password123!"
    )
    User.soft_delete(user_id)

    # 2. Re-create user with same email
    reactivated_id = User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Reactivated User",
        email="revival@curigon.com",
        password_raw="NewPassword123!"
    )

    assert reactivated_id == user_id
    reactivated_user = User.get_by_id(user_id)
    assert reactivated_user["status"] == "active"
    assert reactivated_user["name"] == "Reactivated User"


def test_password_never_exposed_raw():
    """Verify stored password hashes are bcrypt hashed and non-empty."""
    user_id = User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Security User",
        email="security@curigon.com",
        password_raw="RawPassword123!"
    )
    user = User.get_by_id(user_id)
    assert user["password_hash"] != "RawPassword123!"
    assert user["password_hash"].startswith("$2b$")