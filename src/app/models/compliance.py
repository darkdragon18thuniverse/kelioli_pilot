import sqlite3
from typing import Optional, List, Dict, Any
from src.app.models.base import DatabaseManager


class ComplianceParameter:
    @staticmethod
    def create(organization_id: int, department_id: int, parameter_name: str, 
               rule_description: str, severity_level: str = "medium") -> int:
        """Creates a new compliance rule strictly bound to an organization and department."""
        if not department_id:
            raise ValueError("department_id is required and cannot be empty.")

        query = """
            INSERT INTO compliance_parameters (organization_id, department_id, parameter_name, rule_description, severity_level)
            VALUES (?, ?, ?, ?, ?);
        """
        return DatabaseManager.execute_update(query, (organization_id, department_id, parameter_name, rule_description, severity_level))

    @staticmethod
    def get_by_id(parameter_id: int) -> Optional[sqlite3.Row]:
        """Fetches a single compliance parameter record by ID."""
        query = "SELECT * FROM compliance_parameters WHERE id = ?;"
        rows = DatabaseManager.execute_query(query, (parameter_id,))
        return rows[0] if rows else None

    @staticmethod
    def list_by_department(organization_id: int, department_id: int) -> List[sqlite3.Row]:
        """Lists all active and inactive compliance rules belonging to a specific department."""
        query = """
            SELECT * FROM compliance_parameters 
            WHERE organization_id = ? AND department_id = ? 
            ORDER BY id DESC;
        """
        return DatabaseManager.execute_query(query, (organization_id, department_id))

    @staticmethod
    def update(parameter_id: int, updates: Dict[str, Any]) -> bool:
        """Dynamically mutates compliance rule attributes."""
        allowed_keys = {"parameter_name", "rule_description", "severity_level", "is_active"}
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_keys and v is not None}

        if not filtered_updates:
            return False

        set_clause = ", ".join([f"{key} = ?" for key in filtered_updates.keys()])
        params = list(filtered_updates.values())
        params.append(parameter_id)

        query = f"UPDATE compliance_parameters SET {set_clause} WHERE id = ?;"
        return DatabaseManager.execute_update(query, tuple(params)) > 0

    @staticmethod
    def soft_delete(parameter_id: int) -> bool:
        """Soft-deletes a compliance rule by marking is_active = 0."""
        query = "UPDATE compliance_parameters SET is_active = 0 WHERE id = ?;"
        return DatabaseManager.execute_update(query, (parameter_id,)) > 0
