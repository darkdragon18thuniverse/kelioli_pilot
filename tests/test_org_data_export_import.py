import json
import os
import sqlite3
import pytest
from pathlib import Path

from src.app.core.database import init_database
from src.app.models.base import DatabaseManager
from scripts.export_org_data import export_organization_data
from scripts.import_org_data import import_organization_data, check_pre_import_conflicts


@pytest.fixture
def seeded_db(tmp_path):
    """
    Creates a temporary SQLite DB populated with a full organization data tree.
    """
    db_file = str(tmp_path / "seeded_source.db")
    os.environ["DATABASE_PATH"] = db_file
    init_database()

    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()

        # Seed organization
        cursor.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (10, 'Acme Corp', 'acme-corp')"
        )

        # Seed department
        cursor.execute(
            "INSERT INTO departments (id, organization_id, name, slug) VALUES (101, 10, 'Support', 'support')"
        )

        # Seed user
        cursor.execute(
            "INSERT INTO users (id, role_id, organization_id, department_id, name, email, password_hash) "
            "VALUES (1001, 4, 10, 101, 'Agent Alice', 'alice@acme.com', 'hashed_pass')"
        )

        # Seed compliance parameter
        cursor.execute(
            "INSERT INTO compliance_parameters (id, organization_id, department_id, parameter_name, rule_description) "
            "VALUES (501, 10, 101, 'Greeting Check', 'Must say hello')"
        )

        # Seed CSV upload
        cursor.execute(
            "INSERT INTO csv_uploads (id, organization_id, user_id, filename) "
            "VALUES (201, 10, 1001, 'batch_01.csv')"
        )

        # Seed call
        cursor.execute(
            "INSERT INTO calls (id, organization_id, department_id, user_id, csv_upload_id, audio_url, duration_seconds) "
            "VALUES (3001, 10, 101, 1001, 201, 'https://storage/call1.wav', 120.5)"
        )

        # Seed call evaluation
        cursor.execute(
            "INSERT INTO call_evaluations (id, call_id, parameter_id, did_follow_rule, failure_reason) "
            "VALUES (4001, 3001, 501, 1, NULL)"
        )

        # Seed billing snapshot
        cursor.execute(
            "INSERT INTO billing_snapshots (id, organization_id, tier_at_billing, infra_fixed_cost_charged, "
            "per_minute_cost_charged, total_minutes_consumed, total_spend_calculated, billing_period_start, billing_period_end) "
            "VALUES (701, 10, 'free', 0.0, 0.0, 120.5, 0.0, '2026-07-01', '2026-07-31')"
        )

        # Seed daily usage metrics
        cursor.execute(
            "INSERT INTO daily_usage_metrics (id, organization_id, department_id, user_id, usage_date, total_minutes, total_calls_processed) "
            "VALUES (801, 10, 101, 1001, '2026-07-23', 120.5, 1)"
        )

    return db_file


def test_export_org_data(seeded_db, tmp_path):
    output_file = str(tmp_path / "export_org_10.json")

    # Run export
    exported = export_organization_data(org_id=10, db_path=seeded_db)

    assert exported["organization_id"] == 10
    tables = exported["tables"]

    assert len(tables["organizations"]) == 1
    assert tables["organizations"][0]["id"] == 10
    assert len(tables["departments"]) == 1
    assert tables["departments"][0]["id"] == 101
    assert len(tables["users"]) == 1
    assert tables["users"][0]["id"] == 1001
    assert len(tables["compliance_parameters"]) == 1
    assert tables["compliance_parameters"][0]["id"] == 501
    assert len(tables["csv_uploads"]) == 1
    assert tables["csv_uploads"][0]["id"] == 201
    assert len(tables["calls"]) == 1
    assert tables["calls"][0]["id"] == 3001
    assert len(tables["call_evaluations"]) == 1
    assert tables["call_evaluations"][0]["id"] == 4001
    assert tables["call_evaluations"][0]["call_id"] == 3001
    assert tables["call_evaluations"][0]["parameter_id"] == 501
    assert len(tables["billing_snapshots"]) == 1
    assert tables["billing_snapshots"][0]["id"] == 701
    assert len(tables["daily_usage_metrics"]) == 1
    assert tables["daily_usage_metrics"][0]["id"] == 801


def test_export_nonexistent_org(seeded_db):
    with pytest.raises(ValueError, match="Organization with ID 999 not found"):
        export_organization_data(org_id=999, db_path=seeded_db)


def test_import_conflict_detection(seeded_db, tmp_path):
    output_file = str(tmp_path / "export_org_10.json")
    exported = export_organization_data(org_id=10, db_path=seeded_db)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(exported, f)

    # Attempting to import into the same non-empty database should fail with conflict error
    with pytest.raises(ValueError, match="Pre-import conflict check failed"):
        import_organization_data(input_path=output_file, db_path=seeded_db)


def test_full_roundtrip_import(seeded_db, tmp_path):
    output_file = str(tmp_path / "export_org_10.json")
    exported = export_organization_data(org_id=10, db_path=seeded_db)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(exported, f)

    # Create fresh target database
    target_db_file = str(tmp_path / "target_fresh.db")
    os.environ["DATABASE_PATH"] = target_db_file
    init_database()

    # Perform import
    summary = import_organization_data(input_path=output_file, db_path=target_db_file)

    assert summary["organizations"] == 1
    assert summary["departments"] == 1
    assert summary["users"] == 1
    assert summary["compliance_parameters"] == 1
    assert summary["csv_uploads"] == 1
    assert summary["calls"] == 1
    assert summary["call_evaluations"] == 1
    assert summary["billing_snapshots"] == 1
    assert summary["daily_usage_metrics"] == 1

    # Verify spot-checked FK relationships and target DB data
    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM organizations WHERE id = 10")
        org = cursor.fetchone()
        assert org is not None
        assert org["name"] == "Acme Corp"

        cursor.execute("SELECT * FROM call_evaluations WHERE id = 4001")
        eval_row = cursor.fetchone()
        assert eval_row is not None
        assert eval_row["call_id"] == 3001
        assert eval_row["parameter_id"] == 501

        cursor.execute("SELECT * FROM calls WHERE id = 3001")
        call_row = cursor.fetchone()
        assert call_row is not None
        assert call_row["organization_id"] == 10
        assert call_row["department_id"] == 101
        assert call_row["user_id"] == 1001
        assert call_row["csv_upload_id"] == 201
