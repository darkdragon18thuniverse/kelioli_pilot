import pytest
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.models.department import Department


# --- 🔵 DIRECT MODEL & DATABASE RULE TESTS ---

def test_organization_dynamic_update_fields():
    """Organization.update safely mutates specified fields while ignoring invalid keys."""
    org_id = Organization.create(name="Model Org", slug="model-org", tier="free")
    
    updated = Organization.update(org_id, {
        "name": "Model Org Updated",
        "tier": "enterprise",
        "per_minute_cost": 0.25,
        "non_existent_column": "ignored_value"
    })
    assert updated is True

    org = Organization.get_by_id(org_id)
    assert org["name"] == "Model Org Updated"
    assert org["tier"] == "enterprise"
    assert org["per_minute_cost"] == 0.25


def test_user_list_all_with_relations_joins():
    """User.list_all_with_relations correctly populates joined role and organization names."""
    org_id = Organization.create(name="Relational Med", slug="relational-med")
    dept_id = Department.create(organization_id=org_id, name="ICU", slug="icu")
    
    user_id = User.create(
        role_id=4,
        organization_id=org_id,
        department_id=dept_id,
        name="ICU Nurse",
        email="nurse@relationalmed.com",
        password_raw="Pass12345!"
    )

    user_rel = User.get_by_id_with_relations(user_id)
    assert user_rel["name"] == "ICU Nurse"
    assert user_rel["organization_name"] == "Relational Med"
    assert user_rel["department_name"] == "ICU"
    assert user_rel["role_name"] == "agent"


def test_user_update_password_rehashing():
    """Updating a user's password using User.update stores a newly hashed bcrypt value."""
    org_id = Organization.create(name="Pwd Org", slug="pwd-org")
    user_id = User.create(
        role_id=2,
        organization_id=org_id,
        department_id=None,
        name="Update User",
        email="update@pwdorg.com",
        password_raw="OldPassword123!"
    )
    
    old_hash = User.get_by_id(user_id)["password_hash"]

    # Update password
    User.update(user_id, {"password_raw": "BrandNewPassword2026!"})
    new_hash = User.get_by_id(user_id)["password_hash"]

    assert old_hash != new_hash
    assert new_hash.startswith("$2b$")
    assert User.verify_credentials("update@pwdorg.com", "BrandNewPassword2026!") is not None


def test_organization_list_all_versus_list_active():
    """Organization.list_all includes suspended organizations, whereas list_active filters them out."""
    active_id = Organization.create(name="Active Org", slug="active-org")
    suspended_id = Organization.create(name="Suspended Org", slug="suspended-org")
    Organization.soft_delete(suspended_id)

    active_list = Organization.list_active()
    all_list = Organization.list_all()

    active_ids = [o["id"] for o in active_list]
    all_ids = [o["id"] for o in all_list]

    assert active_id in active_ids
    assert suspended_id not in active_ids
    assert suspended_id in all_ids
