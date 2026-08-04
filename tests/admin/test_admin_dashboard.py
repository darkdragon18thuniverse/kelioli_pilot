from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.call import Call, CallEvaluation


def _login(client, email, password):
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def test_dashboard_admin_scoped_to_own_org(client):
    """Admin/manager/agent get their org's dashboard from the JWT without passing organization_id."""
    org_id = Organization.create(name="Dashboard Org", slug="dashboard-org")
    dept_id = Department.create(organization_id=org_id, name="Support", slug="support")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Dash Admin", email="dash_admin@test.com", password_raw="Password2026!"
    )

    token = _login(client, "dash_admin@test.com", "Password2026!")
    res = client.get("/api/v1/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["period"]["label"] == "Last 30 days"
    assert "kpis" in data
    assert data["kpis"]["agents_total_count"] == 0
    assert data["kpis"]["calls_audited_count"] == 0
    assert data["kpis"]["avg_compliance_score"] is None


def test_dashboard_superadmin_requires_organization_id(client):
    """Mirrors CallsController.list_calls: superadmin without organization_id gets 400,
    with organization_id gets 200."""
    org_id = Organization.create(name="Super Dash Org", slug="super-dash-org")
    Department.create(organization_id=org_id, name="Ops", slug="ops")
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Dash Super", email="dash_super@test.com", password_raw="SuperPass2026!"
    )

    token = _login(client, "dash_super@test.com", "SuperPass2026!")

    res_no_org = client.get("/api/v1/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res_no_org.status_code == status.HTTP_400_BAD_REQUEST

    res_with_org = client.get(
        "/api/v1/admin/dashboard",
        params={"organization_id": org_id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_with_org.status_code == status.HTTP_200_OK
    assert res_with_org.json()["period"]["label"] == "Last 30 days"


def test_dashboard_admin_cannot_view_other_org_via_query_param(client):
    """An org-scoped admin passing organization_id for another tenant must not see that
    tenant's data — effective_org_id is always overridden from the JWT for non-superadmins."""
    org1_id = Organization.create(name="Org One Dash", slug="org1-dash")
    Department.create(organization_id=org1_id, name="D1", slug="d1-dash")
    User.create(
        role_id=2, organization_id=org1_id, department_id=None,
        name="Admin One", email="admin1_dash@test.com", password_raw="Password2026!"
    )

    org2_id = Organization.create(name="Org Two Dash", slug="org2-dash")
    dept2_id = Department.create(organization_id=org2_id, name="D2", slug="d2-dash")
    call_id = Call.create(
        organization_id=org2_id, department_id=dept2_id, user_id=None,
        audio_url="https://storage.example.com/other_org.mp3"
    )
    Call.update_evaluation_results(
        call_id=call_id, transcript="hi", total_checked=0, total_passed=0,
        compliance_score_percentage=None, processing_status="completed"
    )

    token = _login(client, "admin1_dash@test.com", "Password2026!")
    res = client.get(
        "/api/v1/admin/dashboard",
        params={"organization_id": org2_id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_200_OK
    # Must be scoped to org1 (0 calls), never org2's data.
    assert res.json()["kpis"]["calls_audited_count"] == 0


def test_dashboard_null_safe_average_zero_rule_department_not_coerced_to_zero(client):
    """A completed call in a department with zero active compliance rules writes a NULL
    compliance_score_percentage (not 0.0). The dashboard's avg_compliance_score and the
    agent's avg_score must both stay null, and the agent must be marked is_scored=false —
    never silently averaged in as a 0% score."""
    org_id = Organization.create(name="Zero Rule Org", slug="zero-rule-org")
    dept_id = Department.create(organization_id=org_id, name="Unscored Dept", slug="unscored-dept")
    admin_id = User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Zero Rule Admin", email="zero_rule_admin@test.com", password_raw="Password2026!"
    )
    agent_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Unscored Agent", email="unscored_agent@test.com", password_raw="Password2026!"
    )

    # Simulate the real pipeline outcome for a completed call in a zero-rule department:
    # total_checked=0 and compliance_score_percentage explicitly NULL (never 0.0).
    call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent_id,
        audio_url="https://storage.example.com/zero_rule.mp3"
    )
    Call.update_evaluation_results(
        call_id=call_id, transcript="Hello", total_checked=0, total_passed=0,
        compliance_score_percentage=None, processing_status="completed"
    )

    token = _login(client, "zero_rule_admin@test.com", "Password2026!")
    res = client.get("/api/v1/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    # The call is still "audited" (processed through the pipeline) even though unscored.
    assert data["kpis"]["calls_audited_count"] == 1
    # But the score average must not exist / not be coerced to 0.
    assert data["kpis"]["avg_compliance_score"] is None

    agents = data["agent_performance"]
    assert len(agents) == 1
    agent = agents[0]
    assert agent["user_id"] == agent_id
    assert agent["calls_count"] == 1
    assert agent["avg_score"] is None
    assert agent["is_scored"] is False

    # Department coverage must reflect zero active rules / not covered.
    dept_coverage = data["department_coverage"]
    assert len(dept_coverage) == 1
    assert dept_coverage[0]["department_id"] == dept_id
    assert dept_coverage[0]["active_rule_count"] == 0
    assert dept_coverage[0]["is_covered"] is False
    assert dept_coverage[0]["avg_score"] is None


def test_dashboard_full_shape_with_seeded_scoring_scenario(client):
    """End-to-end shape/content check: a department WITH active rules, a passing call and a
    failing critical call, verifying kpis, severity_breakdown, rules_by_failure_rate,
    agent_performance and critical_failures_feed are all populated from real data."""
    org_id = Organization.create(name="Scored Org", slug="scored-org")
    dept_id = Department.create(organization_id=org_id, name="Billing", slug="billing")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Scored Admin", email="scored_admin@test.com", password_raw="Password2026!"
    )
    agent_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Scored Agent", email="scored_agent@test.com", password_raw="Password2026!"
    )
    param_id = ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Confirm identity", rule_description="Must confirm caller identity",
        severity_level="critical"
    )

    # Passing call: 1 checked, 1 passed, 100%.
    pass_call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent_id,
        audio_url="https://storage.example.com/pass.mp3", procedure_enquired="Billing dispute"
    )
    Call.update_evaluation_results(
        call_id=pass_call_id, transcript="Verified identity, thanks.", total_checked=1, total_passed=1,
        compliance_score_percentage=100.0, procedure_enquired="Billing dispute", processing_status="completed"
    )
    CallEvaluation.create_batch([{
        "call_id": pass_call_id, "parameter_id": param_id, "did_follow_rule": 1
    }])

    # Failing call: 1 checked, 0 passed, 0%, critical failure with an offset.
    fail_call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent_id,
        audio_url="https://storage.example.com/fail.mp3", procedure_enquired="Billing dispute"
    )
    Call.update_evaluation_results(
        call_id=fail_call_id, transcript="Sure, here's the account balance.", total_checked=1, total_passed=0,
        compliance_score_percentage=0.0, procedure_enquired="Billing dispute", processing_status="completed"
    )
    CallEvaluation.create_batch([{
        "call_id": fail_call_id, "parameter_id": param_id, "did_follow_rule": 0,
        "failed_line_text": "Sure, here's the account balance.",
        "failure_offset_seconds": 12,
        "failure_reason": "Did not verify identity before disclosing balance."
    }])

    token = _login(client, "scored_admin@test.com", "Password2026!")
    res = client.get("/api/v1/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    assert data["kpis"]["calls_audited_count"] == 2
    assert data["kpis"]["avg_compliance_score"] == 50.0
    assert data["kpis"]["critical_failures_count"] == 1
    assert data["kpis"]["critical_failures_rule_count"] == 1
    assert data["kpis"]["critical_failures_agent_count"] == 1

    critical_bucket = next(s for s in data["severity_breakdown"] if s["severity_level"] == "critical")
    assert critical_bucket["failure_count"] == 1
    assert critical_bucket["rule_count"] == 1
    assert critical_bucket["agent_count"] == 1

    assert len(data["rules_by_failure_rate"]) == 1
    rule = data["rules_by_failure_rate"][0]
    assert rule["parameter_id"] == param_id
    assert rule["department_id"] == dept_id
    assert rule["failed_count"] == 1
    assert rule["checked_count"] == 2
    assert rule["failure_rate"] == 50.0

    agents = data["agent_performance"]
    assert len(agents) == 1
    assert agents[0]["user_id"] == agent_id
    assert agents[0]["calls_count"] == 2
    assert agents[0]["avg_score"] == 50.0
    assert agents[0]["critical_count"] == 1
    assert agents[0]["is_scored"] is True

    feed = data["critical_failures_feed"]
    assert len(feed) == 1
    assert feed[0]["call_id"] == fail_call_id
    assert feed[0]["failed_line_text"] == "Sure, here's the account balance."
    assert feed[0]["failure_offset_seconds"] == 12
    assert feed[0]["agent_name"] == "Scored Agent"
    assert feed[0]["department_name"] == "Billing"

    topics = data["topic_breakdown"]
    assert len(topics) == 1
    assert topics[0]["topic"] == "Billing dispute"
    assert topics[0]["calls_count"] == 2
    assert topics[0]["failure_rate"] == 50.0

    dept_coverage = next(d for d in data["department_coverage"] if d["department_id"] == dept_id)
    assert dept_coverage["active_rule_count"] == 1
    assert dept_coverage["is_covered"] is True
    assert dept_coverage["agent_count"] == 1
    assert dept_coverage["calls_count"] == 2
    assert dept_coverage["avg_score"] == 50.0

    health = data["processing_health"]
    assert health["completed"] == 2
    assert health["pending"] == 0
    assert health["failed"] == 0

    assert len(data["score_trend"]) == 12


def test_dashboard_default_target_compliance_score(client):
    """An org with no custom target_compliance_score reports the 85.0 DB column default
    at the top level of the dashboard response, and it drives agents_below_target_count."""
    org_id = Organization.create(name="Default Target Dash Org", slug="default-target-dash-org")
    dept_id = Department.create(organization_id=org_id, name="Ops", slug="ops-default-target")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Default Target Admin", email="default_target_admin@test.com", password_raw="Password2026!"
    )
    agent_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Default Target Agent", email="default_target_agent@test.com", password_raw="Password2026!"
    )
    # An active rule is required for the department to be considered "scored" —
    # otherwise avg_score is forced null regardless of compliance_score_percentage.
    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Confirm identity", rule_description="Must confirm caller identity",
        severity_level="medium"
    )
    call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent_id,
        audio_url="https://storage.example.com/default_target.mp3"
    )
    Call.update_evaluation_results(
        call_id=call_id, transcript="Hi", total_checked=1, total_passed=1,
        compliance_score_percentage=80.0, processing_status="completed"
    )

    token = _login(client, "default_target_admin@test.com", "Password2026!")
    res = client.get("/api/v1/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    assert data["target_compliance_score"] == 85.0
    # 80.0 < 85.0 default target -> this agent counts as below target.
    assert data["kpis"]["agents_below_target_count"] == 1


def test_dashboard_reflects_custom_target_compliance_score(client):
    """An org with a custom target_compliance_score has that value echoed at the top
    level of the dashboard response, and agents_below_target_count is computed against
    it (not the platform default of 85.0)."""
    org_id = Organization.create(
        name="Custom Target Dash Org", slug="custom-target-dash-org",
        target_compliance_score=60.0
    )
    dept_id = Department.create(organization_id=org_id, name="Ops", slug="ops-custom-target")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Custom Target Admin", email="custom_target_admin@test.com", password_raw="Password2026!"
    )
    agent_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Custom Target Agent", email="custom_target_agent@test.com", password_raw="Password2026!"
    )
    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Confirm identity", rule_description="Must confirm caller identity",
        severity_level="medium"
    )
    # 80.0 is below the platform default (85.0) but above this org's custom target (60.0).
    call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent_id,
        audio_url="https://storage.example.com/custom_target.mp3"
    )
    Call.update_evaluation_results(
        call_id=call_id, transcript="Hi", total_checked=1, total_passed=1,
        compliance_score_percentage=80.0, processing_status="completed"
    )

    token = _login(client, "custom_target_admin@test.com", "Password2026!")
    res = client.get("/api/v1/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    assert data["target_compliance_score"] == 60.0
    # 80.0 >= 60.0 custom target -> this agent is NOT below target, unlike the default-org case.
    assert data["kpis"]["agents_below_target_count"] == 0


def test_dashboard_invalid_period_rejected(client):
    org_id = Organization.create(name="Invalid Period Org", slug="invalid-period-org")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Invalid Period Admin", email="invalid_period_admin@test.com", password_raw="Password2026!"
    )
    token = _login(client, "invalid_period_admin@test.com", "Password2026!")
    res = client.get(
        "/api/v1/admin/dashboard",
        params={"period": "1y"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
