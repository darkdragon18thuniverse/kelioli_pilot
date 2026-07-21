import sqlite3
from typing import Optional, List, Dict, Any
from src.app.models.base import DatabaseManager


class Department:
    @staticmethod
    def create(organization_id: int, name: str, slug: str) -> int:
        """Creates a new department linked to an organization."""
        existing = Department.get_by_slug(organization_id, slug)
        if existing:
            raise ValueError(f"Department slug '{slug}' already exists for this organization.")

        query = """
            INSERT INTO departments (organization_id, name, slug)
            VALUES (?, ?, ?);
        """
        return DatabaseManager.execute_update(query, (organization_id, name, slug))

    @staticmethod
    def get_by_id(dept_id: int) -> Optional[sqlite3.Row]:
        """Fetches a department by its primary key ID."""
        query = "SELECT * FROM departments WHERE id = ?;"
        rows = DatabaseManager.execute_query(query, (dept_id,))
        return rows[0] if rows else None

    @staticmethod
    def get_by_slug(organization_id: int, slug: str) -> Optional[sqlite3.Row]:
        """Fetches a department record by organization ID and slug."""
        query = "SELECT * FROM departments WHERE organization_id = ? AND slug = ?;"
        rows = DatabaseManager.execute_query(query, (organization_id, slug))
        return rows[0] if rows else None

    @staticmethod
    def list_by_organization(organization_id: int) -> List[sqlite3.Row]:
        """Lists all departments belonging to a specific organization."""
        query = "SELECT * FROM departments WHERE organization_id = ? ORDER BY id DESC;"
        return DatabaseManager.execute_query(query, (organization_id,))

    @staticmethod
    def update(dept_id: int, updates: Dict[str, Any]) -> bool:
        """Dynamically mutates allowed department fields."""
        allowed_keys = {"name", "slug", "status"}
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_keys and v is not None}

        if not filtered_updates:
            return False

        set_clause = ", ".join([f"{key} = ?" for key in filtered_updates.keys()])
        params = list(filtered_updates.values())
        params.append(dept_id)

        query = f"UPDATE departments SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        return DatabaseManager.execute_update(query, tuple(params)) > 0

    @staticmethod
    def soft_delete(dept_id: int) -> bool:
        """Soft-deletes a department by updating status to inactive."""
        query = "UPDATE departments SET status = 'inactive', updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        return DatabaseManager.execute_update(query, (dept_id,)) > 0
