"""
Core Role definitions and mapping constants for Role-Based Access Control (RBAC).
"""
from typing import Dict

ROLES: Dict[str, int] = {
    "superadmin": 1,
    "admin": 2,
    "manager": 3,
    "agent": 4,
}
