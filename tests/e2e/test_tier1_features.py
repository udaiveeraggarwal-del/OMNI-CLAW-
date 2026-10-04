"""
Tier 1: Comprehensive Feature Coverage E2E Test Suite.
Validates >=5 discrete test cases per major architectural feature across:
- Core Engine Initialization & Canonical Models
- LLM Providers (OpenAI, Anthropic, Gemini, Mock)
- Memory Sliding Window & Buffers
- State Persistence (InMemory, File, SQLite ACID)
- Tool Routing & Schema Validation
- UI Workflow Export & Import
- Browser Operate Skill
- Code Runner Skill
- Odoo Builder Skill
- Social Media Skill
- SEO Optimizer Skill
- Privacy Encryption & SOCKS5 Tunneling
"""

from __future__ import annotations

import base64
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
    ToolDefinition,
    ToolResult,
)
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.providers.openai_provider import MockOpenAIProvider
from omniagent.core.providers.anthropic_provider import MockAnthropicProvider
from omniagent.core.providers.gemini_provider import MockGeminiProvider
from omniagent.core.memory import (
    SlidingWindowMemory,
    SummaryMemory,
    SemanticMemory,
)
from omniagent.core.state import (
    AgentState,
    InMemoryStateStore,
    FileStateStore,
    SQLiteStateStore,
)
from omniagent.core.router import (
    BaseTool,
    FunctionTool,
    ToolRegistry,
    ToolRouter,
)
from tests.e2e.conftest import (
    MockSOCKS5Server,
    SAMPLE_HTML_PAGE,
    SAMPLE_WORKFLOW_SCHEMA,
)
from tests.e2e.contract_oracles import (
    ReferenceCrypto,
    ReferenceOnionCell,
    ReferenceOnionRouter,
    ReferenceFingerprintScrubber,
    ReferenceBrowserOperate,
    ReferenceCodeRunner,
    ReferenceOdooBuilder,
    ReferenceSocialMedia,
    ReferenceSEOOptimizer,
    ReferenceWorkflowRunner,
    hkdf_sha256,
)


