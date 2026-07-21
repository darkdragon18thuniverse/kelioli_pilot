from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from src.app.models.compliance import ComplianceParameter
from src.app.models.department import Department

ROLES = {
    "superadmin": 1,
    "admin": 2,
    "manager": 3,
    "agent": 4
}


class ComplianceController:
    @staticmethod
    def _verify_role(current_user: Dict[str, Any], allowed_role_ids: List[int]) -> None:
        if current_user["role_id"] not in allowed_role_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Insufficient administrative privileges."
            )

    @staticmethod
    def create_parameter(current_user: Dict[str, Any], organization_id: int, department_id: int,
                         parameter_name: str, rule_description: str, severity_level: str) -> Dict[str, Any]:
        """Creates a compliance rule. Tenant Admins & Managers supported."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        # Tenant Admin Scope Check
        if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != organization_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot create rules for other organizations.")

        # Manager Scope Check
        if current_user["role_id"] == ROLES["manager"]:
            if current_user["organization_id"] != organization_id or current_user["department_id"] != department_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only create rules for their assigned department.")

        # Verify department exists and belongs to organization
        dept = Department.get_by_id(department_id)
        if not dept or dept["organization_id"] != organization_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid department_id for the specified organization.")

        try:
            param_id = ComplianceParameter.create(
                organization_id=organization_id,
                department_id=department_id,
                parameter_name=parameter_name,
                rule_description=rule_description,
                severity_level=severity_level
            )
            return {"status": "success", "id": param_id, "message": "Compliance parameter created successfully."}
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def list_parameters_by_department(current_user: Dict[str, Any], organization_id: int, department_id: int) -> Dict[str, Any]:
        """Lists compliance rules for a specific department."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]] and current_user["organization_id"] != organization_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access rules outside your organization.")

        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]] and current_user["department_id"] != department_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to rules outside your assigned department.")

        rows = ComplianceParameter.list_by_department(organization_id, department_id)
        return {"parameters": [dict(row) for row in rows] if rows else []}

    @staticmethod
    def get_parameter_by_id(current_user: Dict[str, Any], parameter_id: int) -> Dict[str, Any]:
        """Fetches single rule details."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        param = ComplianceParameter.get_by_id(parameter_id)
        if not param:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compliance parameter record not found.")

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]] and param["organization_id"] != current_user["organization_id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to cross-tenant rules.")

        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]] and param["department_id"] != current_user["department_id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to rules outside your assigned department.")

        return dict(param)

    @staticmethod
    def update_parameter(current_user: Dict[str, Any], parameter_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates rule details or toggles enable/disable status."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        param = ComplianceParameter.get_by_id(parameter_id)
        if not param:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compliance parameter record not found.")

        if current_user["role_id"] == ROLES["admin"] and param["organization_id"] != current_user["organization_id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit rules outside your organization.")

        if current_user["role_id"] == ROLES["manager"]:
            if param["organization_id"] != current_user["organization_id"] or param["department_id"] != current_user["department_id"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only update rules within their assigned department.")

        updated = ComplianceParameter.update(parameter_id, updates)
        if not updated:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        return {"status": "success", "message": "Compliance parameter updated successfully."}
