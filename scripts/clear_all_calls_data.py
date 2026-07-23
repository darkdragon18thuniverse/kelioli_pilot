#!/usr/bin/env python3
import os
import sys
import sqlite3
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DB_PATH = ROOT_DIR / "src" / "app" / "production.db"
MEDIA_DIR = ROOT_DIR / "media" / "temp_audio"

def clear_all_calls_data(skip_confirm: bool = False):
    if not DB_PATH.exists():
        print(f"❌ Error: Database file not found at {DB_PATH}")
        sys.exit(1)

    if not skip_confirm:
        print("⚠️  WARNING: This will permanently delete ALL call records, evaluations, CSV upload history, and daily usage metrics.")
        confirm = input("Are you sure you want to clear all call data? (y/N): ")
        if confirm.lower() not in ("y", "yes"):
            print("❌ Operation cancelled.")
            sys.exit(0)

    print("\n🧹 Clearing call data...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    tables_to_clear = [
        "call_evaluations",
        "calls",
        "csv_uploads",
        "daily_usage_metrics",
        "billing_snapshots"
    ]

    cleared_counts = {}

    try:
        cursor.execute("PRAGMA foreign_keys = OFF;")
        
        for table in tables_to_clear:
            # Check if table exists
            cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?;", (table,))
            if cursor.fetchone()[0] > 0:
                cursor.execute(f"SELECT count(*) FROM \"{table}\";")
                count = cursor.fetchone()[0]
                cursor.execute(f"DELETE FROM \"{table}\";")
                
                # Reset autoincrement sequence if sqlite_sequence exists
                cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='sqlite_sequence';")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("DELETE FROM sqlite_sequence WHERE name=?;", (table,))
                
                cleared_counts[table] = count
            else:
                cleared_counts[table] = 0

        conn.commit()
        cursor.execute("VACUUM;")
        conn.close()

        print("---------------------------------------------------------------------")
        for table, count in cleared_counts.items():
            print(f"  • Cleared {count} records from '{table}'")
        print("---------------------------------------------------------------------")

        # Clean up temp audio files if any exist
        if MEDIA_DIR.exists():
            cleaned_files = 0
            for item in MEDIA_DIR.iterdir():
                if item.is_file():
                    item.unlink()
                    cleaned_files += 1
            if cleaned_files > 0:
                print(f"  • Cleaned {cleaned_files} temporary audio files in {MEDIA_DIR.name}")

        print("✅ All call data and CSV upload history cleared successfully! You can test fresh calls now.")

    except Exception as e:
        print(f"❌ Error clearing call data: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clear all call data, evaluations, CSV upload history, and usage metrics.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    clear_all_calls_data(skip_confirm=args.yes)