class TestTier1FeatureCoverage(unittest.TestCase):
    """Tier 1: Feature Coverage test cases (>=5 per feature)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="omni_e2e_t1_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Feature 1: Core Engine Initialization & Canonical Models (6 tests)
    # --------------------------------------------------------------------------

    def test_message_creation_and_role_parsing(self):
        """Test canonical Message initialization and role string normalization."""
        msg_user = Message(role=MessageRole.USER, content="Hello")
        self.assertEqual(msg_user.role, MessageRole.USER)
        self.assertEqual(msg_user.content, "Hello")

        # Parsing from string alias
        msg_sys = Message(role="sys", content="Be concise")
        self.assertEqual(msg_sys.role, MessageRole.SYSTEM)

        msg_ai = Message(role="ai", content="Sure thing")
        self.assertEqual(msg_ai.role, MessageRole.ASSISTANT)

    def test_message_serialization_roundtrip(self):
        """Test Message serialization to dict and restoration from dict."""
        tc = ToolCall(id="call_99", name="calculator", arguments={"a": 5, "b": 10})
        original = Message(
            role=MessageRole.ASSISTANT,
            content="Calculating sum",
            tool_calls=[tc],
            tool_call_id="call_99",
        )
        data = original.to_dict()
        restored = Message.from_dict(data)

        self.assertEqual(restored.role, original.role)
        self.assertEqual(restored.content, original.content)
        self.assertEqual(len(restored.tool_calls), 1)
        self.assertEqual(restored.tool_calls[0].name, "calculator")
        self.assertEqual(restored.tool_calls[0].arguments, {"a": 5, "b": 10})

    def test_tool_call_deserialization_and_args(self):
        """Test ToolCall handles json strings and dictionary arguments robustly."""
        # JSON string auto-deserialization
        tc_json = ToolCall(id="c1", name="search", arguments='{"query": "omniagent"}')
        self.assertEqual(tc_json.arguments, {"query": "omniagent"})

        # Dict argument
        tc_dict = ToolCall(id="c2", name="filter", arguments={"limit": 25})
        self.assertEqual(tc_dict.arguments["limit"], 25)

        # Dict roundtrip
        tc_dict_data = tc_dict.to_dict()
        restored = ToolCall.from_dict(tc_dict_data)
        self.assertEqual(restored.id, "c2")
        self.assertEqual(restored.name, "filter")

    def test_token_usage_accounting(self):
        """Test TokenUsage auto-computes totals and serializes correctly."""
        usage = TokenUsage(prompt_tokens=150, completion_tokens=75)
        self.assertEqual(usage.total_tokens, 225)

        usage_dict = usage.to_dict()
        self.assertEqual(usage_dict["total_tokens"], 225)
        restored = TokenUsage.from_dict(usage_dict)
        self.assertEqual(restored.prompt_tokens, 150)
        self.assertEqual(restored.completion_tokens, 75)

    def test_llm_response_structure_and_tool_calls(self):
        """Test LLMResponse structure, tool_calls detection, and serialization."""
        tc = ToolCall(id="c_test", name="browser_operate", arguments={"action": "navigate"})
        resp = LLMResponse(
            content="Navigating to documentation",
            tool_calls=[tc],
            finish_reason="tool_calls",
            usage=TokenUsage(prompt_tokens=50, completion_tokens=20),
            model="mock-gpt4",
            provider="openai",
        )
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(len(resp.tool_calls), 1)

        d = resp.to_dict()
        restored = LLMResponse.from_dict(d)
        self.assertEqual(restored.provider, "openai")
        self.assertEqual(restored.model, "mock-gpt4")
        self.assertTrue(restored.has_tool_calls)

    def test_network_security_context_model(self):
        """Test NetworkSecurityContext model defaults and serialization."""
        sec_ctx = NetworkSecurityContext(enabled=True, proxy_url="socks5://127.0.0.1:9055")
        self.assertTrue(sec_ctx.enabled)
        self.assertEqual(sec_ctx.proxy_url, "socks5://127.0.0.1:9055")
        self.assertTrue(sec_ctx.route_dns_remotely)
        self.assertTrue(sec_ctx.scrub_fingerprints)

        d = sec_ctx.to_dict()
        self.assertEqual(d["proxy_url"], "socks5://127.0.0.1:9055")

    # --------------------------------------------------------------------------
    # Feature 2: LLM Providers Normalization (6 tests)
    # --------------------------------------------------------------------------

    def test_mock_openai_provider_normalization(self):
        """Test MockOpenAIProvider request normalization and response generation."""
        provider = MockOpenAIProvider(api_key="mock-key", model="gpt-4o")
        msgs = [Message(role=MessageRole.USER, content="Explain quantum computing")]
        payload = provider.normalize_request(messages=msgs, temperature=0.5)

        self.assertIn("messages", payload)
        self.assertEqual(payload["model"], "gpt-4o")
        self.assertEqual(payload["temperature"], 0.5)

        resp = provider.generate(messages=msgs)
        self.assertIsInstance(resp, LLMResponse)
        self.assertEqual(resp.provider, "openai")
        self.assertTrue(len(resp.content) > 0)

    def test_mock_anthropic_provider_normalization(self):
        """Test MockAnthropicProvider messages schema normalization."""
        provider = MockAnthropicProvider(api_key="mock-key", model="claude-3-5-sonnet")
        msgs = [
            Message(role=MessageRole.SYSTEM, content="Act as researcher"),
            Message(role=MessageRole.USER, content="Summarize findings"),
        ]
        payload = provider.normalize_request(messages=msgs)

        self.assertIn("system", payload)
        self.assertEqual(payload["system"], "Act as researcher")
        self.assertIn("messages", payload)
        self.assertEqual(payload["messages"][0]["role"], "user")

        resp = provider.generate(messages=msgs)
        self.assertEqual(resp.provider, "anthropic")
        self.assertTrue(len(resp.content) > 0)

    def test_mock_gemini_provider_normalization(self):
        """Test MockGeminiProvider contents/parts schema normalization."""
        provider = MockGeminiProvider(api_key="mock-key", model="gemini-1.5-pro")
        msgs = [Message(role=MessageRole.USER, content="Create a plan")]
        payload = provider.normalize_request(messages=msgs)

        self.assertIn("contents", payload)
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "Create a plan")

        resp = provider.generate(messages=msgs)
        self.assertEqual(resp.provider, "gemini")
        self.assertTrue(len(resp.content) > 0)

    def test_provider_factory_resolution_by_name(self):
        """Test ProviderFactory creates live/mock providers by canonical name."""
        p_openai = ProviderFactory.create_mock("openai")
        self.assertEqual(p_openai.provider_name, "openai")

        p_anthropic = ProviderFactory.create_mock("anthropic")
        self.assertEqual(p_anthropic.provider_name, "anthropic")

        p_gemini = ProviderFactory.create_mock("gemini")
        self.assertEqual(p_gemini.provider_name, "gemini")

    def test_provider_factory_resolution_by_alias(self):
        """Test ProviderFactory resolves vendor aliases correctly."""
        p_gpt = ProviderFactory.create_mock("gpt")
        self.assertEqual(p_gpt.provider_name, "openai")

        p_claude = ProviderFactory.create_mock("claude")
        self.assertEqual(p_claude.provider_name, "anthropic")

        p_google = ProviderFactory.create_mock("google")
        self.assertEqual(p_google.provider_name, "gemini")

    def test_provider_factory_mock_generation(self):
        """Test ProviderFactory mock provider generates deterministic responses."""
        mock_p = ProviderFactory.create_mock("openai", model="gpt-4-turbo")
        msgs = [Message(role=MessageRole.USER, content="Hello mock test")]
        resp = mock_p.generate(messages=msgs)
        self.assertIsInstance(resp, LLMResponse)
        self.assertGreater(resp.usage.total_tokens, 0)
        self.assertIn("openai", resp.provider)

    # --------------------------------------------------------------------------
    # Feature 3: Memory Sliding Window & Buffers (5 tests)
    # --------------------------------------------------------------------------

    def test_sliding_window_memory_capacity(self):
        """Test SlidingWindowMemory bounds message count to max_messages."""
        mem = SlidingWindowMemory(max_messages=3)
        for i in range(6):
            mem.add_message(Message(role=MessageRole.USER, content=f"Msg {i}"))

        active = mem.get_messages()
        self.assertEqual(len(active), 3)
        self.assertEqual(active[-1].content, "Msg 5")
        self.assertEqual(active[0].content, "Msg 3")

    def test_sliding_window_pinned_system_prompt(self):
        """Test SlidingWindowMemory always preserves initial system prompt."""
        mem = SlidingWindowMemory(max_messages=3)
        mem.add_message(Message(role=MessageRole.SYSTEM, content="System Instructions"))
        for i in range(5):
            mem.add_message(Message(role=MessageRole.USER, content=f"User Turn {i}"))

        active = mem.get_messages()
        self.assertEqual(len(active), 3)
        self.assertEqual(active[0].role, MessageRole.SYSTEM)
        self.assertEqual(active[0].content, "System Instructions")
        self.assertEqual(active[-1].content, "User Turn 4")

    def test_sliding_window_token_limit_pruning(self):
        """Test SlidingWindowMemory enforces token ceiling."""
        # Limit to ~20 tokens
        mem = SlidingWindowMemory(max_messages=10, max_tokens=20)
        # Message with ~15 tokens (60 chars)
        mem.add_message(Message(role=MessageRole.USER, content="A" * 60))
        # Another message with ~15 tokens
        mem.add_message(Message(role=MessageRole.USER, content="B" * 60))

        active = mem.get_messages()
        self.assertLessEqual(len(active), 2)

    def test_summary_memory_condensation(self):
        """Test SummaryMemory maintains conversation buffer and condensation."""
        mock_p = ProviderFactory.create_mock("openai")
        mem = SummaryMemory(max_unsummarized_messages=2, provider=mock_p)
        mem.add_message(Message(role=MessageRole.USER, content="Message 1"))
        mem.add_message(Message(role=MessageRole.ASSISTANT, content="Message 2"))
        mem.add_message(Message(role=MessageRole.USER, content="Message 3"))

        messages = mem.get_messages()
        self.assertGreaterEqual(len(messages), 1)

    def test_semantic_memory_relevance_retrieval(self):
        """Test SemanticMemory keyword overlap relevance search."""
        mem = SemanticMemory()
        mem.store("doc1", "How to configure database connection in Postgres?")
        mem.store("doc2", "What is the weather in Seattle today?")
        mem.store("doc3", "PostgreSQL database pool timeout configuration")

        results = mem.search(query="Postgres database pool", top_k=2)
        self.assertEqual(len(results), 2)
        self.assertTrue(any("PostgreSQL" in r["content"] for r in results))

    # --------------------------------------------------------------------------
    # Feature 4: State Persistence (5 tests)
    # --------------------------------------------------------------------------

    def test_in_memory_state_store_lifecycle(self):
        """Test InMemoryStateStore save, load, delete, and list operations."""
        store = InMemoryStateStore()
        state = AgentState(session_id="sess_1", status="RUNNING")
        state.add_message(Message(role=MessageRole.USER, content="Init query"))
        state.update_scratchpad("counter", 42)

        store.save(state)
        loaded = store.load("sess_1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, "sess_1")
        self.assertEqual(loaded.scratchpad["counter"], 42)
        self.assertEqual(len(loaded.messages), 1)

        sessions = store.list_sessions()
        self.assertIn("sess_1", sessions)

        deleted = store.delete("sess_1")
        self.assertTrue(deleted)
        self.assertIsNone(store.load("sess_1"))

    def test_file_state_store_persistence(self):
        """Test FileStateStore persists JSON files to disk."""
        store = FileStateStore(directory=self.temp_dir)
        state = AgentState(session_id="file_sess", status="COMPLETED")
        state.update_scratchpad("result", "success_value")

        store.save(state)
        file_path = os.path.join(self.temp_dir, "file_sess.json")
        self.assertTrue(os.path.exists(file_path))

        # Re-load from fresh store instance
        store2 = FileStateStore(directory=self.temp_dir)
        loaded = store2.load("file_sess")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.scratchpad["result"], "success_value")

    def test_sqlite_state_store_acid(self):
        """Test SQLiteStateStore transactional storage and schema initialization."""
        db_path = os.path.join(self.temp_dir, "omni_test.db")
        store = SQLiteStateStore(db_path=db_path)

        state = AgentState(session_id="sqlite_sess", status="RUNNING")
        state.add_message(Message(role=MessageRole.USER, content="DB test message"))
        store.save(state)

        loaded = store.load("sqlite_sess")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, "RUNNING")
        self.assertEqual(len(loaded.messages), 1)

        # Update state
        state.status = "SUCCESS"
        store.save(state)
        updated = store.load("sqlite_sess")
        self.assertEqual(updated.status, "SUCCESS")

    def test_agent_state_scratchpad_and_touch(self):
        """Test AgentState scratchpad mutations and timestamp touch updates."""
        state = AgentState(session_id="touch_test")
        t0 = state.updated_at
        state.update_scratchpad("task_key", "task_val")
        t1 = state.updated_at
        self.assertGreaterEqual(t1, t0)
        self.assertEqual(state.scratchpad["task_key"], "task_val")

    def test_state_store_multi_session_isolation(self):
        """Test state store maintains strict isolation across distinct sessions."""
        store = InMemoryStateStore()
        s1 = AgentState(session_id="s1", scratchpad={"owner": "alice"})
        s2 = AgentState(session_id="s2", scratchpad={"owner": "bob"})

        store.save(s1)
        store.save(s2)

        self.assertEqual(store.load("s1").scratchpad["owner"], "alice")
        self.assertEqual(store.load("s2").scratchpad["owner"], "bob")

    # --------------------------------------------------------------------------
    # Feature 5: Tool Routing & Schema Validation (5 tests)
    # --------------------------------------------------------------------------

    def test_tool_registry_registration_and_definitions(self):
        """Test ToolRegistry registers tools and produces LLM definitions."""
        registry = ToolRegistry()
        tool = ReferenceBrowserOperate()
        registry.register(tool)

        self.assertTrue(registry.has("browser_operate"))
        defs = registry.list_definitions()
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0].name, "browser_operate")

    def test_tool_router_successful_dispatch(self):
        """Test ToolRouter validates input schema and routes tool invocation."""
        registry = ToolRegistry()
        registry.register(ReferenceBrowserOperate())
        router = ToolRouter(registry=registry)

        call = ToolCall(id="c1", name="browser_operate", arguments={"action": "navigate", "url": "https://example.com"})
        result = router.route(call)
        self.assertTrue(result.success)
        self.assertEqual(result.output["status"], "navigated")

    def test_tool_router_validation_error(self):
        """Test ToolRouter catches jsonschema validation errors on invalid args."""
        registry = ToolRegistry()
        registry.register(ReferenceBrowserOperate())
        router = ToolRouter(registry=registry)

        # Missing required parameter "action"
        call = ToolCall(id="c_err", name="browser_operate", arguments={"url": "https://example.com"})
        result = router.route(call)
        self.assertFalse(result.success)
        self.assertIn("error", result.error.lower())

    def test_function_tool_wrapper(self):
        """Test FunctionTool creates BaseTool from Python function."""
        def multiply(x: int, y: int) -> int:
            return x * y

        fn_tool = FunctionTool(name="multiply", description="Multiplies two numbers", fn=multiply)
        res = fn_tool.execute(x=6, y=7)
        self.assertTrue(res.success)
        self.assertEqual(res.output, 42)

    def test_tool_router_unregistered_tool(self):
        """Test ToolRouter returns clear error when invoking unknown tool."""
        router = ToolRouter(registry=ToolRegistry())
        call = ToolCall(id="c_unreg", name="non_existent_tool", arguments={})
        res = router.route(call)
        self.assertFalse(res.success)
        self.assertIn("not found", res.error.lower())

    # --------------------------------------------------------------------------
    # Feature 6: UI Workflow Export & Import (5 tests)
    # --------------------------------------------------------------------------

    def test_workflow_json_schema_validation(self):
        """Test workflow JSON schema validation passes for valid graph."""
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(SAMPLE_WORKFLOW_SCHEMA)
        self.assertTrue(is_valid)
        self.assertIsNone(err)

    def test_workflow_yaml_serialization_roundtrip(self):
        """Test workflow serialization preserves nodes, coordinates, and edges."""
        workflow_json = json.dumps(SAMPLE_WORKFLOW_SCHEMA)
        restored = json.loads(workflow_json)
        self.assertEqual(len(restored["nodes"]), 4)
        self.assertEqual(len(restored["edges"]), 3)

    def test_workflow_node_topology_mapping(self):
        """Test workflow DAG correctly maps trigger to action execution."""
        runner = ReferenceWorkflowRunner()
        result = runner.execute_workflow(
            workflow_dict=SAMPLE_WORKFLOW_SCHEMA,
            input_payload={"input": "Analyze market trends"},
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["execution_trace"]), 4)

    def test_workflow_metadata_preservation(self):
        """Test workflow metadata (name, description, schema_version) retained."""
        meta = SAMPLE_WORKFLOW_SCHEMA["metadata"]
        self.assertEqual(meta["name"], "Research & Synthesis Pipeline")
        self.assertEqual(SAMPLE_WORKFLOW_SCHEMA["schema_version"], "1.0.0")

    def test_workflow_step_execution_trace(self):
        """Test workflow execution trace tracks node statuses."""
        res = ReferenceWorkflowRunner.execute_workflow(
            workflow_dict=SAMPLE_WORKFLOW_SCHEMA,
            input_payload={},
        )
        trace_statuses = [t["status"] for t in res["execution_trace"]]
        self.assertTrue(all(s == "COMPLETED" for s in trace_statuses))

    # --------------------------------------------------------------------------
    # Feature 7: Browser Operate Skill (5 tests)
    # --------------------------------------------------------------------------

    def test_browser_operate_navigate(self):
        """Test browser_operate navigate action."""
        tool = ReferenceBrowserOperate()
        res = tool.execute(action="navigate", url="https://example.com/demo")
        self.assertTrue(res.success)
        self.assertEqual(res.output["status"], "navigated")

    def test_browser_operate_scrape_content(self):
        """Test browser_operate scrape action extracts rendered text."""
        tool = ReferenceBrowserOperate()
        tool.execute(action="navigate", html_override="<html><body><h1>Title</h1><p>Body paragraph</p></body></html>")
        res = tool.execute(action="scrape")
        self.assertTrue(res.success)
        self.assertIn("Title", res.output["text"])
        self.assertIn("Body paragraph", res.output["text"])

    def test_browser_operate_dom_interaction(self):
        """Test browser_operate fill and click actions."""
        tool = ReferenceBrowserOperate()
        fill_res = tool.execute(action="fill", selector="#search_input", value="OmniAgent query")
        self.assertTrue(fill_res.success)

        click_res = tool.execute(action="click", selector="#submit_btn")
        self.assertTrue(click_res.success)

    def test_browser_operate_screenshot(self):
        """Test browser_operate screenshot returns base64 image."""
        tool = ReferenceBrowserOperate()
        res = tool.execute(action="screenshot")
        self.assertTrue(res.success)
        self.assertIn("image_base64", res.output)

    def test_browser_operate_tool_definition(self):
        """Test browser_operate provides valid canonical ToolDefinition."""
        tool = ReferenceBrowserOperate()
        defn = tool.to_definition()
        self.assertEqual(defn.name, "browser_operate")
        self.assertIn("action", defn.parameters["properties"])

    # --------------------------------------------------------------------------
    # Feature 8: Code Runner Skill (5 tests)
    # --------------------------------------------------------------------------

    def test_code_runner_python_stdout(self):
        """Test code_runner executes Python code and captures stdout."""
        runner = ReferenceCodeRunner()
        res = runner.execute(language="python", code="print('Hello from sandbox')")
        self.assertTrue(res.success)
        self.assertEqual(res.output, "Hello from sandbox")

    def test_code_runner_math_computation(self):
        """Test code_runner executes computation and outputs JSON."""
        runner = ReferenceCodeRunner()
        script = "import json\nresult = {'sum': sum([1, 2, 3, 4, 5])}\nprint(json.dumps(result))"
        res = runner.execute(language="python", code=script)
        self.assertTrue(res.success)
        data = json.loads(res.output)
        self.assertEqual(data["sum"], 15)

    def test_code_runner_tempdir_cleanup(self):
        """Test code_runner creates and removes ephemeral sandbox directories."""
        runner = ReferenceCodeRunner()
        res = runner.execute(language="python", code="import os; print(os.getcwd())")
        self.assertTrue(res.success)
        # Sandbox path was ephemeral
        self.assertFalse(os.path.exists(res.metadata["sandbox_dir"]))

    def test_code_runner_runtime_error_capture(self):
        """Test code_runner captures script exceptions and errors."""
        runner = ReferenceCodeRunner()
        res = runner.execute(language="python", code="1 / 0")
        self.assertFalse(res.success)
        self.assertIn("ZeroDivisionError", res.error)

    def test_code_runner_multilanguage_dispatch(self):
        """Test code_runner shell execution dispatch."""
        runner = ReferenceCodeRunner()
        res = runner.execute(language="shell", code="echo OmniAgent Shell OK")
        self.assertTrue(res.success)
        self.assertIn("OmniAgent Shell OK", res.output)

    # --------------------------------------------------------------------------
    # Feature 9: Odoo Builder Skill (5 tests)
    # --------------------------------------------------------------------------

    def test_odoo_create_product(self):
        """Test odoo_builder creates eCommerce product catalog entry."""
        odoo = ReferenceOdooBuilder()
        res = odoo.execute(action="create_product", name="AI Automation Suite", price=299.0, sku="AI-AUTO-01")
        self.assertTrue(res.success)
        self.assertEqual(res.output["name"], "AI Automation Suite")
        self.assertEqual(res.output["list_price"], 299.0)

    def test_odoo_list_products(self):
        """Test odoo_builder lists all created products."""
        odoo = ReferenceOdooBuilder()
        odoo.execute(action="create_product", name="Product A")
        odoo.execute(action="create_product", name="Product B")
        res = odoo.execute(action="list_products")
        self.assertTrue(res.success)
        self.assertEqual(len(res.output), 2)

    def test_odoo_create_website_page(self):
        """Test odoo_builder creates CMS website landing page."""
        odoo = ReferenceOdooBuilder()
        res = odoo.execute(action="create_page", name="Landing Page", url="/landing", content="<h1>OmniAgent ERP</h1>")
        self.assertTrue(res.success)
        self.assertEqual(res.output["url"], "/landing")

    def test_odoo_list_pages(self):
        """Test odoo_builder retrieves website pages."""
        odoo = ReferenceOdooBuilder()
        odoo.execute(action="create_page", name="Home", url="/")
        res = odoo.execute(action="list_pages")
        self.assertTrue(res.success)
        self.assertEqual(len(res.output), 1)

    def test_odoo_tool_definition_schema(self):
        """Test odoo_builder schema definition."""
        odoo = ReferenceOdooBuilder()
        defn = odoo.to_definition()
        self.assertEqual(defn.name, "odoo_builder")
        self.assertIn("action", defn.parameters["properties"])

    # --------------------------------------------------------------------------
    # Feature 10: Social Media Skill (5 tests)
    # --------------------------------------------------------------------------

    def test_social_media_youtube_script_generation(self):
        """Test social_media generates structured 5-part YouTube script."""
        social = ReferenceSocialMedia()
        res = social.execute(action="generate_youtube_script", topic="Autonomous AI Agents", target_duration_seconds=90)
        self.assertTrue(res.success)
        script = res.output
        self.assertIn("hook", script)
        self.assertIn("intro", script)
        self.assertIn("scenes", script)
        self.assertIn("spoken_script", script)
        self.assertIn("call_to_action", script)

    def test_social_media_youtube_metadata(self):
        """Test social_media prepares YouTube title, description, and tags."""
        social = ReferenceSocialMedia()
        res = social.execute(action="generate_youtube_script", topic="DeepSeek Distillation")
        metadata = res.output["metadata"]
        self.assertIn("title", metadata)
        self.assertIn("description", metadata)
        self.assertIn("tags", metadata)

    def test_social_media_instagram_post(self):
        """Test social_media formats and publishes Instagram post."""
        social = ReferenceSocialMedia()
        res = social.execute(
            action="create_instagram_post",
            caption="Check out OmniAgent v1!",
            image_url="https://images.example.com/banner.png",
            hashtags=["#ai", "#agents", "#production"],
        )
        self.assertTrue(res.success)
        self.assertEqual(res.output["status"], "published")
        self.assertIn("#production", res.output["caption"])

    def test_social_media_analytics_retrieval(self):
        """Test social_media retrieves engagement metrics."""
        social = ReferenceSocialMedia()
        res = social.execute(action="get_analytics")
        self.assertTrue(res.success)
        self.assertIn("youtube", res.output)
        self.assertIn("instagram", res.output)

    def test_social_media_tool_definition_schema(self):
        """Test social_media tool schema definition."""
        social = ReferenceSocialMedia()
        defn = social.to_definition()
        self.assertEqual(defn.name, "social_media")

    # --------------------------------------------------------------------------
    # Feature 11: SEO Optimizer Skill (5 tests)
    # --------------------------------------------------------------------------

    def test_seo_audit_perfect_page(self):
        """Test technical SEO audit returns maximum score on well-structured page."""
        seo = ReferenceSEOOptimizer()
        res = seo.execute(action="audit_page", html_content=SAMPLE_HTML_PAGE)
        self.assertTrue(res.success)
        self.assertEqual(res.output["health_score"], 100)
        self.assertEqual(len(res.output["issues"]), 0)

    def test_seo_audit_missing_tags(self):
        """Test SEO audit detects missing title, description, and h1 tags."""
        seo = ReferenceSEOOptimizer()
        bare_html = "<html><body><div>Just a div</div></body></html>"
        res = seo.execute(action="audit_page", html_content=bare_html)
        self.assertTrue(res.success)
        self.assertLess(res.output["health_score"], 50)
        self.assertIn("Missing <title> tag", res.output["issues"])

    def test_seo_keyword_density(self):
        """Test SEO keyword density analysis."""
        seo = ReferenceSEOOptimizer()
        res = seo.execute(
            action="keyword_density",
            html_content=SAMPLE_HTML_PAGE,
            keywords=["omniagent", "autonomous", "automation"],
        )
        self.assertTrue(res.success)
        self.assertIn("omniagent", res.output["keywords"])
        self.assertGreater(res.output["keywords"]["omniagent"]["count"], 0)

    def test_seo_schema_jsonld_generation(self):
        """Test SEO Schema.org JSON-LD generation."""
        seo = ReferenceSEOOptimizer()
        res = seo.execute(action="generate_schema_jsonld", schema_type="SoftwareApplication")
        self.assertTrue(res.success)
        jsonld = res.output
        self.assertEqual(jsonld["@context"], "https://schema.org")
        self.assertEqual(jsonld["@type"], "SoftwareApplication")

    def test_seo_optimizer_tool_definition_schema(self):
        """Test seo_optimizer tool schema definition."""
        seo = ReferenceSEOOptimizer()
        defn = seo.to_definition()
        self.assertEqual(defn.name, "seo_optimizer")

    # --------------------------------------------------------------------------
    # Feature 12: Privacy Encryption & SOCKS5 Tunneling (6 tests)
    # --------------------------------------------------------------------------

    def test_crypto_aead_encrypt_decrypt_roundtrip(self):
        """Test symmetric AEAD encrypt/decrypt recovers exact plaintext."""
        key = os.urandom(32)
        plaintext = b"Confidential autonomous agent instructions."
        enc = ReferenceCrypto.aead_encrypt(key, plaintext)
        decrypted = ReferenceCrypto.aead_decrypt(key, enc)
        self.assertEqual(decrypted, plaintext)

    def test_crypto_aead_tamper_detection(self):
        """Test AEAD raises error when ciphertext is tampered."""
        key = os.urandom(32)
        plaintext = b"Sensitive message"
        enc = ReferenceCrypto.aead_encrypt(key, plaintext)
        # Corrupt ciphertext
        tampered = dict(enc)
        raw_ct = bytearray(base64.b64decode(tampered["ciphertext"]))
        raw_ct[0] ^= 0xFF
        tampered["ciphertext"] = base64.b64encode(raw_ct).decode("utf-8")

        with self.assertRaises(ValueError):
            ReferenceCrypto.aead_decrypt(key, tampered)

    def test_crypto_x25519_key_exchange(self):
        """Test ECDH X25519 keypair generation and shared secret negotiation."""
        priv_a, pub_a = ReferenceCrypto.x25519_keypair()
        priv_b, pub_b = ReferenceCrypto.x25519_keypair()

        secret_ab = ReferenceCrypto.x25519_diffie_hellman(priv_a, pub_b)
        secret_ba = ReferenceCrypto.x25519_diffie_hellman(priv_b, pub_a)

        self.assertEqual(secret_ab, secret_ba)
        self.assertEqual(len(secret_ab), 32)

    def test_crypto_hkdf_derivation(self):
        """Test HKDF-SHA256 key expansion with salt and info."""
        ikm = b"initial-shared-key-material"
        derived = hkdf_sha256(ikm=ikm, salt=b"salt-123", info=b"omniagent-aead", length=32)
        self.assertEqual(len(derived), 32)

    def test_onion_router_3hop_peeling(self):
        """Test 3-hop layered concentric onion encryption and peeling."""
        router = ReferenceOnionRouter()
        self.assertTrue(router.build_circuit())

        payload = b"GET /api/v1/resource HTTP/1.1\r\nHost: target.internal\r\n\r\n"
        onion_package = router.onion_encrypt(payload)

        # Hop 1: Entry Guard peels outer layer
        peeled_1 = router.peel_entry(onion_package)
        # Hop 2: Middle Relay peels middle layer
        peeled_2 = router.peel_middle(peeled_1)
        # Hop 3: Exit Node peels inner layer
        peeled_3 = router.peel_exit(peeled_2)

        self.assertEqual(peeled_3, payload)

    def test_socks5_loopback_proxy_handshake(self):
        """Test loopback SOCKS5 proxy handshake and remote domain parsing."""
        proxy = MockSOCKS5Server()
        proxy.start()
        try:
            # Connect to proxy
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", proxy.port))

            # 1. Handshake: VER=5, NMETHODS=1, METHOD=0x00
            s.sendall(b"\x05\x01\x00")
            resp = s.recv(2)
            self.assertEqual(resp, b"\x05\x00")

            # 2. Connect request: VER=5, CMD=1, RSV=0, ATYP=3 (domain), domain="api.openai.com", port=443
            domain = b"api.openai.com"
            req = b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + (443).to_bytes(2, "big")
            s.sendall(req)
            reply = s.recv(10)
            self.assertEqual(reply[:2], b"\x05\x00")  # SOCKS5 success
            s.close()

            self.assertEqual(proxy.last_target_host, "api.openai.com")
            self.assertEqual(proxy.last_target_port, 443)
        finally:
            proxy.stop()


if __name__ == "__main__":
    unittest.main()
