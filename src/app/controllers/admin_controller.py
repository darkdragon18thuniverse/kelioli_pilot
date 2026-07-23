from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.base import DatabaseManager
from src.app.core.logging_config import get_logger
from src.app.core.roles import ROLES
from src.app.core.constants import (
    DEFAULT_PER_MINUTE_COST,
    DEFAULT_INFRA_FIXED_COST,
    DEFAULT_MAX_MONTHLY_MINUTES,
)

logger = get_logger(__name__)


class AdminController:
    @staticmethod
    def _verify_role(current_user: Dict[str, Any], allowed_role_ids: List[int]) -> None:
        if current_user["role_id"] not in allowed_role_ids:
            logger.warning(f"RBAC Denied in Admin: User {current_user['id']} (role_id: {current_user['role_id']}) tried operation requiring roles {allowed_role_ids}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Insufficient administrative privileges."
            )

    @staticmethod
    def create_organization(current_user: Dict[str, Any], name: str, slug: str,
                            billing_email: Optional[str] = None, tier: str = "free",
                            company_context: Optional[str] = None,
                            stt_model_routing: Optional[str] = None,
                            llm_model_routing: Optional[str] = None,
                            default_language: Optional[str] = None,
                            per_minute_cost: float = DEFAULT_PER_MINUTE_COST,
                            infra_fixed_cost: float = DEFAULT_INFRA_FIXED_COST,
                            max_monthly_minutes: float = DEFAULT_MAX_MONTHLY_MINUTES,
                            status_val: Optional[str] = None) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"]])

        if current_user.get("organization_id") is not None or current_user.get("department_id") is not None:
            logger.warning(f"Structure Mismatch: Superadmin user {current_user['id']} has org/dept assignment.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure Mismatch: Global Superadmins must not belong to specific organizations or departments."
            )

        create_kwargs: Dict[str, Any] = {
            "name": name, "slug": slug, "billing_email": billing_email, "tier": tier,
            "company_context": company_context, "default_language": default_language,
            "per_minute_cost": per_minute_cost, "infra_fixed_cost": infra_fixed_cost,
            "max_monthly_minutes": max_monthly_minutes
        }
        if status_val is not None:
            create_kwargs["status"] = status_val
        if stt_model_routing is not None:
            create_kwargs["stt_model_routing"] = stt_model_routing
        if llm_model_routing is not None:
            create_kwargs["llm_model_routing"] = llm_model_routing

        try:
            org_id = Organization.create(**create_kwargs)
            logger.info(f"Organization provisioned successfully: org_id={org_id}, name='{name}', slug='{slug}', tier='{tier}'")
            return {"status": "success", "id": org_id, "message": "Organization provisioned successfully."}
        except ValueError as e:
            logger.warning(f"Failed to provision organization '{name}': {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def get_organizations(current_user: Dict[str, Any]) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"]])

        try:
            all_rows = Organization.list_all()
            orgs_list = [dict(row) for row in all_rows] if all_rows else []
            logger.info(f"Retrieved {len(orgs_list)} total organizations for superadmin user_id={current_user['id']}")
            return {"organizations": orgs_list}
        except Exception as e:
            logger.exception(f"Error fetching organizations: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @staticmethod
    def get_organization_by_id(current_user: Dict[str, Any], org_id: int) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"]])

        org = Organization.get_by_id(org_id)
        if not org:
            logger.warning(f"Organization not found: org_id={org_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")
        logger.info(f"Retrieved details for organization org_id={org_id}")
        return dict(org)

    @staticmethod
    def update_organization(current_user: Dict[str, Any], org_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"]])

        org = Organization.get_by_id(org_id)
        if not org:
            logger.warning(f"Update failed: Organization org_id={org_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")

        updated = Organization.update(org_id, updates)
        if not updated:
            logger.warning(f"No valid fields provided for updating org_id={org_id}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        logger.info(f"Organization org_id={org_id} updated successfully (updated fields: {list(updates.keys())})")
        return {"status": "success", "message": "Organization settings updated successfully."}

    @staticmethod
    def get_admin_summary(current_user: Dict[str, Any]) -> Dict[str, Any]:
        """Aggregates metric card totals across the complete platform infrastructure safely."""
        AdminController._verify_role(current_user, [ROLES["superadmin"]])

        try:
            active_orgs = Organization.list_active()
            total_tenants = len(active_orgs) if active_orgs else 0

            try:
                all_users = User.list_all_with_relations()
                global_users = len(all_users) if all_users else 0
            except Exception:
                global_users = 0

            try:
                call_rows = DatabaseManager.execute_query("SELECT COUNT(*) as cnt FROM calls;")
                total_calls = call_rows[0]["cnt"] if call_rows else 0
            except Exception:
                total_calls = 0

            logger.info(f"Aggregated admin summary: tenants={total_tenants}, users={global_users}, calls={total_calls}")
            return {
                "total_tenants": total_tenants,
                "global_platform_users": global_users,
                "total_audited_calls": total_calls
            }
        except Exception as e:
            logger.exception(f"Error building admin summary metrics: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @staticmethod
    def create_department(current_user: Dict[str, Any], organization_id: int, name: str, slug: str,
                          department_context: Optional[str] = None) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != organization_id:
            logger.warning(f"Tenant Admin Scope Denied: User {current_user['id']} tried creating department for org_id {organization_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot create departments outside your organization."
            )

        try:
            dept_id = Department.create(
                organization_id=organization_id, name=name, slug=slug,
                department_context=department_context
            )
            logger.info(f"Department created successfully: dept_id={dept_id}, name='{name}', slug='{slug}', org_id={organization_id}")
            return {"status": "success", "id": dept_id, "message": "Department created successfully."}
        except ValueError as e:
            logger.warning(f"Failed to create department '{name}': {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def get_departments_by_organization(current_user: Dict[str, Any], organization_id: int) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != organization_id:
            logger.warning(f"Cross-tenant department list access denied for user_id={current_user['id']} requesting org_id={organization_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot access departments outside your organization."
            )

        rows = Department.list_by_organization(organization_id)
        depts_list = [dict(row) for row in rows] if rows else []
        logger.info(f"Retrieved {len(depts_list)} departments for org_id={organization_id}")
        return {"departments": depts_list}

    @staticmethod
    def get_department_by_id(current_user: Dict[str, Any], dept_id: int) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        dept = Department.get_by_id(dept_id)
        if not dept:
            logger.warning(f"Department not found: dept_id={dept_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department record not found.")

        if current_user["role_id"] == ROLES["admin"] and dept["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant department view denied for dept_id={dept_id} to user_id={current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access Denied: Cross-tenant department record access prohibited."
            )

        logger.info(f"Retrieved department details: dept_id={dept_id}")
        return dict(dept)

    @staticmethod
    def update_department(current_user: Dict[str, Any], dept_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        dept = Department.get_by_id(dept_id)
        if not dept:
            logger.warning(f"Update failed: Department dept_id={dept_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department record not found.")

        if current_user["role_id"] == ROLES["admin"] and dept["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant department update denied for dept_id={dept_id} to user_id={current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot edit departments outside your organization."
            )

        if "slug" in updates and updates["slug"] != dept["slug"]:
            existing = Department.get_by_slug(dept["organization_id"], updates["slug"])
            if existing:
                logger.warning(f"Duplicate slug '{updates['slug']}' for org_id={dept['organization_id']}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Department slug '{updates['slug']}' already exists for this organization."
                )

        updated = Department.update(dept_id, updates)
        if not updated:
            logger.warning(f"No valid fields provided for updating dept_id={dept_id}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        if updates.get("status") == "inactive":
            User.suspend_by_department(dept_id)
            logger.info(f"Department dept_id={dept_id} set to inactive. User profiles suspended cascade executed.")

        logger.info(f"Department updated successfully: dept_id={dept_id}, updated_fields={list(updates.keys())}")
        return {"status": "success", "message": "Department updated successfully."}

    @staticmethod
    def create_user(current_user: Dict[str, Any], role_id: int, organization_id: Optional[int],
                    department_id: Optional[int], name: str, email: str, password_raw: str,
                    user_status: Optional[str] = None) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        if current_user["role_id"] == ROLES["superadmin"]:
            if role_id == ROLES["superadmin"] and (organization_id is not None or department_id is not None):
                logger.warning("Structure Mismatch: Superadmin assigned org/dept.")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Structure Mismatch: Global Superadmins must not belong to specific organizations or departments."
                )

        elif current_user["role_id"] == ROLES["admin"]:
            organization_id = current_user["organization_id"]
            if role_id in [ROLES["superadmin"], ROLES["admin"]]:
                logger.warning(f"Role escalation denied for Tenant Admin user_id={current_user['id']} attempting to create role_id={role_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Operation Denied: Tenant Admins can only provision Managers and Operational Agents."
                )

        elif current_user["role_id"] == ROLES["manager"]:
            organization_id = current_user["organization_id"]
            department_id = current_user["department_id"]
            if role_id != ROLES["agent"]:
                logger.warning(f"Role creation denied for Manager user_id={current_user['id']} attempting role_id={role_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Operation Denied: Department Managers can only provision Operational Agents."
                )

        if role_id == ROLES["agent"] and (organization_id is None or department_id is None):
            logger.warning(f"Missing org/dept context for Agent user creation.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure Mismatch: Operational Agents require both valid organization and department contexts."
            )

        user_create_kwargs: Dict[str, Any] = {
            "role_id": role_id,
            "organization_id": organization_id,
            "department_id": department_id,
            "name": name,
            "email": email,
            "password_raw": password_raw
        }
        if user_status is not None:
            user_create_kwargs["status"] = user_status

        try:
            user_id = User.create(**user_create_kwargs)
            logger.info(f"User provisioned successfully: user_id={user_id}, email='{email}', role_id={role_id}, org_id={organization_id}, dept_id={department_id}")
            return {"status": "success", "id": user_id, "message": "User provisioned successfully."}
        except ValueError as e:
            logger.warning(f"Failed to provision user '{email}': {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def list_users(current_user: Dict[str, Any], role_id: Optional[int] = None) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        org_filter = None
        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"]]:
            org_filter = current_user["organization_id"]

        rows = User.list_all_with_relations(role_id=role_id, organization_id=org_filter)
        users = [dict(row) for row in rows] if rows else []

        if current_user["role_id"] == ROLES["manager"]:
            users = [u for u in users if u.get("department_id") == current_user["department_id"]]

        logger.info(f"Retrieved {len(users)} users for requester user_id={current_user['id']} (role_id={current_user['role_id']})")
        return {"users": users}

    @staticmethod
    def get_user_by_id(current_user: Dict[str, Any], user_id: int) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        user = User.get_by_id_with_relations(user_id)
        if not user:
            logger.warning(f"User account not found: user_id={user_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"]] and user["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant user view denied for user_id={user_id} to requester={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to cross-tenant user records.")

        if current_user["role_id"] == ROLES["manager"] and user["department_id"] != current_user["department_id"]:
            logger.warning(f"Cross-department user view denied for user_id={user_id} to manager requester={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to users outside your department.")

        logger.info(f"Retrieved user details: user_id={user_id}")
        return dict(user)

    @staticmethod
    def update_user(current_user: Dict[str, Any], user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        target_user = User.get_by_id(user_id)
        if not target_user:
            logger.warning(f"Update failed: User account user_id={user_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

        if current_user["role_id"] == ROLES["admin"]:
            if target_user["organization_id"] != current_user["organization_id"]:
                logger.warning(f"Cross-tenant user edit denied for user_id={user_id} to admin requester={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit users outside your organization.")
            if "role_id" in updates and updates["role_id"] in [ROLES["superadmin"], ROLES["admin"]]:
                logger.warning(f"Role escalation attempt denied for user_id={user_id} to new role_id={updates['role_id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot escalate role permissions.")

        if current_user["role_id"] == ROLES["manager"]:
            if target_user["organization_id"] != current_user["organization_id"] or target_user["department_id"] != current_user["department_id"]:
                logger.warning(f"Cross-department user edit denied for user_id={user_id} to manager requester={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only update users within their assigned department.")
            if "role_id" in updates and updates["role_id"] != ROLES["agent"]:
                logger.warning(f"Invalid role change attempt by manager for user_id={user_id}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers cannot alter agent role assignments.")

        # Sanitize password logging if included in updates
        sanitized_updates_keys = list(updates.keys())
        updated = User.update(user_id, updates)
        if not updated:
            logger.warning(f"No valid fields provided for updating user_id={user_id}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        logger.info(f"User profile updated successfully: user_id={user_id}, updated_fields={sanitized_updates_keys}")
        return {"status": "success", "message": "User profile updated successfully."}
