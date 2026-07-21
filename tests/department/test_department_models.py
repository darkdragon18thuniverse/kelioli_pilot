import pytest
from src.app.models.organization import Organization
from src.app.models.department import Department


def test_department_duplicate_slug_rejection(wipe_tables_between_tests):
    """Enforces unique slug constraints per organization."""
    org_id = Organization.create(name="Org Model", slug="org-model")

    Department.create(organization_id=org_id, name="ICU", slug="icu")

    with pytest.raises(ValueError, match="already exists"):
        Department.create(organization_id=org_id, name="Intensive Care Unit", slug="icu")


def test_department_dynamic_update_and_soft_delete(wipe_tables_between_tests):
    """Tests SQL field mutations and soft-deletion behavior."""
    org_id = Organization.create(name="Org Model 2", slug="org-model-2")
    dept_id = Department.create(organization_id=org_id, name="ER", slug="er")

    updated = Department.update(dept_id, {"name": "Emergency Room", "slug": "emergency-room"})
    assert updated is True

    fetched = Department.get_by_id(dept_id)
    assert fetched["name"] == "Emergency Room"
    assert fetched["slug"] == "emergency-room"

    deleted = Department.soft_delete(dept_id)
    assert deleted is True

    fetched_after = Department.get_by_id(dept_id)
    assert fetched_after["status"] == "inactive"
