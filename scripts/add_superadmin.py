#!/usr/bin/env python3
import sys
import getpass
from pathlib import Path

# Align paths to resolve imports from the project root directory (one level up from scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
sys.path.append(str(ROOT_DIR))

from src.app.models.user import User
from src.app.models.role import Role

def create_superadmin_cli():
    print("👤 --- Create Global System Superadmin Account ---")
    
    try:
        Role.ensure_seeded()
    except Exception as e:
        print(f"⚠️ Warning during role integrity check: {e}")

    name = input("Enter Superadmin Name [System SuperAdmin]: ").strip() or "System SuperAdmin"
    email = input("Enter Superadmin Email: ").strip().lower()
    
    if not email:
        print("❌ Error: Email field cannot be empty.")
        sys.exit(1)
        
    password = getpass.getpass("Enter Superadmin Password: ")
    confirm_password = getpass.getpass("Confirm Superadmin Password: ")
    
    if password != confirm_password:
        print("❌ Error: Password confirmation mismatch.")
        sys.exit(1)
        
    if len(password) < 8:
        print("❌ Error: Password must be at least 8 characters long.")
        sys.exit(1)

    try:
        user_id = User.create(
            role_id=1,
            organization_id=None,
            department_id=None,
            name=name,
            email=email,
            password_raw=password
        )
        print(f"\n🚀 Success: Superadmin account successfully configured under ID: {user_id}")
        print(f"Identity Target: {email}")
        
    except ValueError as e:
        print(f"\n❌ Execution Blocked: {e}")
        sys.exit(1)

if __name__ == "__main__":
    create_superadmin_cli()