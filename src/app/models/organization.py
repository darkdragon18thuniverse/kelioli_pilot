import sqlite3
from typing import Optional, List, Dict, Any
from src.app.models.base import DatabaseManager
from src.app.core.constants import (
    DEFAULT_PER_MINUTE_COST,
    DEFAULT_INFRA_FIXED_COST,
    DEFAULT_MAX_MONTHLY_MINUTES,
)


class Organization:
    @staticmethod
    def create(name: str, slug: str, billing_email: Optional[str] = None, tier: str = "free",
               stt_model_routing: str = "sarvam-2", llm_model_routing: str = "openrouter/free",
               company_context: Optional[str] = None, default_language: Optional[str] = None,
               per_minute_cost: float = DEFAULT_PER_MINUTE_COST,
               infra_fixed_cost: float = DEFAULT_INFRA_FIXED_COST,
               max_monthly_minutes: float = DEFAULT_MAX_MONTHLY_MINUTES,
               status: str = "active") -> int:
        slug_clean = slug.lower().strip()
        existing = Organization.get_by_slug(slug_clean)

        if existing:
            if existing["status"] != "active":
                update_query = """
                    UPDATE organizations
                    SET name = ?, billing_email = ?, status = ?, tier = ?,
                        stt_model_routing = ?, llm_model_routing = ?, company_context = ?,
                        default_language = ?, per_minute_cost = ?, infra_fixed_cost = ?
                    WHERE id = ?;
                """
                DatabaseManager.execute_update(
                    update_query,
                    (name.strip(), billing_email, status, tier, stt_model_routing, llm_model_routing, company_context,
                     default_language, per_minute_cost, infra_fixed_cost, existing["id"])
                )
                return existing["id"]
            return existing["id"]

        query = """
            INSERT INTO organizations (
                name, slug, billing_email, status, tier, stt_model_routing, llm_model_routing,
                company_context, default_language, per_minute_cost, infra_fixed_cost, max_monthly_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        return DatabaseManager.execute_update(
            query, (name.strip(), slug_clean, billing_email, status, tier, stt_model_routing, llm_model_routing,
                    company_context, default_language, per_minute_cost, infra_fixed_cost, max_monthly_minutes)
        )

    @staticmethod
    def get_by_id(org_id: int) -> Optional[sqlite3.Row]:
        query = "SELECT * FROM organizations WHERE id = ?;"
        rows = DatabaseManager.execute_query(query, (org_id,))
        return rows[0] if rows else None

    @staticmethod
    def get_by_slug(slug: str) -> Optional[sqlite3.Row]:
        query = "SELECT * FROM organizations WHERE slug = ?;"
        rows = DatabaseManager.execute_query(query, (slug.lower().strip(),))
        return rows[0] if rows else None

    @staticmethod
    def list_all() -> List[sqlite3.Row]:
        query = "SELECT * FROM organizations ORDER BY id DESC;"
        return DatabaseManager.execute_query(query)

    @staticmethod
    def list_active() -> List[sqlite3.Row]:
        """Lists all active organizations."""
        query = "SELECT * FROM organizations WHERE status = 'active' ORDER BY id DESC;"
        return DatabaseManager.execute_query(query)

    @staticmethod
    def soft_delete(org_id: int) -> bool:
        """Suspends an organization."""
        query = "UPDATE organizations SET status = 'suspended', updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        return DatabaseManager.execute_update(query, (org_id,)) > 0

    @staticmethod
    def update(org_id: int, updates: Dict[str, Any]) -> bool:
        allowed_keys = {
            "name", "slug", "status", "tier", "billing_email",
            "stt_model_routing", "llm_model_routing", "company_context", "default_language",
            "per_minute_cost", "infra_fixed_cost", "max_monthly_minutes"
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_keys and v is not None}
        if not filtered:
            return False

        set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
        params = list(filtered.values())
        params.append(org_id)

        query = f"UPDATE organizations SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        return DatabaseManager.execute_update(query, tuple(params)) > 0
