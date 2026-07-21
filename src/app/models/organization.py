import sqlite3
from typing import Optional, List, Dict, Any
from src.app.models.base import DatabaseManager

class Organization:
    """
    Handles corporate tenant state logic. 
    Protects downstream analytical records using a safe soft-delete state suspension model.
    """

    @staticmethod
    def create(name: str, slug: str, billing_email: Optional[str] = None, tier: str = "free", 
               per_minute_cost: float = 0.0, infra_fixed_cost: float = 0.0) -> int:
        """
        Creates a new tenant organization. 
        If a matching slug exists in a suspended state, it is automatically reactivated 
        and synchronized with the new settings.
        """
        slug_clean = slug.lower().strip()
        existing = Organization.get_by_slug(slug_clean)
        
        if existing:
            if existing["status"] != "active":
                update_query = """
                    UPDATE organizations 
                    SET name = ?, billing_email = ?, status = 'active', tier = ?,
                        per_minute_cost = ?, infra_fixed_cost = ?
                    WHERE id = ?;
                """
                DatabaseManager.execute_update(
                    update_query, 
                    (name.strip(), billing_email, tier, per_minute_cost, infra_fixed_cost, existing["id"])
                )
                return existing["id"]
            else:
                raise ValueError(f"Active tenant collision: An active company using '{slug_clean}' already exists.")

        insert_query = """
            INSERT INTO organizations (name, slug, billing_email, tier, per_minute_cost, infra_fixed_cost)
            VALUES (?, ?, ?, ?, ?, ?);
        """
        return DatabaseManager.execute_update(
            insert_query, 
            (name.strip(), slug_clean, billing_email, tier, per_minute_cost, infra_fixed_cost)
        )

    @staticmethod
    def get_by_id(org_id: int) -> Optional[sqlite3.Row]:
        """Fetch tenant infrastructure records by primary tracking ID."""
        rows = DatabaseManager.execute_query("SELECT * FROM organizations WHERE id = ?;", (org_id,))
        return rows[0] if rows else None

    @staticmethod
    def get_by_slug(slug: str) -> Optional[sqlite3.Row]:
        """Fetch tenant configurations via unique URL slug identifiers."""
        rows = DatabaseManager.execute_query("SELECT * FROM organizations WHERE slug = ?;", (slug.lower().strip(),))
        return rows[0] if rows else None

    @staticmethod
    def list_active() -> List[sqlite3.Row]:
        """Fetch all operational, un-suspended corporate accounts executing on the engine."""
        return DatabaseManager.execute_query("SELECT * FROM organizations WHERE status = 'active' ORDER BY id DESC;")

    @staticmethod
    def list_all() -> List[sqlite3.Row]:
        """Fetch all organizations regardless of status for admin inspection."""
        return DatabaseManager.execute_query("SELECT * FROM organizations ORDER BY id DESC;")

    @staticmethod
    def update(org_id: int, updates: Dict[str, Any]) -> bool:
        """Dynamically updates allowed organization fields."""
        allowed_fields = ["name", "billing_email", "tier", "per_minute_cost", "infra_fixed_cost", "status", "daily_limit_minutes"]
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}

        if not filtered_updates:
            return False

        set_clause = ", ".join([f"{key} = ?" for key in filtered_updates.keys()])
        values = list(filtered_updates.values())
        values.append(org_id)

        query = f"UPDATE organizations SET {set_clause} WHERE id = ?;"
        return DatabaseManager.execute_update(query, tuple(values)) > 0

    @staticmethod
    def update_routing(org_id: int, stt_model: str, llm_model: str) -> bool:
        """Modify operational infrastructure models assigned to process this tenant's audio metrics."""
        query = "UPDATE organizations SET stt_model_routing = ?, llm_model_routing = ? WHERE id = ?;"
        return DatabaseManager.execute_update(query, (stt_model, llm_model, org_id)) > 0

    @staticmethod
    def soft_delete(org_id: int) -> bool:
        """Suspends the organization without destructive drops."""
        return DatabaseManager.execute_update("UPDATE organizations SET status = 'suspended' WHERE id = ?;", (org_id,)) > 0