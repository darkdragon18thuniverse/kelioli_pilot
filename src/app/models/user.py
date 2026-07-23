from typing import Optional, List, Dict, Any
import sqlite3
import bcrypt
from src.app.models.base import DatabaseManager
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.core.roles import ROLES

class User:
    """
    Handles corporate user accounts.
    Enforces relational cross-tenant alignment and soft-delete auto-reactivation.
    """

    @staticmethod
    def _hash_password(password: str) -> str:
        """Internal bcrypt hashing format for modern secure credential storage."""
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')

    @staticmethod
    def create(role_id: int, organization_id: Optional[int], department_id: Optional[int], 
               name: str, email: str, password_raw: str, status: str = "active") -> int:
        """
        Creates a user account. Enforces structural constraints based on role mappings.
        If a user with this email exists in a non-active state, it reactivates them and updates settings.
        """
        email_clean = email.lower().strip()
        pwd_hash = User._hash_password(password_raw)

        # 1. Tenant Boundary Structural Checks & Guard Clauses
        if role_id != ROLES["superadmin"] and not organization_id:
            raise ValueError("Relational Violation: Non-superadmin records must specify a valid organization mapping.")
            
        if role_id == ROLES["agent"] and not department_id:
            raise ValueError("Relational Violation: Agent accounts must be assigned to an active department framework.")

        if organization_id:
            org = Organization.get_by_id(organization_id)
            if not org or org["status"] != "active":
                raise ValueError("Operation Denied: Target organization is suspended or non-existent.")
            
            if department_id:
                dept = Department.get_by_id(department_id)
                if not dept or dept["organization_id"] != organization_id or dept["status"] != "active":
                    raise ValueError("Operation Denied: Department does not belong to organization or is inactive.")

        # 2. Duplicate Check for Soft-Delete Reactivation Flow
        existing = User.get_by_email(email_clean)
        if existing:
            if existing["status"] != "active":
                update_query = """
                    UPDATE users 
                    SET role_id = ?, organization_id = ?, department_id = ?, 
                        name = ?, password_hash = ?, status = ?
                    WHERE id = ?;
                """
                DatabaseManager.execute_update(
                    update_query, 
                    (role_id, organization_id, department_id, name.strip(), pwd_hash, status, existing["id"])
                )
                return existing["id"]
            else:
                raise ValueError(f"Account Conflict: An active user with email '{email_clean}' already exists.")

        # 3. Fresh Record Insertion
        insert_query = """
            INSERT INTO users (role_id, organization_id, department_id, name, email, password_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        return DatabaseManager.execute_update(
            insert_query, 
            (role_id, organization_id, department_id, name.strip(), email_clean, pwd_hash, status)
        )

    @staticmethod
    def get_by_id(user_id: int) -> Optional[sqlite3.Row]:
        """Fetch user record profile details using primary tracking ID."""
        rows = DatabaseManager.execute_query("SELECT * FROM users WHERE id = ?;", (user_id,))
        return rows[0] if rows else None

    @staticmethod
    def get_by_id_with_relations(user_id: int) -> Optional[sqlite3.Row]:
        """Fetch user record joined with organization, department, and role display names."""
        query = """
            SELECT u.id, u.role_id, u.organization_id, u.department_id, u.name, u.email, u.status, u.created_at,
                   o.name AS organization_name, d.name AS department_name, r.name AS role_name
            FROM users u
            LEFT JOIN organizations o ON u.organization_id = o.id
            LEFT JOIN departments d ON u.department_id = d.id
            LEFT JOIN roles r ON u.role_id = r.id
            WHERE u.id = ?;
        """
        rows = DatabaseManager.execute_query(query, (user_id,))
        return rows[0] if rows else None

    @staticmethod
    def get_by_email(email: str) -> Optional[sqlite3.Row]:
        """Look up unique email accounts to run validation check passes."""
        rows = DatabaseManager.execute_query("SELECT * FROM users WHERE email = ?;", (email.lower().strip(),))
        return rows[0] if rows else None

    @staticmethod
    def verify_credentials(email: str, password_raw: str) -> Optional[sqlite3.Row]:
        """Authenticates user email and checks raw input against bcrypt hashes for active accounts."""
        email_clean = email.lower().strip()
        user = User.get_by_email(email_clean)
        
        if user and user["status"] == "active":
            password_bytes = password_raw.encode('utf-8')
            hashed_bytes = user["password_hash"].encode('utf-8')
            if bcrypt.checkpw(password_bytes, hashed_bytes):
                return user
        return None

    @staticmethod
    def list_all_with_relations(role_id: Optional[int] = None, organization_id: Optional[int] = None) -> List[sqlite3.Row]:
        """Fetch users joined with organization and department names, with optional role/tenant filtering."""
        query = """
            SELECT u.id, u.role_id, u.organization_id, u.department_id, u.name, u.email, u.status, u.created_at,
                   o.name AS organization_name, d.name AS department_name, r.name AS role_name
            FROM users u
            LEFT JOIN organizations o ON u.organization_id = o.id
            LEFT JOIN departments d ON u.department_id = d.id
            LEFT JOIN roles r ON u.role_id = r.id
            WHERE 1=1
        """
        params: List[Any] = []
        if role_id is not None:
            query += " AND u.role_id = ?"
            params.append(role_id)
        if organization_id is not None:
            query += " AND u.organization_id = ?"
            params.append(organization_id)

        query += " ORDER BY u.id DESC;"
        return DatabaseManager.execute_query(query, tuple(params))

    @staticmethod
    def list_by_organization(organization_id: int) -> List[sqlite3.Row]:
        """Fetch active users bound inside an isolated company layout context."""
        query = "SELECT * FROM users WHERE organization_id = ? AND status = 'active' ORDER BY name ASC;"
        return DatabaseManager.execute_query(query, (organization_id,))

    @staticmethod
    def update(user_id: int, updates: Dict[str, Any]) -> bool:
        """Dynamically updates allowed user fields with password re-hashing support."""
        allowed_fields = ["role_id", "organization_id", "department_id", "name", "email", "status", "password_raw"]
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}

        if not filtered_updates:
            return False

        if "password_raw" in filtered_updates:
            filtered_updates["password_hash"] = User._hash_password(filtered_updates.pop("password_raw"))

        if "email" in filtered_updates:
            filtered_updates["email"] = filtered_updates["email"].lower().strip()

        if "name" in filtered_updates:
            filtered_updates["name"] = filtered_updates["name"].strip()

        set_clause = ", ".join([f"{key} = ?" for key in filtered_updates.keys()])
        values = list(filtered_updates.values())
        values.append(user_id)

        query = f"UPDATE users SET {set_clause} WHERE id = ?;"
        return DatabaseManager.execute_update(query, tuple(values)) > 0

    @staticmethod
    def soft_delete(user_id: int) -> bool:
        """Toggles user status to 'suspended' to safely maintain historical record continuity."""
        return DatabaseManager.execute_update("UPDATE users SET status = 'suspended' WHERE id = ?;", (user_id,)) > 0

    @staticmethod
    def suspend_by_department(department_id: int) -> int:
        """Suspends all user accounts assigned to a specific department."""
        query = "UPDATE users SET status = 'suspended', updated_at = CURRENT_TIMESTAMP WHERE department_id = ?;"
        return DatabaseManager.execute_update(query, (department_id,))