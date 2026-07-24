from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from src.app.models.compliance import ComplianceParameter
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.core.logging_config import get_logger
from src.app.core.roles import ROLES

logger = get_logger(__name__)


class ComplianceController:
    @staticmethod
    def _verify_role(current_user: Dict[str, Any], allowed_role_ids: List[int]) -> None:
        if current_user["role_id"] not in allowed_role_ids:
            logger.warning(f"RBAC Denied: User {current_user['id']} (role_id: {current_user['role_id']}) attempted operation requiring role_ids: {allowed_role_ids}")
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
            logger.warning(f"Tenant Admin Scope Denied: User {current_user['id']} tried creating compliance rule for org_id {organization_id}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot create rules for other organizations.")

        # Manager Scope Check
        if current_user["role_id"] == ROLES["manager"]:
            if current_user["organization_id"] != organization_id or current_user["department_id"] != department_id:
                logger.warning(f"Manager Scope Denied: User {current_user['id']} tried creating compliance rule for org_id {organization_id}, dept_id {department_id}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only create rules for their assigned department.")

        # Verify department exists and belongs to organization
        dept = Department.get_by_id(department_id)
        if not dept or dept["organization_id"] != organization_id:
            logger.warning(f"Invalid department context: dept_id {department_id} for org_id {organization_id}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid department_id for the specified organization.")

        try:
            param_id = ComplianceParameter.create(
                organization_id=organization_id,
                department_id=department_id,
                parameter_name=parameter_name,
                rule_description=rule_description,
                severity_level=severity_level
            )
            logger.info(f"Compliance parameter created: param_id={param_id}, name='{parameter_name}', severity='{severity_level}', org_id={organization_id}, dept_id={department_id}")
            return {"status": "success", "id": param_id, "message": "Compliance parameter created successfully."}
        except ValueError as e:
            logger.warning(f"Failed to create compliance parameter: {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @staticmethod
    def list_parameters_by_department(current_user: Dict[str, Any], organization_id: int, department_id: int) -> Dict[str, Any]:
        """Lists compliance rules for a specific department."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]] and current_user["organization_id"] != organization_id:
            logger.warning(f"Cross-tenant rule access denied for User {current_user['id']} requesting org_id {organization_id}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access rules outside your organization.")

        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]] and current_user["department_id"] != department_id:
            logger.warning(f"Cross-department rule access denied for User {current_user['id']} requesting dept_id {department_id}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to rules outside your assigned department.")

        rows = ComplianceParameter.list_by_department(organization_id, department_id)
        params_list = [dict(row) for row in rows] if rows else []
        logger.info(f"Retrieved {len(params_list)} compliance parameters for org_id={organization_id}, dept_id={department_id}")
        return {"parameters": params_list}

    @staticmethod
    def get_parameter_by_id(current_user: Dict[str, Any], parameter_id: int) -> Dict[str, Any]:
        """Fetches single rule details."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        param = ComplianceParameter.get_by_id(parameter_id)
        if not param:
            logger.warning(f"Compliance parameter record not found: param_id={parameter_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compliance parameter record not found.")

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]] and param["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant rule access denied for param_id={parameter_id} to user_id={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to cross-tenant rules.")

        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]] and param["department_id"] != current_user["department_id"]:
            logger.warning(f"Cross-department rule access denied for param_id={parameter_id} to user_id={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to rules outside your assigned department.")

        logger.info(f"Retrieved compliance parameter detail: param_id={parameter_id}")
        return dict(param)

    @staticmethod
    def update_parameter(current_user: Dict[str, Any], parameter_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates rule details or toggles enable/disable status."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        param = ComplianceParameter.get_by_id(parameter_id)
        if not param:
            logger.warning(f"Update failed: Compliance parameter record not found: param_id={parameter_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compliance parameter record not found.")

        if current_user["role_id"] == ROLES["admin"] and param["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant update denied for param_id={parameter_id} to user_id={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit rules outside your organization.")

        if current_user["role_id"] == ROLES["manager"]:
            if param["organization_id"] != current_user["organization_id"] or param["department_id"] != current_user["department_id"]:
                logger.warning(f"Cross-department update denied for param_id={parameter_id} to manager user_id={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only update rules within their assigned department.")

        updated = ComplianceParameter.update(parameter_id, updates)
        if not updated:
            logger.warning(f"No valid fields provided for updating param_id={parameter_id}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid fields provided for update.")

        logger.info(f"Compliance parameter updated successfully: param_id={parameter_id}, updated_fields={list(updates.keys())}")
        return {"status": "success", "message": "Compliance parameter updated successfully."}

    @staticmethod
    def format_rule(current_user: Dict[str, Any], raw_input: str, expected_action: Optional[str] = None,
                    failure_example: Optional[str] = None) -> Dict[str, str]:
        """Formats compliance rule text using AI. Supported for Superadmins, Admins, and Managers."""
        ComplianceController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])

        from src.app.services.stt import LLMService

        model = "openrouter/free"
        org_id = current_user.get("organization_id")
        if org_id:
            org = Organization.get_by_id(org_id)
            if org and org["llm_model_routing"]:
                model = org["llm_model_routing"]

        try:
            result = LLMService.format_rule(
                raw_input=raw_input,
                expected_action=expected_action,
                failure_example=failure_example,
                model=model
            )
            logger.info(f"Compliance rule reformatted successfully for user_id={current_user['id']}")
            return result
        except ValueError as e:
            logger.warning(f"Failed to format compliance rule: {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            logger.exception(f"Unexpected error while formatting compliance rule: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"LLM formatting error: {str(e)}")
