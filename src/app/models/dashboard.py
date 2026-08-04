import sqlite3
from typing import Any, Dict, List, Optional
from src.app.models.base import DatabaseManager

SEVERITY_LEVELS = ("critical", "high", "medium", "low")


class Dashboard:
    """
    Read-only multi-query aggregation layer for the org-admin "Performance &
    Compliance" dashboard (GET /api/v1/admin/dashboard). Every method here is a
    pure SQL aggregation scoped to (organization_id, date range) — no RBAC, no
    period-label logic; that lives in the controller. Mirrors the structure of
    models/billing.py (many small, single-purpose static aggregation queries).

    Critical invariant enforced everywhere an average is computed: every AVG()
    over compliance_score_percentage filters
    `processing_status = 'completed' AND compliance_score_percentage IS NOT NULL`.
    The column defaults to 0.0 for pending calls and is NULL for failed calls
    and for completed calls whose department had zero active rules at eval
    time — trusting the raw column average would silently pull every score
    towards a wrong, deflated number.
    """

    # ------------------------------------------------------------------
    # KPI helpers
    # ------------------------------------------------------------------
    @staticmethod
    def get_avg_score_and_count(organization_id: int, start: str, end: str) -> Dict[str, Any]:
        query = """
            SELECT AVG(compliance_score_percentage) AS avg_score, COUNT(*) AS call_count
            FROM calls
            WHERE organization_id = ?
              AND processing_status = 'completed'
              AND compliance_score_percentage IS NOT NULL
              AND date(created_at) BETWEEN ? AND ?;
        """
        row = DatabaseManager.execute_query(query, (organization_id, start, end))[0]
        avg_score = row["avg_score"]
        return {
            "avg_score": round(float(avg_score), 2) if avg_score is not None else None,
            "call_count": int(row["call_count"] or 0),
        }

    @staticmethod
    def get_calls_audited_count(organization_id: int, start: str, end: str) -> int:
        query = """
            SELECT COUNT(*) AS c FROM calls
            WHERE organization_id = ? AND processing_status = 'completed'
              AND date(created_at) BETWEEN ? AND ?;
        """
        row = DatabaseManager.execute_query(query, (organization_id, start, end))[0]
        return int(row["c"] or 0)

    @staticmethod
    def get_minutes_processed(organization_id: int, start: str, end: str) -> float:
        query = """
            SELECT COALESCE(SUM(duration_seconds), 0.0) / 60.0 AS minutes
            FROM calls
            WHERE organization_id = ? AND processing_status = 'completed'
              AND date(created_at) BETWEEN ? AND ?;
        """
        row = DatabaseManager.execute_query(query, (organization_id, start, end))[0]
        return round(float(row["minutes"] or 0.0), 2)

    @staticmethod
    def get_failure_stats_for_severity(organization_id: int, severity_level: str, start: str, end: str) -> Dict[str, Any]:
        query = """
            SELECT COUNT(*) AS failure_count,
                   COUNT(DISTINCT ce.parameter_id) AS rule_count,
                   COUNT(DISTINCT c.user_id) AS agent_count
            FROM call_evaluations ce
            JOIN calls c ON ce.call_id = c.id
            JOIN compliance_parameters cp ON ce.parameter_id = cp.id
            WHERE c.organization_id = ? AND ce.did_follow_rule = 0 AND cp.severity_level = ?
              AND date(c.created_at) BETWEEN ? AND ?;
        """
        row = DatabaseManager.execute_query(query, (organization_id, severity_level, start, end))[0]
        return {
            "failure_count": int(row["failure_count"] or 0),
            "rule_count": int(row["rule_count"] or 0),
            "agent_count": int(row["agent_count"] or 0),
        }

    @staticmethod
    def get_severity_breakdown(organization_id: int, start: str, end: str) -> List[Dict[str, Any]]:
        breakdown = []
        for severity in SEVERITY_LEVELS:
            stats = Dashboard.get_failure_stats_for_severity(organization_id, severity, start, end)
            breakdown.append({
                "severity_level": severity,
                "failure_count": stats["failure_count"],
                "rule_count": stats["rule_count"],
                "agent_count": stats["agent_count"],
            })
        return breakdown

    # ------------------------------------------------------------------
    # Score trend (weekly)
    # ------------------------------------------------------------------
    @staticmethod
    def get_score_trend(organization_id: int, weeks: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        weeks: list of {"label": "W1", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} in
        chronological (oldest-first) order.
        """
        trend = []
        for week in weeks:
            stats = Dashboard.get_avg_score_and_count(organization_id, week["start"], week["end"])
            trend.append({
                "week_label": week["label"],
                "week_start": week["start"],
                "avg_score": stats["avg_score"],
                "call_count": stats["call_count"],
            })
        return trend

    # ------------------------------------------------------------------
    # Rules by failure rate
    # ------------------------------------------------------------------
    @staticmethod
    def _rule_failure_counts(organization_id: int, start: str, end: str) -> Dict[int, Dict[str, int]]:
        query = """
            SELECT ce.parameter_id AS parameter_id,
                   SUM(CASE WHEN ce.did_follow_rule = 0 THEN 1 ELSE 0 END) AS failed_count,
                   COUNT(*) AS checked_count
            FROM call_evaluations ce
            JOIN calls c ON ce.call_id = c.id
            WHERE c.organization_id = ? AND date(c.created_at) BETWEEN ? AND ?
            GROUP BY ce.parameter_id;
        """
        rows = DatabaseManager.execute_query(query, (organization_id, start, end))
        return {
            int(r["parameter_id"]): {
                "failed_count": int(r["failed_count"] or 0),
                "checked_count": int(r["checked_count"] or 0),
            }
            for r in rows
        }

    @staticmethod
    def get_rules_by_failure_rate(organization_id: int, start: str, end: str,
                                   prev_start: str, prev_end: str) -> List[Dict[str, Any]]:
        params_query = """
            SELECT cp.id AS parameter_id, cp.parameter_name, cp.department_id,
                   d.name AS department_name, cp.severity_level
            FROM compliance_parameters cp
            JOIN departments d ON cp.department_id = d.id
            WHERE cp.organization_id = ? AND cp.is_active = 1;
        """
        params_rows = DatabaseManager.execute_query(params_query, (organization_id,))
        current_counts = Dashboard._rule_failure_counts(organization_id, start, end)
        prev_counts = Dashboard._rule_failure_counts(organization_id, prev_start, prev_end)

        result = []
        for p in params_rows:
            pid = int(p["parameter_id"])
            cur = current_counts.get(pid, {"failed_count": 0, "checked_count": 0})
            prev = prev_counts.get(pid, {"failed_count": 0, "checked_count": 0})
            failure_rate = round((cur["failed_count"] / cur["checked_count"]) * 100, 2) if cur["checked_count"] > 0 else 0.0
            prev_failure_rate = round((prev["failed_count"] / prev["checked_count"]) * 100, 2) if prev["checked_count"] > 0 else 0.0
            result.append({
                "parameter_id": pid,
                "parameter_name": p["parameter_name"],
                "department_id": int(p["department_id"]),
                "department_name": p["department_name"],
                "severity_level": p["severity_level"],
                "failed_count": cur["failed_count"],
                "checked_count": cur["checked_count"],
                "failure_rate": failure_rate,
                "failure_rate_delta": round(failure_rate - prev_failure_rate, 2),
            })
        result.sort(key=lambda r: r["failure_rate"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # Agent performance
    # ------------------------------------------------------------------
    @staticmethod
    def get_agent_performance(organization_id: int, start: str, end: str) -> List[Dict[str, Any]]:
        agents_query = """
            SELECT u.id AS user_id, u.name AS name, u.department_id AS department_id,
                   d.name AS department_name,
                   COUNT(CASE WHEN c.processing_status = 'completed' THEN c.id END) AS calls_count,
                   AVG(CASE WHEN c.processing_status = 'completed'
                             AND c.compliance_score_percentage IS NOT NULL
                        THEN c.compliance_score_percentage END) AS avg_score
            FROM users u
            LEFT JOIN departments d ON u.department_id = d.id
            LEFT JOIN calls c ON c.user_id = u.id AND date(c.created_at) BETWEEN ? AND ?
            WHERE u.organization_id = ? AND u.role_id = 4
            GROUP BY u.id
            ORDER BY u.name ASC;
        """
        agent_rows = DatabaseManager.execute_query(agents_query, (start, end, organization_id))

        critical_query = """
            SELECT c.user_id AS user_id, COUNT(*) AS critical_count
            FROM call_evaluations ce
            JOIN calls c ON ce.call_id = c.id
            JOIN compliance_parameters cp ON ce.parameter_id = cp.id
            WHERE c.organization_id = ? AND ce.did_follow_rule = 0 AND cp.severity_level = 'critical'
              AND c.user_id IS NOT NULL AND date(c.created_at) BETWEEN ? AND ?
            GROUP BY c.user_id;
        """
        critical_rows = DatabaseManager.execute_query(critical_query, (organization_id, start, end))
        critical_map = {int(r["user_id"]): int(r["critical_count"] or 0) for r in critical_rows}

        active_rules_query = """
            SELECT department_id, COUNT(*) AS active_rule_count
            FROM compliance_parameters
            WHERE organization_id = ? AND is_active = 1
            GROUP BY department_id;
        """
        active_rows = DatabaseManager.execute_query(active_rules_query, (organization_id,))
        active_rule_map = {int(r["department_id"]): int(r["active_rule_count"] or 0) for r in active_rows}

        performance = []
        for a in agent_rows:
            dept_id = a["department_id"]
            is_scored = bool(dept_id is not None and active_rule_map.get(int(dept_id), 0) > 0)
            avg_score = a["avg_score"]
            performance.append({
                "user_id": int(a["user_id"]),
                "name": a["name"],
                "department_id": int(dept_id) if dept_id is not None else None,
                "department_name": a["department_name"],
                "calls_count": int(a["calls_count"] or 0),
                # A department with zero active rules can never legitimately score a call
                # (compliance_score_percentage is NULL by design in that case) — force null
                # here too rather than trust whatever the AVG() happened to compute.
                "avg_score": round(float(avg_score), 2) if (is_scored and avg_score is not None) else None,
                "critical_count": critical_map.get(int(a["user_id"]), 0),
                "is_scored": is_scored,
            })
        return performance

    # ------------------------------------------------------------------
    # Critical failures feed
    # ------------------------------------------------------------------
    @staticmethod
    def get_critical_failures_feed(organization_id: int, start: str, end: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = """
            SELECT c.id AS call_id, cp.parameter_name AS parameter_name,
                   ce.failed_line_text AS failed_line_text,
                   ce.failure_offset_seconds AS failure_offset_seconds,
                   u.name AS agent_name, d.name AS department_name, c.created_at AS created_at
            FROM call_evaluations ce
            JOIN calls c ON ce.call_id = c.id
            JOIN compliance_parameters cp ON ce.parameter_id = cp.id
            LEFT JOIN users u ON c.user_id = u.id
            LEFT JOIN departments d ON c.department_id = d.id
            WHERE c.organization_id = ? AND ce.did_follow_rule = 0 AND cp.severity_level = 'critical'
              AND date(c.created_at) BETWEEN ? AND ?
            ORDER BY c.created_at DESC, ce.id DESC
            LIMIT ?;
        """
        rows = DatabaseManager.execute_query(query, (organization_id, start, end, limit))
        return [
            {
                "call_id": int(r["call_id"]),
                "parameter_name": r["parameter_name"],
                "failed_line_text": r["failed_line_text"],
                "failure_offset_seconds": r["failure_offset_seconds"],
                "agent_name": r["agent_name"],
                "department_name": r["department_name"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Topic breakdown
    # ------------------------------------------------------------------
    @staticmethod
    def get_topic_breakdown(organization_id: int, start: str, end: str, limit: int = 5) -> List[Dict[str, Any]]:
        # procedure_enquired is free-text LLM output; "N/A" and "General Inquiry" are known
        # literal fallback values and are bucketed honestly like any other topic, not dropped.
        # Case-folded via LOWER(TRIM()) for grouping since casing is unnormalized.
        query = """
            SELECT LOWER(TRIM(c.procedure_enquired)) AS topic_key,
                   MIN(c.procedure_enquired) AS topic_display,
                   COUNT(*) AS calls_count,
                   SUM(CASE WHEN failing.call_id IS NOT NULL THEN 1 ELSE 0 END) AS failed_calls_count
            FROM calls c
            LEFT JOIN (
                SELECT DISTINCT ce.call_id FROM call_evaluations ce WHERE ce.did_follow_rule = 0
            ) failing ON failing.call_id = c.id
            WHERE c.organization_id = ? AND c.processing_status = 'completed'
              AND c.procedure_enquired IS NOT NULL AND TRIM(c.procedure_enquired) != ''
              AND date(c.created_at) BETWEEN ? AND ?
            GROUP BY topic_key
            ORDER BY calls_count DESC
            LIMIT ?;
        """
        rows = DatabaseManager.execute_query(query, (organization_id, start, end, limit))
        result = []
        for r in rows:
            calls_count = int(r["calls_count"] or 0)
            failed_count = int(r["failed_calls_count"] or 0)
            failure_rate = round((failed_count / calls_count) * 100, 2) if calls_count > 0 else 0.0
            result.append({
                "topic": r["topic_display"],
                "calls_count": calls_count,
                "failure_rate": failure_rate,
            })
        return result

    # ------------------------------------------------------------------
    # Department coverage
    # ------------------------------------------------------------------
    @staticmethod
    def get_department_coverage(organization_id: int, start: str, end: str) -> List[Dict[str, Any]]:
        query = """
            SELECT d.id AS department_id, d.name AS department_name,
                   COALESCE(r.active_rule_count, 0) AS active_rule_count,
                   COALESCE(a.agent_count, 0) AS agent_count,
                   COALESCE(cl.calls_count, 0) AS calls_count,
                   cl.avg_score AS avg_score
            FROM departments d
            LEFT JOIN (
                SELECT department_id, COUNT(*) AS active_rule_count
                FROM compliance_parameters
                WHERE organization_id = ? AND is_active = 1
                GROUP BY department_id
            ) r ON r.department_id = d.id
            LEFT JOIN (
                SELECT department_id, COUNT(*) AS agent_count
                FROM users
                WHERE organization_id = ? AND role_id = 4
                GROUP BY department_id
            ) a ON a.department_id = d.id
            LEFT JOIN (
                SELECT department_id, COUNT(*) AS calls_count,
                       AVG(CASE WHEN compliance_score_percentage IS NOT NULL THEN compliance_score_percentage END) AS avg_score
                FROM calls
                WHERE organization_id = ? AND processing_status = 'completed'
                  AND date(created_at) BETWEEN ? AND ?
                GROUP BY department_id
            ) cl ON cl.department_id = d.id
            WHERE d.organization_id = ?
            ORDER BY d.name ASC;
        """
        rows = DatabaseManager.execute_query(
            query, (organization_id, organization_id, organization_id, start, end, organization_id)
        )
        result = []
        for r in rows:
            active_rule_count = int(r["active_rule_count"] or 0)
            avg_score = r["avg_score"]
            result.append({
                "department_id": int(r["department_id"]),
                "department_name": r["department_name"],
                "active_rule_count": active_rule_count,
                "agent_count": int(r["agent_count"] or 0),
                "calls_count": int(r["calls_count"] or 0),
                "avg_score": round(float(avg_score), 2) if avg_score is not None else None,
                "is_covered": active_rule_count > 0,
            })
        return result

    # ------------------------------------------------------------------
    # Processing health
    # ------------------------------------------------------------------
    @staticmethod
    def get_processing_health(organization_id: int, start: str, end: str) -> Dict[str, Any]:
        counts_query = """
            SELECT
                SUM(CASE WHEN processing_status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN processing_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN processing_status IN ('transcribing', 'evaluating') THEN 1 ELSE 0 END) AS in_flight,
                SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM calls
            WHERE organization_id = ? AND date(created_at) BETWEEN ? AND ?;
        """
        row = DatabaseManager.execute_query(counts_query, (organization_id, start, end))[0]

        errors_query = """
            SELECT error_message AS message, COUNT(*) AS count
            FROM calls
            WHERE organization_id = ? AND processing_status = 'failed' AND error_message IS NOT NULL
              AND date(created_at) BETWEEN ? AND ?
            GROUP BY error_message
            ORDER BY COUNT(*) DESC
            LIMIT 5;
        """
        error_rows = DatabaseManager.execute_query(errors_query, (organization_id, start, end))

        return {
            "completed": int(row["completed"] or 0),
            "pending": int(row["pending"] or 0),
            "in_flight": int(row["in_flight"] or 0),
            "failed": int(row["failed"] or 0),
            "top_errors": [{"message": r["message"], "count": int(r["count"] or 0)} for r in error_rows],
        }

    # ------------------------------------------------------------------
    # Misc counts
    # ------------------------------------------------------------------
    @staticmethod
    def get_agents_total_count(organization_id: int) -> int:
        query = "SELECT COUNT(*) AS c FROM users WHERE organization_id = ? AND role_id = 4;"
        row = DatabaseManager.execute_query(query, (organization_id,))[0]
        return int(row["c"] or 0)
