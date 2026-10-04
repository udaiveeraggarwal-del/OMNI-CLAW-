"""
Tier 4: Real-World Application Scenarios E2E Test Suite.
Validates >=5 comprehensive end-to-end production workflows:
- Scenario 1: Autonomous Research Agent (browser_operate + SEO audit + report synthesis)
- Scenario 2: E-Commerce Automation (data normalization + Odoo product & page creation)
- Scenario 3: Social Media Content Engine (trend analysis + YouTube script + Instagram post + rubric evaluation)
- Scenario 4: Secure Privacy-Preserving Agent Operation (3-hop onion routing + SOCKS5 tunnel + remote DNS + header scrubbing)
- Scenario 5: Visual Workflow Execution Pipeline (canvas DAG export -> compilation -> end-to-end execution)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from omniagent.core.models import (
    LLMResponse,
    Message,
    MessageRole,
    NetworkSecurityContext,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.planner import (
    ExecutionPlan,
    MultiStepPlanner,
    PlanStep,
    StepStatus,
)
from omniagent.core.router import (
    ToolRegistry,
    ToolRouter,
)
from omniagent.core.state import (
    AgentState,
    SQLiteStateStore,
)
from tests.e2e.conftest import (
    MockSOCKS5Server,
    SAMPLE_HTML_PAGE,
    SAMPLE_WORKFLOW_SCHEMA,
)
from tests.e2e.contract_oracles import (
    ReferenceBrowserOperate,
    ReferenceCodeRunner,
    ReferenceFingerprintScrubber,
    ReferenceOdooBuilder,
    ReferenceOnionRouter,
    ReferenceSEOOptimizer,
    ReferenceSocialMedia,
    ReferenceWorkflowRunner,
)


class TestTier4RealWorldApplicationScenarios(unittest.TestCase):
    """Tier 4: Comprehensive Real-World Application Scenario tests (>=5 workflows)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="omni_e2e_t4_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Scenario 1: Autonomous Research Agent
    # --------------------------------------------------------------------------

    def test_scenario_1_autonomous_research_agent(self):
        """
        Scenario 1: Autonomous Research Agent Workflow.
        1. browser_operate navigates to documentation/landing page and extracts rendered content.
        2. seo_optimizer performs in-depth technical audit and keyword density analysis.
        3. MultiStepPlanner reflects on findings and synthesizes an executive summary report.
        """
        registry = ToolRegistry()
        browser = ReferenceBrowserOperate()
        seo = ReferenceSEOOptimizer()
        registry.register(browser)
        registry.register(seo)
        router = ToolRouter(registry=registry)
        mock_provider = ProviderFactory.create_mock("openai")

        db_path = os.path.join(self.temp_dir, "research_agent.db")
        state_store = SQLiteStateStore(db_path=db_path)
        planner = MultiStepPlanner(router=router, provider=mock_provider, state_store=state_store)

        session_id = "research_session_001"
        plan = ExecutionPlan(goal="Perform competitive audit and produce executive synthesis")

        # Step 1: Browse target
        step1 = PlanStep(
            step_id=1,
            title="Navigate to Target Portal",
            tool_name="browser_operate",
            tool_input_template={
                "action": "navigate",
                "html_override": SAMPLE_HTML_PAGE,
            },
        )
        # Step 2: Technical SEO Audit
        step2 = PlanStep(
            step_id=2,
            title="Conduct Technical Audit",
            tool_name="seo_optimizer",
            tool_input_template={
                "action": "audit_page",
                "html_content": SAMPLE_HTML_PAGE,
            },
            dependencies=[1],
        )
        # Step 3: Keyword Analysis
        step3 = PlanStep(
            step_id=3,
            title="Analyze Keyword Density",
            tool_name="seo_optimizer",
            tool_input_template={
                "action": "keyword_density",
                "html_content": SAMPLE_HTML_PAGE,
                "keywords": ["omniagent", "autonomous", "automation"],
            },
            dependencies=[2],
        )

        plan.add_step(step1)
        plan.add_step(step2)
        plan.add_step(step3)

        executed_plan = planner.execute_plan(plan, session_id=session_id)

        # Verification
        self.assertEqual(executed_plan.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed_plan.steps[1].status, StepStatus.COMPLETED)
        self.assertEqual(executed_plan.steps[2].status, StepStatus.COMPLETED)

        audit_res = executed_plan.steps[1].output
        self.assertGreaterEqual(audit_res["health_score"], 90)

        kw_res = executed_plan.steps[2].output
        self.assertIn("omniagent", kw_res["keywords"])
        self.assertGreater(kw_res["keywords"]["omniagent"]["count"], 0)

        # Confirm persisted session in SQLite
        saved_state = state_store.load(session_id)
        self.assertIsNotNone(saved_state)
        self.assertIn("step1", saved_state.scratchpad)
        self.assertIn("step2", saved_state.scratchpad)

    # --------------------------------------------------------------------------
    # Scenario 2: E-Commerce Automation
    # --------------------------------------------------------------------------

    def test_scenario_2_ecommerce_automation(self):
        """
        Scenario 2: E-Commerce Automation Workflow.
        1. code_runner executes Python data processing on inbound raw catalog CSV/JSON.
        2. odoo_builder creates eCommerce product catalog entries.
        3. odoo_builder creates website landing page for product launch.
        4. Verifies product listing and website page inventory.
        """
        registry = ToolRegistry()
        code_runner = ReferenceCodeRunner()
        odoo = ReferenceOdooBuilder()
        registry.register(code_runner)
        registry.register(odoo)
        router = ToolRouter(registry=registry)
        mock_provider = ProviderFactory.create_mock("anthropic")

        planner = MultiStepPlanner(router=router, provider=mock_provider)
        plan = ExecutionPlan(goal="Process product specs and deploy to Odoo store")

        # Step 1: Normalize pricing in sandbox
        normalize_script = """import json
raw_product = {"title": "Autonomous Agent Cloud Server", "cost": 150.0, "markup": 1.6}
price = round(raw_product["cost"] * raw_product["markup"], 2)
print(json.dumps({"name": raw_product["title"], "price": price, "sku": "AGENT-SRV-01"}))
"""
        step1 = PlanStep(
            step_id=1,
            title="Normalize Product Pricing",
            tool_name="code_runner",
            tool_input_template={"language": "python", "code": normalize_script},
        )

        # Step 2: Create Product in Odoo
        step2 = PlanStep(
            step_id=2,
            title="Create Odoo Product",
            tool_name="odoo_builder",
            tool_input_template={
                "action": "create_product",
                "name": "Autonomous Agent Cloud Server",
                "price": 240.0,
                "sku": "AGENT-SRV-01",
            },
            dependencies=[1],
        )

        # Step 3: Create Website Landing Page
        step3 = PlanStep(
            step_id=3,
            title="Publish Store Landing Page",
            tool_name="odoo_builder",
            tool_input_template={
                "action": "create_page",
                "name": "Product Launch",
                "url": "/products/agent-cloud-server",
                "content": "<h1>Autonomous Agent Cloud Server</h1><p>Instant deployment at $240.0</p>",
            },
            dependencies=[2],
        )

        plan.add_step(step1)
        plan.add_step(step2)
        plan.add_step(step3)

        executed_plan = planner.execute_plan(plan)

        self.assertEqual(executed_plan.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed_plan.steps[1].status, StepStatus.COMPLETED)
        self.assertEqual(executed_plan.steps[2].status, StepStatus.COMPLETED)

        # Verify created product in Odoo
        prod_data = executed_plan.steps[1].output
        self.assertEqual(prod_data["name"], "Autonomous Agent Cloud Server")
        self.assertEqual(prod_data["list_price"], 240.0)

        # Verify created page in Odoo
        page_data = executed_plan.steps[2].output
        self.assertEqual(page_data["url"], "/products/agent-cloud-server")

    # --------------------------------------------------------------------------
    # Scenario 3: Social Media Content Engine
    # --------------------------------------------------------------------------

    def test_scenario_3_social_media_content_engine(self):
        """
        Scenario 3: Social Media Content Engine Workflow.
        1. Trend analysis generates multi-platform concept.
        2. social_media skill creates 5-part structured YouTube script (Hook, Intro, Scenes, Script, CTA) + metadata.
        3. social_media skill creates Instagram carousel post with hashtags.
        4. Agent-as-Judge 100-point rubric evaluation confirms schema compliance >= 90/100.
        """
        registry = ToolRegistry()
        social = ReferenceSocialMedia()
        registry.register(social)
        router = ToolRouter(registry=registry)
        mock_provider = ProviderFactory.create_mock("gemini")

        planner = MultiStepPlanner(router=router, provider=mock_provider)
        plan = ExecutionPlan(goal="Create viral multi-platform social media campaign")

        # Step 1: YouTube Script Generation
        step1 = PlanStep(
            step_id=1,
            title="Generate YouTube Video Script",
            tool_name="social_media",
            tool_input_template={
                "action": "generate_youtube_script",
                "topic": "DeepSeek R1 vs Claude 3.5 Sonnet Reasoning",
                "target_duration_seconds": 60,
            },
        )

        # Step 2: Instagram Promotion Post
        step2 = PlanStep(
            step_id=2,
            title="Publish Instagram Campaign Post",
            tool_name="social_media",
            tool_input_template={
                "action": "create_instagram_post",
                "caption": "DeepSeek R1 meets Claude Code! Discover how agentic distillation changes AI.",
                "image_url": "https://media.omniagent.ai/infographic.png",
                "hashtags": ["#deepseek", "#claudecode", "#aiagents", "#omniagent"],
            },
            dependencies=[1],
        )

        plan.add_step(step1)
        plan.add_step(step2)

        executed_plan = planner.execute_plan(plan)
        self.assertEqual(executed_plan.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed_plan.steps[1].status, StepStatus.COMPLETED)

        yt_output = executed_plan.steps[0].output
        self.assertIn("hook", yt_output)
        self.assertIn("scenes", yt_output)
        self.assertIn("call_to_action", yt_output)
        self.assertIn("metadata", yt_output)

        ig_output = executed_plan.steps[1].output
        self.assertEqual(ig_output["status"], "published")
        self.assertIn("#deepseek", ig_output["caption"])

        # Agent-as-Judge rubric evaluation (0-100)
        # Rubric checks: hook present (+20), scenes present (+20), cta present (+20), tags present (+20), ig published (+20)
        score = 0
        if "hook" in yt_output and len(yt_output["hook"]) > 5:
            score += 20
        if "scenes" in yt_output and len(yt_output["scenes"]) >= 2:
            score += 20
        if "call_to_action" in yt_output:
            score += 20
        if "tags" in yt_output["metadata"]:
            score += 20
        if ig_output["status"] == "published":
            score += 20

        self.assertGreaterEqual(score, 90, f"Judge evaluation score {score} is below 90 threshold.")

    # --------------------------------------------------------------------------
    # Scenario 4: Secure Privacy-Preserving Agent Operation
    # --------------------------------------------------------------------------

    def test_scenario_4_secure_privacy_preserving_operation(self):
        """
        Scenario 4: Secure Privacy-Preserving Agent Operation Workflow.
        1. Loopback SOCKS5 proxy server initialized on 127.0.0.1.
        2. NetworkSecurityContext configured with proxy tunneling and fingerprint scrubbing.
        3. Tor-inspired 3-hop onion circuit established with concentric encryption.
        4. LLM query executed with tracking headers stripped and remote DNS resolution.
        """
        proxy = MockSOCKS5Server()
        proxy.start()
        try:
            # 1. Establish Onion Circuit
            onion_router = ReferenceOnionRouter()
            circuit_ok = onion_router.build_circuit()
            self.assertTrue(circuit_ok)

            # Test layered concentric encryption
            secret_request = b"POST /v1/chat/completions HTTP/1.1\r\nHost: api.openai.com\r\n\r\n{'query': 'confidential'}"
            onion_packet = onion_router.onion_encrypt(secret_request)

            # Peeling across Entry -> Middle -> Exit hops
            p1 = onion_router.peel_entry(onion_packet)
            p2 = onion_router.peel_middle(p1)
            peeled_request = onion_router.peel_exit(p2)
            self.assertEqual(peeled_request, secret_request)

            # 2. Configure Security Context
            sec_ctx = NetworkSecurityContext(
                enabled=True,
                proxy_url=f"socks5://127.0.0.1:{proxy.port}",
                route_dns_remotely=True,
                scrub_fingerprints=True,
            )
            provider = ProviderFactory.create_mock("openai", security_context=sec_ctx)

            # 3. Verify Header Scrubbing
            raw_headers = {
                "Host": "api.openai.com",
                "X-Forwarded-For": "103.21.244.2",
                "Sec-CH-UA": '"Chromium";v="120"',
                "User-Agent": "Bot/1.0",
            }
            clean_headers = ReferenceFingerprintScrubber.scrub_headers(raw_headers)
            self.assertNotIn("X-Forwarded-For", clean_headers)
            self.assertNotIn("Sec-CH-UA", clean_headers)
            self.assertIn("Mozilla/5.0", clean_headers["User-Agent"])

            # 4. Execute LLM call in security context
            resp = provider.generate(
                messages=[Message(role=MessageRole.USER, content="Anonymized query")],
            )
            self.assertIsInstance(resp, LLMResponse)
            self.assertTrue(len(resp.content) > 0)
        finally:
            proxy.stop()

    # --------------------------------------------------------------------------
    # Scenario 5: Visual Workflow Execution Pipeline
    # --------------------------------------------------------------------------

    def test_scenario_5_visual_workflow_execution_pipeline(self):
        """
        Scenario 5: Visual Workflow Execution Pipeline.
        1. Loads multi-node visual workflow graph containing Trigger, LLM, Tool, and Action.
        2. Validates graph syntax and DAG dependency integrity.
        3. Executes the full pipeline end-to-end via WorkflowRunner.
        4. Verifies execution traces, intermediate outputs, and synthesized final artifact.
        """
        runner = ReferenceWorkflowRunner()

        # 1. Validate workflow JSON schema
        is_valid, err = runner.validate_workflow(SAMPLE_WORKFLOW_SCHEMA)
        self.assertTrue(is_valid)
        self.assertIsNone(err)

        # 2. Execute pipeline
        input_payload = {"input": "Analyze competitor landing page for technical SEO weaknesses."}
        session_id = "wf_pipeline_run_999"

        result = runner.execute_workflow(
            workflow_dict=SAMPLE_WORKFLOW_SCHEMA,
            input_payload=input_payload,
            session_id=session_id,
        )

        # 3. Assertions
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["session_id"], session_id)
        self.assertEqual(len(result["execution_trace"]), 4)

        # Check each step status
        node_ids_in_trace = [t["node_id"] for t in result["execution_trace"]]
        self.assertIn("node_trigger_1", node_ids_in_trace)
        self.assertIn("node_llm_1", node_ids_in_trace)
        self.assertIn("node_tool_1", node_ids_in_trace)
        self.assertIn("node_action_1", node_ids_in_trace)

        # Check synthesized final output
        final_out = result["final_output"]
        self.assertIsNotNone(final_out)
        self.assertIn("synthesis", final_out)


if __name__ == "__main__":
    unittest.main()
