import io
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter


def _provision_admin(client, org_name="CSV Test Org", org_slug="csv-test-org", dept_name="ICU", dept_slug="icu", email=None):
    if not email:
        email = f"csvadmin_{org_slug}@test.com"
    org_id = Organization.create(name=org_name, slug=org_slug, billing_email=email)
    dept_id = Department.create(organization_id=org_id, name=dept_name, slug=dept_slug)
    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Verify Identity", rule_description="Ask for full name.",
        severity_level="critical"
    )
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="CSV Admin", email=email, password_raw="Password2026!"
    )
    login_res = client.post("/api/v1/auth/login", data={"username": email, "password": "Password2026!"})
    token = login_res.json()["access_token"]
    return token, org_id, dept_id


def test_csv_batch_processes_valid_rows_with_mocked_ai(client):
    """A well-formed CSV batch processes successfully using mocked STT/LLM responses."""
    token, org_id, dept_id = _provision_admin(client)

    csv_content = f"organization_id,department_id,user_id,audio_url\n{org_id},{dept_id},,mock_audio.wav\n"
    csv_file = io.BytesIO(csv_content.encode("utf-8"))

    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", csv_file, "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED
    data = res.json()
    assert data["processed_records"] == 1
    assert data["failed_records"] == 0
    assert data["batch_status"] == "completed"


def test_csv_batch_row_missing_department_fails_without_silent_fallback(client):
    """A row missing department_id is marked failed, NOT silently defaulted to department_id=1."""
    token, org_id, dept_id = _provision_admin(client, org_slug="missing-dept")

    csv_content = f"organization_id,department_id,user_id,audio_url\n{org_id},,,mock_audio.wav\n"
    csv_file = io.BytesIO(csv_content.encode("utf-8"))

    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", csv_file, "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED
    data = res.json()
    assert data["processed_records"] == 0
    assert data["failed_records"] == 1


def test_csv_batch_duplicate_file_hash_rejected(client):
    """Uploading the exact same CSV content twice while the first is still processing/completed is blocked."""
    token, org_id, dept_id = _provision_admin(client, org_slug="dup-hash")

    csv_content = f"organization_id,department_id,user_id,audio_url\n{org_id},{dept_id},,mock_audio.wav\n"

    first_res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert first_res.status_code == status.HTTP_202_ACCEPTED

    second_res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert second_res.status_code == status.HTTP_409_CONFLICT


def test_csv_batch_manager_cannot_process_rows_outside_own_department(client):
    """Manager-scoped batch upload skips (fails) rows for a different department than their own."""
    org_id = Organization.create(name="Manager CSV Org", slug="manager-csv-org")
    dept_a = Department.create(organization_id=org_id, name="Dept A", slug="dept-a")
    dept_b = Department.create(organization_id=org_id, name="Dept B", slug="dept-b")

    User.create(
        role_id=3, organization_id=org_id, department_id=dept_a,
        name="Manager A", email="mgra@test.com", password_raw="Password2026!"
    )
    login_res = client.post("/api/v1/auth/login", data={"username": "mgra@test.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    csv_content = f"organization_id,department_id,user_id,audio_url\n{org_id},{dept_b},,mock_audio.wav\n"
    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED
    data = res.json()
    assert data["processed_records"] == 0
    assert data["failed_records"] == 1


def test_list_csv_uploads_and_detail_success(client):
    """Verifies listing batch upload history and single detail fetch with all expected fields."""
    token, org_id, dept_id = _provision_admin(client, org_name="History Org", org_slug="history-org")

    csv_content = f"organization_id,department_id,user_id,audio_url\n{org_id},{dept_id},,mock_audio.wav\n"
    process_res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("history_batch.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert process_res.status_code == status.HTTP_202_ACCEPTED
    upload_id = process_res.json()["csv_upload_id"]

    # Test GET /api/v1/csv-uploads
    list_res = client.get(
        f"/api/v1/csv-uploads?organization_id={org_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert list_res.status_code == status.HTTP_200_OK
    list_data = list_res.json()
    assert "csv_uploads" in list_data
    assert len(list_data["csv_uploads"]) == 1

    item = list_data["csv_uploads"][0]
    assert item["id"] == upload_id
    assert item["organization_id"] == org_id
    assert "user_id" in item
    assert item["filename"] == "history_batch.csv"
    assert "file_hash" in item
    assert item["total_records"] == 1
    assert item["processed_records"] == 1
    assert item["failed_records"] == 0
    assert item["status"] == "completed"
    assert "created_at" in item

    # Test GET /api/v1/csv-uploads/{id}
    detail_res = client.get(
        f"/api/v1/csv-uploads/{upload_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert detail_res.status_code == status.HTTP_200_OK
    detail_data = detail_res.json()
    assert detail_data["id"] == upload_id
    assert detail_data["filename"] == "history_batch.csv"
    assert detail_data["organization_id"] == org_id


def test_csv_uploads_rbac_cross_tenant_access_denied(client):
    """Non-superadmin cannot access csv uploads list or details for another organization."""
    token_org1, org1_id, dept1_id = _provision_admin(client, org_name="Org 1", org_slug="org-1")
    token_org2, org2_id, dept2_id = _provision_admin(client, org_name="Org 2", org_slug="org-2")

    csv_content = f"organization_id,department_id,user_id,audio_url\n{org1_id},{dept1_id},,mock_audio.wav\n"
    process_res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("tenant1.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token_org1}"}
    )
    upload_id = process_res.json()["csv_upload_id"]

    # Org 2 admin attempting to list Org 1 uploads -> 403 Forbidden
    res_list = client.get(
        f"/api/v1/csv-uploads?organization_id={org1_id}",
        headers={"Authorization": f"Bearer {token_org2}"}
    )
    assert res_list.status_code == status.HTTP_403_FORBIDDEN

    # Org 2 admin attempting to get Org 1 upload detail -> 403 Forbidden
    res_detail = client.get(
        f"/api/v1/csv-uploads/{upload_id}",
        headers={"Authorization": f"Bearer {token_org2}"}
    )
    assert res_detail.status_code == status.HTTP_403_FORBIDDEN


def test_csv_uploads_not_found_handling(client):
    """404 returned when querying non-existent org_id or non-existent upload_id."""
    token, org_id, _ = _provision_admin(client, org_name="404 Org", org_slug="org-404")

    # Non-existent org requested by org admin -> 403 (since org_id != admin's org_id)
    res_list = client.get(
        "/api/v1/csv-uploads?organization_id=999999",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_list.status_code == status.HTTP_403_FORBIDDEN

    # Superadmin user
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin CSV", email="csvsuper@test.com", password_raw="Password2026!"
    )
    super_login = client.post("/api/v1/auth/login", data={"username": "csvsuper@test.com", "password": "Password2026!"})
    super_token = super_login.json()["access_token"]

    # Superadmin querying non-existent org -> 404 Not Found
    res_super_list = client.get(
        "/api/v1/csv-uploads?organization_id=999999",
        headers={"Authorization": f"Bearer {super_token}"}
    )
    assert res_super_list.status_code == status.HTTP_404_NOT_FOUND

    # Superadmin querying non-existent upload detail -> 404 Not Found
    res_detail = client.get(
        "/api/v1/csv-uploads/999999",
        headers={"Authorization": f"Bearer {super_token}"}
    )
    assert res_detail.status_code == status.HTTP_404_NOT_FOUND


