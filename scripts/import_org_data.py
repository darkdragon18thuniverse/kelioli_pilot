#!/usr/bin/env python3
"""
Organization Data Import CLI Script

Reloads an organization's full relational data tree from JSON into a target database.
"""

import argparse
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

# Insertion order respecting foreign key dependencies
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


def check_pre_import_conflicts(cursor, tables_data: dict) -> dict:
    """
    Checks if any row's primary key ID from the import JSON already exists
    in the target database before any write operation is performed.
    Returns dict mapping table_name -> list of conflicting IDs.
    """
    conflicts = {}

    for table in TABLE_ORDER:
        rows = tables_data.get(table, [])
        if not rows:
            continue

        ids = [row["id"] for row in rows if "id" in row]
        if not ids:
            continue

        # Check existing IDs in target DB
        placeholders = ", ".join(["?"] * len(ids))
        query = f"SELECT id FROM {table} WHERE id IN ({placeholders})"
        cursor.execute(query, ids)
        existing = cursor.fetchall()

        if existing:
            conflicts[table] = [row["id"] for row in existing]

    return conflicts


def update_sqlite_sequence(cursor, table_name: str):
    """
    Updates sqlite_sequence counter to MAX(id) for tables with autoincrement primary keys.
    """
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
    if cursor.fetchone():
        cursor.execute(f"SELECT MAX(id) FROM {table_name}")
        max_id_row = cursor.fetchone()
        if max_id_row and max_id_row[0] is not None:
            max_id = max_id_row[0]
            cursor.execute(
                "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                (table_name, max_id),
            )


def import_organization_data(input_path: str, db_path: str = None) -> dict:
    """
    Reads JSON export file, validates conflicts, and inserts data inside a single transaction.
    Returns row count summary dict per table.
    """
    input_file = Path(input_path).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Import payload file not found: {input_file}")

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "tables" not in data or not isinstance(data["tables"], dict):
        raise ValueError("Invalid import file format: missing 'tables' object.")

    tables_data = data["tables"]

    if db_path:
        os.environ["DATABASE_PATH"] = str(Path(db_path).resolve())

    summary = {}

    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()

        # 1. Pre-import conflict validation BEFORE any writing occurs
        conflicts = check_pre_import_conflicts(cursor, tables_data)
        if conflicts:
            conflict_msgs = [f"  - {tbl}: IDs {ids}" for tbl, ids in conflicts.items()]
            raise ValueError(
                "Pre-import conflict check failed! The following primary key IDs already exist in the target database:\n"
                + "\n".join(conflict_msgs)
                + "\nImport aborted. Target database was not modified."
            )

        # 2. Sequential insertion in FK dependency order inside a single transaction
        for table in TABLE_ORDER:
            rows = tables_data.get(table, [])
            summary[table] = 0

            if not rows:
                continue

            columns = list(rows[0].keys())
            col_names = ", ".join(columns)
            placeholders = ", ".join(["?"] * len(columns))
            query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            values = [tuple(row[col] for col in columns) for row in rows]
            cursor.executemany(query, values)
            summary[table] = len(rows)

            if "id" in columns:
                update_sqlite_sequence(cursor, table)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Import organization data tree from JSON")
    parser.add_argument("--input", type=str, required=True, help="Input JSON file path")
    parser.add_argument("--db-path", type=str, help="Override database file path")

    args = parser.parse_args()

    try:
        print(f"📥 Importing organization data from {args.input}...")
        summary = import_organization_data(args.input, db_path=args.db_path)

        print("\n✨ Import completed successfully!")
        print("Row count summary:")
        total_rows = 0
        for table, count in summary.items():
            total_rows += count
            print(f"  - {table}: {count}")
        print(f"Total rows imported: {total_rows}")

    except Exception as e:
        print(f"❌ Import failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
