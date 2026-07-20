#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Align project path variables to easily reach inside the app core folder
ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "src" / "app" / "production.db"

def manual_nuke_and_reset():
    print("⚠️  MANUAL DATABASE RESET INITIATED")
    
    # 1. Target and remove the active files
    targets = [
        DB_PATH,
        Path(f"{DB_PATH}-wal"),
        Path(f"{DB_PATH}-shm")
    ]
    
    for target in targets:
        if target.exists():
            try:
                os.remove(target)
                print(f"🔥 Successfully dropped: {target.name}")
            except Exception as e:
                print(f"❌ Error deleting {target.name}: {e}")
                sys.exit(1)

    print("✨ Environment cleared.")
    
    # 2. Add local pathing context to call production initializer cleanly
    sys.path.append(str(ROOT_DIR / "src"))
    from src.app.core.database import init_database
    
    try:
        init_database()
        print("🚀 Fresh production schema successfully applied to a clean database instance!")
    except Exception as e:
        print(f"❌ Initialization script execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Simple CLI verification barrier so you never run it by accident in production
    confirm = input("This will permanently delete ALL data inside production.db. Continue? (y/N): ")
    if confirm.lower() == 'y':
        manual_nuke_and_reset()
    else:
        print("Reset aborted.")