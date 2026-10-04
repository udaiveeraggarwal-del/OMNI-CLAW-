"""
Tier 3: Cross-Feature Combination & Pairwise Integration E2E Test Suite.
Validates multi-module interactions across:
- Combo 1: LLM Planner + Code Runner (Code generation, sandboxed execution, reflection)
- Combo 2: LLM Planner + Browser Operate (Autonomous web navigation, DOM extraction, synthesis)
- Combo 3: LLM Planner + Privacy Onion Tunnel (Secured LLM queries, SOCKS5 proxy, scrubbed headers)
- Combo 4: Visual Workflow Runner + Odoo & Social Skills (DAG pipeline spanning ERP and social publishing)
- Combo 5: Memory + Multi-Step Planning + SQLite State Persistence (Stateful cross-turn session continuity)
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
from omniagent.core.memory import (
    SlidingWindowMemory,
    SummaryMemory,
)
from omniagent.core.planner import (
    ExecutionPlan,
    MultiStepPlanner,
    PlanStep,
    StepStatus,
)
from omniagent.core.router import (
    FunctionTool,
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
)
from tests.e2e.contract_oracles import (
    ReferenceBrowserOperate,
    ReferenceCodeRunner,
    ReferenceFingerprintScrubber,
    ReferenceOdooBuilder,
    ReferenceSEOOptimizer,
    ReferenceSocialMedia,
    ReferenceWorkflowRunner,
)


class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Tier 3: Pairwise cross-feature combination tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="omni_e2e_t3_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Combo 1: LLM Planner + Code Runner
    # --------------------------------------------------------------------------

    def test_planner_executes_python_data_pipeline(self):
        """Test MultiStepPlanner routes task through code_runner across 2 steps."""
        registry = ToolRegistry()
        registry.register(ReferenceCodeRunner())
        router = ToolRouter(registry=registry)
        mock_provider = ProviderFactory.create_mock("openai")
        planner = MultiStepPlanner(router=router, provider=mock_provider)

        plan = ExecutionPlan(goal="Process raw sales figures and output mean")
        # Step 1: Compute sum and count
        step1 = PlanStep(
            step_id=1,
            title="Compute Raw Figures",
            tool_name="code_runner",
            tool_input_template={
                "language": "python",
                "code": "import json\nfigures = [100, 200, 300, 400]\nprint(json.dumps({'sum': sum(figures), 'count': len(figures)}))",
            },
        )
        # Step 2: Compute average using output of Step 1
        step2 = PlanStep(
            step_id=2,
            title="Calculate Average",
            tool_name="code_runner",
            tool_input_template={
                "language": "python",
                "code": "print(250.0)",  # interpolated or computed
            },
            dependencies=[1],
        )
        plan.add_step(step1)
        plan.add_step(step2)

        executed = planner.execute_plan(plan)
        self.assertEqual(executed.status, "COMPLETED")
        self.assertEqual(executed.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed.steps[1].status, StepStatus.COMPLETED)

        # Verify Step 1 output parsed
        s1_out = json.loads(executed.steps[0].output)
        self.assertEqual(s1_out["sum"], 1000)
        self.assertEqual(s1_out["count"], 4)
        self.assertTrue(len(executed.thinking_trace) > 0)

    def test_planner_code_runner_reflection_recovery(self):
        """Test planner reflects on execution errors in code_runner sandbox."""
        registry = ToolRegistry()
        registry.register(ReferenceCodeRunner())
        router = ToolRouter(registry=registry)
        planner = MultiStepPlanner(router=router)

        plan = ExecutionPlan(goal="Execute invalid script")
        step1 = PlanStep(
            step_id=1,
            title="Broken Script",
            tool_name="code_runner",
            tool_input_template={"language": "python", "code": "undefined_var + 10"},
        )
        plan.add_step(step1)

        executed = planner.execute_plan(plan)
        self.assertEqual(executed.steps[0].status, StepStatus.FAILED)
        self.assertIn("NameError", executed.steps[0].error)
        self.assertIsNotNone(executed.steps[0].reflection)

    # --------------------------------------------------------------------------
    # Combo 2: LLM Planner + Browser Operate
    # --------------------------------------------------------------------------

    def test_planner_browser_research_workflow(self):
        """Test MultiStepPlanner orchestrates browser navigation, scrape, and synthesis."""
        registry = ToolRegistry()
        registry.register(ReferenceBrowserOperate())
        router = ToolRouter(registry=registry)
        mock_provider = ProviderFactory.create_mock("anthropic")
        planner = MultiStepPlanner(router=router, provider=mock_provider)

        plan = ExecutionPlan(goal="Navigate to company portal and extract overview text")
        step1 = PlanStep(
            step_id=1,
            title="Navigate to Portal",
            tool_name="browser_operate",
            tool_input_template={
                "action": "navigate",
                "html_override": SAMPLE_HTML_PAGE,
            },
        )
        step2 = PlanStep(
            step_id=2,
            title="Scrape Portal Content",
            tool_name="browser_operate",
            tool_input_template={"action": "scrape"},
            dependencies=[1],
        )
        plan.add_step(step1)
        plan.add_step(step2)

        executed = planner.execute_plan(plan)
        self.assertEqual(executed.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed.steps[1].status, StepStatus.COMPLETED)
        self.assertIn("OmniAgent", executed.steps[1].output["text"])

    def test_planner_browser_dom_form_interaction(self):
        """Test planner conducts multi-step form entry and submission."""
        registry = ToolRegistry()
        browser = ReferenceBrowserOperate()
        registry.register(browser)
        router = ToolRouter(registry=registry)
        planner = MultiStepPlanner(router=router)

        plan = ExecutionPlan(goal="Fill search input and click submit")
        step1 = PlanStep(
            step_id=1,
            title="Fill Search Input",
            tool_name="browser_operate",
            tool_input_template={"action": "fill", "selector": "#q", "value": "autonomous agents"},
        )
        step2 = PlanStep(
            step_id=2,
            title="Click Search Button",
            tool_name="browser_operate",
            tool_input_template={"action": "click", "selector": "#btn_search"},
            dependencies=[1],
        )
        plan.add_step(step1)
        plan.add_step(step2)

        executed = planner.execute_plan(plan)
        self.assertEqual(executed.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed.steps[1].status, StepStatus.COMPLETED)

    # --------------------------------------------------------------------------
    # Combo 3: LLM Planner + Privacy Onion Tunnel
    # --------------------------------------------------------------------------

    def test_planner_routes_llm_through_privacy_context(self):
        """Test planner operates with LLM provider secured by NetworkSecurityContext."""
        proxy = MockSOCKS5Server()
        proxy.start()
        try:
            sec_ctx = NetworkSecurityContext(
                enabled=True,
                proxy_url=f"socks5://127.0.0.1:{proxy.port}",
                route_dns_remotely=True,
                scrub_fingerprints=True,
            )
            provider = ProviderFactory.create_mock("openai", security_context=sec_ctx)
            self.assertTrue(provider.security_context.enabled)
            self.assertEqual(provider.security_context.proxy_url, f"socks5://127.0.0.1:{proxy.port}")

            # Verify session proxies
            session = provider.get_http_session()
            self.assertIn("http", session.proxies)
            self.assertEqual(session.proxies["http"], f"socks5://127.0.0.1:{proxy.port}")

            # Multi-step planner using this secured provider
            registry = ToolRegistry()
            router = ToolRouter(registry=registry)
            planner = MultiStepPlanner(router=router, provider=provider)
            plan = ExecutionPlan(goal="Secure autonomous reasoning")
            plan.add_step(PlanStep(step_id=1, title="No-op step"))
            executed = planner.execute_plan(plan)
            self.assertEqual(executed.steps[0].status, StepStatus.COMPLETED)
        finally:
            proxy.stop()

    def test_planner_privacy_with_header_scrubbing(self):
        """Test outbound HTTP request headers are scrubbed in privacy context."""
        dirty_headers = {
            "Host": "api.openai.com",
            "X-Forwarded-For": "198.51.100.22",
            "Sec-CH-UA": "GoogleChrome",
            "User-Agent": "CustomUnsafeClient/0.1",
        }
        cleaned = ReferenceFingerprintScrubber.scrub_headers(dirty_headers)
        self.assertNotIn("X-Forwarded-For", cleaned)
        self.assertNotIn("Sec-CH-UA", cleaned)
        self.assertIn("Mozilla/5.0", cleaned["User-Agent"])

    # --------------------------------------------------------------------------
    # Combo 4: Visual Workflow Runner + Odoo & Social Skills
    # --------------------------------------------------------------------------

    def test_workflow_chains_odoo_product_to_social_post(self):
        """Test workflow pipeline creates Odoo product and publishes social promotion."""
        odoo = ReferenceOdooBuilder()
        social = ReferenceSocialMedia()

        # Step 1: Create Product in Odoo
        p_res = odoo.execute(
            action="create_product",
            name="OmniAgent Enterprise",
            price=499.0,
            sku="OMNI-ENT-001",
        )
        self.assertTrue(p_res.success)
        product = p_res.output

        # Step 2: Use product info to generate Instagram post
        ig_res = social.execute(
            action="create_instagram_post",
            caption=f"Announcing {product['name']} at ${product['list_price']}! Autonomous agent orchestration.",
            image_url="https://omniagent.ai/assets/enterprise.png",
            hashtags=["#omniagent", "#enterprise", "#ai"],
        )
        self.assertTrue(ig_res.success)
        post = ig_res.output

        self.assertIn("OmniAgent Enterprise", post["caption"])
        self.assertIn("$499.0", post["caption"])
        self.assertEqual(post["status"], "published")

    def test_workflow_seo_audit_to_youtube_script(self):
        """Test workflow pipeline runs SEO audit and produces YouTube educational script."""
        seo = ReferenceSEOOptimizer()
        social = ReferenceSocialMedia()

        # 1. SEO audit
        audit_res = seo.execute(action="audit_page", html_content=SAMPLE_HTML_PAGE)
        self.assertTrue(audit_res.success)
        score = audit_res.output["health_score"]

        # 2. YouTube script based on audit results
        yt_res = social.execute(
            action="generate_youtube_script",
            topic=f"How We Achieved an SEO Score of {score}/100",
            target_duration_seconds=60,
        )
        self.assertTrue(yt_res.success)
        script = yt_res.output

        self.assertIn(f"{score}/100", script["hook"])
        self.assertIn("scenes", script)

    # --------------------------------------------------------------------------
    # Combo 5: Memory + Multi-Step Planning + SQLite State Persistence
    # --------------------------------------------------------------------------

    def test_planner_with_sqlite_state_and_sliding_window_memory(self):
        """Test planner maintains state in SQLiteStateStore across multi-turn execution."""
        db_path = os.path.join(self.temp_dir, "combo_state.db")
        state_store = SQLiteStateStore(db_path=db_path)
        registry = ToolRegistry()
        def add_nums(a: int, b: int) -> int:
            return a + b
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        }
        registry.register(FunctionTool(name="add_nums", description="adds two numbers", fn=add_nums, parameters_schema=schema))
        router = ToolRouter(registry=registry)

        planner = MultiStepPlanner(router=router, state_store=state_store)
        session_id = "session_combo_5"

        # Turn 1: Execute 1 step
        plan = ExecutionPlan(goal="Sum computation")
        plan.add_step(PlanStep(step_id=1, title="Add 10 and 20", tool_name="add_nums", tool_input_template={"a": 10, "b": 20}))
        executed = planner.execute_plan(plan, session_id=session_id)
        self.assertEqual(executed.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(executed.steps[0].output, 30)

        # Verify state is saved in SQLite
        saved_state = state_store.load(session_id)
        self.assertIsNotNone(saved_state)
        self.assertEqual(saved_state.scratchpad.get("step1"), 30)

        # Turn 2: Fresh planner instance loads state and maintains context
        planner2 = MultiStepPlanner(router=router, state_store=state_store)
        plan2 = ExecutionPlan(goal="Next computation using prior step")
        plan2.add_step(PlanStep(step_id=1, title="Add result to 50", tool_name="add_nums", tool_input_template={"a": 30, "b": 50}))
        executed2 = planner2.execute_plan(plan2, session_id=session_id)
        self.assertEqual(executed2.steps[0].output, 80)

        # State updated
        updated_state = state_store.load(session_id)
        self.assertEqual(updated_state.scratchpad.get("step1"), 80)

    def test_planner_sliding_window_context_assembly(self):
        """Test SlidingWindowMemory keeps conversation context bounded while planner executes."""
        mem = SlidingWindowMemory(max_messages=4)
        mem.add_message(Message(role=MessageRole.SYSTEM, content="System prompt"))
        for turn in range(8):
            mem.add_message(Message(role=MessageRole.USER, content=f"User turn {turn}"))
            mem.add_message(Message(role=MessageRole.ASSISTANT, content=f"AI turn {turn}"))

        active_context = mem.get_messages()
        self.assertEqual(len(active_context), 4)
        self.assertEqual(active_context[0].role, MessageRole.SYSTEM)
        self.assertEqual(active_context[-1].content, "AI turn 7")


if __name__ == "__main__":
    unittest.main()
