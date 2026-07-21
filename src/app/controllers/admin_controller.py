from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User

ROLES = {
    "superadmin": 1,
    "admin": 2,
    "manager": 3,
    "agent": 4
}


class AdminController:
    @staticmethod
    def _verify_role(current_user: Dict[str, Any], allowed_role_ids: List[int]) -> None:
        """Helper to enforce strict RBAC bounds."""
        if current_user["role_id"] not in allowed_role_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Insufficient administrative privileges."
            )

    @staticmethod
    def create_organization(current_user: Dict[str, Any], name: str, slug: str, 
                            billing_email: Optional[str] = None, tier: str = "free", 
                            per_minute_cost: float = 0.0, infra_fixed_cost: float = 0.0) -> Dict[str, Any]:
        """Global corporate tenant initialization. Restricted entirely to Superadmins."""
        AdminController._verify_role(current_user, [ROLES["superadmin"]])
        
        if current_user.get("organization_id") is not None or current_user.get("department_id") is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure Mismatch: Global Superadmins must not belong to specific organizations or departments."
            )
        
        try:
            org_id = Organization.create(
                name=name, slug=slug, billing_email=billing_email, tier=tier,
                per_minute_cost=per_minute_cost, infra_fixed_cost=infra_fixed_cost
            )
            return {"status": "success", "id": org_id, "message": "Organization provisioned successfully."}
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def get_organizations(current_user: Dict[str, Any]) -> Dict[str, Any]:
        """Retrieves all database tenant records to fill out the admin data tables."""
        AdminController._verify_role(current_user, [ROLES["superadmin"]])
        
        try:
            all_rows = Organization.list_all()
            orgs_list = [dict(row) for row in all_rows] if all_rows else []
            return {"organizations": orgs_list}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @staticmethod
    def get_organization_by_id(current_user: Dict[str, Any], org_id: int) -> Dict[str, Any]:
        """Fetches single organization details for View/Edit modals."""
        AdminController._verify_role(current_user, [ROLES["superadmin"]])
        
        org = Organization.get_by_id(org_id)
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")
        return dict(org)

    @staticmethod
    def update_organization(current_user: Dict[str, Any], org_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates organization configuration fields."""
        AdminController._verify_role(current_user, [ROLES["superadmin"]])
        
        org = Organization.get_by_id(org_id)
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")

        updated = Organization.update(org_id, updates)
        if not updated:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        return {"status": "success", "message": "Organization settings updated successfully."}

    @staticmethod
    def get_admin_summary(current_user: Dict[str, Any]) -> Dict[str, Any]:
        """Aggregates metric card totals across the complete platform infrastructure."""
        AdminController._verify_role(current_user, [ROLES["superadmin"]])
        
        try:
            active_orgs = Organization.list_active()
            total_tenants = len(active_orgs) if active_orgs else 0
            
            all_users = User.list_all_with_relations()
            global_users = len(all_users) if all_users else 0
            total_calls = 0 
            
            return {
                "total_tenants": total_tenants,
                "global_platform_users": global_users,
                "total_audited_calls": total_calls
            }
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @staticmethod
    def create_department(current_user: Dict[str, Any], organization_id: int, name: str, slug: str) -> Dict[str, Any]:
        """Creates departments. Scoped strictly to the admin's organization."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])
        
        if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot create departments outside your organization."
            )
            
        try:
            dept_id = Department.create(organization_id=organization_id, name=name, slug=slug)
            return {"status": "success", "id": dept_id, "message": "Department created successfully."}
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def get_departments_by_organization(current_user: Dict[str, Any], organization_id: int) -> Dict[str, Any]:
        """Retrieves all departments for a given organization ID."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot access departments outside your organization."
            )

        rows = Department.list_by_organization(organization_id)
        return {"departments": [dict(row) for row in rows] if rows else []}

    @staticmethod
    def get_department_by_id(current_user: Dict[str, Any], dept_id: int) -> Dict[str, Any]:
        """Fetches single department details with tenant scoping."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        dept = Department.get_by_id(dept_id)
        if not dept:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department record not found.")

        if current_user["role_id"] == ROLES["admin"] and dept["organization_id"] != current_user["organization_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access Denied: Cross-tenant department record access prohibited."
            )

        return dict(dept)

    @staticmethod
    def update_department(current_user: Dict[str, Any], dept_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates department attributes, including toggle enable/disable status."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        dept = Department.get_by_id(dept_id)
        if not dept:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department record not found.")

        if current_user["role_id"] == ROLES["admin"] and dept["organization_id"] != current_user["organization_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot edit departments outside your organization."
            )

        if "slug" in updates and updates["slug"] != dept["slug"]:
            existing = Department.get_by_slug(dept["organization_id"], updates["slug"])
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Department slug '{updates['slug']}' already exists for this organization."
                )

        updated = Department.update(dept_id, updates)
        if not updated:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        return {"status": "success", "message": "Department updated successfully."}

    @staticmethod
    def create_user(current_user: Dict[str, Any], role_id: int, organization_id: Optional[int], 
                    department_id: Optional[int], name: str, email: str, password_raw: str) -> Dict[str, Any]:
        """Strict Hierarchical Provisioning Flow with Token-Context Enforcement."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        # 1. SUPERADMIN CONSTRAINTS
        if current_user["role_id"] == ROLES["superadmin"]:
            if role_id == ROLES["superadmin"] and (organization_id is not None or department_id is not None):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Structure Mismatch: Global Superadmins must not belong to specific organizations or departments."
                )

        # 2. TENANT ADMIN CONSTRAINTS
        elif current_user["role_id"] == ROLES["admin"]:
            organization_id = current_user["organization_id"]
            if role_id in [ROLES["superadmin"], ROLES["admin"]]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Operation Denied: Tenant Admins can only provision Managers and Operational Agents."
                )

        # 3. MANAGER CONSTRAINTS
        elif current_user["role_id"] == ROLES["manager"]:
            organization_id = current_user["organization_id"]
            department_id = current_user["department_id"]
            if role_id != ROLES["agent"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Operation Denied: Department Managers can only provision Operational Agents."
                )

        if role_id == ROLES["agent"] and (organization_id is None or department_id is None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure Mismatch: Operational Agents require both valid organization and department contexts."
            )

        try:
            user_id = User.create(
                role_id=role_id, organization_id=organization_id, department_id=department_id,
                name=name, email=email, password_raw=password_raw
            )
            return {"status": "success", "id": user_id, "message": "User provisioned successfully."}
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def list_users(current_user: Dict[str, Any], role_id: Optional[int] = None) -> Dict[str, Any]:
        """Lists users with RBAC tenant and department scoping."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])
        
        org_filter = None
        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"]]:
            org_filter = current_user["organization_id"]

        rows = User.list_all_with_relations(role_id=role_id, organization_id=org_filter)
        users = [dict(row) for row in rows] if rows else []

        if current_user["role_id"] == ROLES["manager"]:
            users = [u for u in users if u.get("department_id") == current_user["department_id"]]

        return {"users": users}

    @staticmethod
    def get_user_by_id(current_user: Dict[str, Any], user_id: int) -> Dict[str, Any]:
        """Fetches single user details."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])
        
        user = User.get_by_id_with_relations(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"]] and user["organization_id"] != current_user["organization_id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to cross-tenant user records.")

        if current_user["role_id"] == ROLES["manager"] and user["department_id"] != current_user["department_id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to users outside your department.")

        return dict(user)

    @staticmethod
    def update_user(current_user: Dict[str, Any], user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates user profile or status with strict role mutation checks."""
        AdminController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])
        
        target_user = User.get_by_id(user_id)
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

        if current_user["role_id"] == ROLES["admin"]:
            if target_user["organization_id"] != current_user["organization_id"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit users outside your organization.")
            if "role_id" in updates and updates["role_id"] in [ROLES["superadmin"], ROLES["admin"]]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot escalate role permissions.")

        if current_user["role_id"] == ROLES["manager"]:
            if target_user["organization_id"] != current_user["organization_id"] or target_user["department_id"] != current_user["department_id"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only update users within their assigned department.")
            if "role_id" in updates and updates["role_id"] != ROLES["agent"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers cannot alter agent role assignments.")

        updated = User.update(user_id, updates)
        if not updated:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        return {"status": "success", "message": "User profile updated successfully."}
