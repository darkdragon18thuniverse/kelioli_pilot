import pytest
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.compliance import ComplianceParameter


def test_compliance_parameter_model_crud(wipe_tables_between_tests):
    """Direct database operations test for compliance parameters."""
    org_id = Organization.create(name="Model Org", slug="model-org")
    dept_id = Department.create(organization_id=org_id, name="ER", slug="er")

    # Create
    param_id = ComplianceParameter.create(
        organization_id=org_id,
        department_id=dept_id,
        parameter_name="HIPAA Consent",
        rule_description="Check for consent.",
        severity_level="critical"
    )
    assert param_id > 0

    # Fetch
    fetched = ComplianceParameter.get_by_id(param_id)
    assert fetched["parameter_name"] == "HIPAA Consent"
    assert fetched["severity_level"] == "critical"

    # Update
    updated = ComplianceParameter.update(param_id, {"parameter_name": "Updated Consent"})
    assert updated is True

    # Soft Delete
    deleted = ComplianceParameter.soft_delete(param_id)
    assert deleted is True
    assert ComplianceParameter.get_by_id(param_id)["is_active"] == 0
