#!/usr/bin/env python3
"""
Organization Data Export CLI Script

Dumps an organization's full relational data tree to a JSON file.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# Setup project root import path
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.app.models.base import DatabaseManager

# Tables in scope in FK dependency order
TABLE_ORDER = [
    "organizations",
    "departments",
    "users",
    "compliance_parameters",
    "csv_uploads",
    "calls",
    "call_evaluations",
    "billing_snapshots",
    "daily_usage_metrics",
]


def export_organization_data(org_id: int, db_path: str = None) -> dict:
    """
    Query and extract all data for specified organization ID.
    Returns structured data dictionary.
    """
    if db_path:
        os.environ["DATABASE_PATH"] = str(Path(db_path).resolve())

    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()

        # 1. Verify organization exists
        cursor.execute("SELECT * FROM organizations WHERE id = ?", (org_id,))
        org_row = cursor.fetchone()
        if not org_row:
            raise ValueError(f"Organization with ID {org_id} not found in database.")

        exported_tables = {}

        # 2. Extract rows per table
        for table in TABLE_ORDER:
            if table == "organizations":
                query = "SELECT * FROM organizations WHERE id = ?"
                params = (org_id,)
            elif table == "call_evaluations":
                query = (
                    "SELECT call_evaluations.* FROM call_evaluations "
                    "JOIN calls ON call_evaluations.call_id = calls.id "
                    "WHERE calls.organization_id = ?"
                )
                params = (org_id,)
            else:
                # departments, users, compliance_parameters, csv_uploads, calls, billing_snapshots, daily_usage_metrics
                query = f"SELECT * FROM {table} WHERE organization_id = ?"
                params = (org_id,)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            exported_tables[table] = [dict(row) for row in rows]

    export_payload = {
        "organization_id": org_id,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tables": exported_tables,
    }
    return export_payload


def main():
    parser = argparse.ArgumentParser(description="Export organization data tree to JSON")
    parser.add_argument("--org-id", type=int, required=True, help="ID of organization to export")
    parser.add_argument("--output", type=str, help="Output JSON file path")
    parser.add_argument("--db-path", type=str, help="Override database file path")

    args = parser.parse_args()

    # Determine default output path if not specified
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = ROOT_DIR / "scripts" / "exports" / f"org_{args.org_id}_{timestamp}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        print(f"📦 Exporting organization {args.org_id} data...")
        data = export_organization_data(args.org_id, db_path=args.db_path)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        print(f"✅ Export successful -> {output_path}")
        print("Summary of exported rows:")
        total_rows = 0
        for table, rows in data["tables"].items():
            count = len(rows)
            total_rows += count
            print(f"  - {table}: {count}")
        print(f"Total exported rows: {total_rows}")

    except Exception as e:
        print(f"❌ Export failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
