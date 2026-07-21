import sqlite3
from typing import Optional, List
from src.app.models.base import DatabaseManager

class Role:
    """
    Read-Only Role model for Role-Based Access Control (RBAC).
    Guarantees structural platform tiers remain unalterable at runtime.
    """

    @staticmethod
    def get_by_id(role_id: int) -> Optional[sqlite3.Row]:
        """Fetch a specific role profile by its primary database index."""
        query = "SELECT id, name, description, created_at FROM roles WHERE id = ?;"
        rows = DatabaseManager.execute_query(query, (role_id,))
        return rows[0] if rows else None

    @staticmethod
    def get_by_name(name: str) -> Optional[sqlite3.Row]:
        """Fetch a specific role configuration by its strict token key."""
        query = "SELECT id, name, description, created_at FROM roles WHERE name = ?;"
        rows = DatabaseManager.execute_query(query, (name.lower().strip(),))
        return rows[0] if rows else None

    @staticmethod
    def list_all() -> List[sqlite3.Row]:
        """Retrieve all operational roles mapped inside the engine."""
        query = "SELECT id, name, description, created_at FROM roles ORDER BY id ASC;"
        return DatabaseManager.execute_query(query)

    @staticmethod
    def ensure_seeded() -> None:
        """
        Idempotent runtime bootstrapper. Checks if roles are missing 
        and populates them automatically using a high-performance C-level batch.
        """
        # Quick guard check to see if seeding is already done
        if len(Role.list_all()) == 4:
            return

        default_roles = [
            ("superadmin", "Global system overseer. Cross-tenant administration."),
            ("admin", "Tenant administrator. Full control over organization boundaries."),
            ("manager", "Department manager. Evaluates workflows and analytics dashboards."),
            ("agent", "Frontline operative. Executes individual customer interactions.")
        ]
        
        insert_query = """
            INSERT INTO roles (name, description) 
            VALUES (?, ?) 
            ON CONFLICT(name) DO NOTHING;
        """
        
        with DatabaseManager.get_connection() as conn:
            conn.executemany(insert_query, default_roles)