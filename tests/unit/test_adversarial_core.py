"""
Adversarial Stress Test Suite for OmniAgent Core (Milestone 1).
Targets:
1. ProviderFactory aliases, unknown providers, invalid configs, and wire response corruption.
2. Memory limits under heavy load (SlidingWindowMemory, SummaryMemory, SemanticMemory).
3. SQLiteStateStore & FileStateStore ACID persistence, multi-threaded concurrency, SQL injection, and large payloads.
4. ToolRouter, ReflectionEngine, and MultiStepPlanner failure handling, exceptions, cycles, and missing dependencies.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from typing import Any, Dict, List, Optional

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
from omniagent.core.providers.base import BaseLLMProvider
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.providers.openai_provider import OpenAIProvider, MockOpenAIProvider
from omniagent.core.providers.anthropic_provider import AnthropicProvider, MockAnthropicProvider
from omniagent.core.providers.gemini_provider import GeminiProvider, MockGeminiProvider
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
from omniagent.core.planner import (
    ExecutionPlan,
    MultiStepPlanner,
    PlanStep,
    ReflectionEngine,
    StepStatus,
    resolve_parameter_reference,
    resolve_template,
)


class TestProviderFactoryAdversarial(unittest.TestCase):
    """Adversarial stress tests for ProviderFactory and wire normalization."""

    def test_unknown_provider_names(self):
        """Verify unknown or bogus provider names raise ValueError with clear error message."""
        bogus_names = [
            "nonexistent_llm",
            "llama3_local",
            "deepseek_custom",
            "random_vendor_xyz",
            "12345",
        ]
        for name in bogus_names:
            with self.subTest(provider_name=name):
                with self.assertRaises(ValueError) as ctx:
                    ProviderFactory.create(name)
                self.assertIn("Unknown provider", str(ctx.exception))
                self.assertIn("Supported providers", str(ctx.exception))

                # Same for create_mock
                with self.assertRaises(ValueError):
                    ProviderFactory.create_mock(name)

    def test_empty_and_whitespace_provider_names(self):
        """Verify empty strings and whitespace-only strings are rejected."""
        invalids = ["", "   ", "\t", "\n"]
        for inv in invalids:
            with self.subTest(invalid=repr(inv)):
                with self.assertRaises(ValueError):
                    ProviderFactory.create(inv)

    def test_case_insensitivity_and_whitespace_stripping(self):
        """Verify factory normalizes uppercase, mixed case, and padding."""
        test_cases = [
            ("  OPENAI  ", "openai"),
            ("OpEnAi", "openai"),
            ("  cLaUdE  ", "anthropic"),
            ("CLAUDE", "anthropic"),
            ("  gEmInI  ", "gemini"),
            ("GEMINI", "gemini"),
            ("  GPT  ", "openai"),
            ("  chatgpt ", "openai"),
            ("  VERTEX  ", "gemini"),
            ("  gemini-pro  ", "gemini"),
            ("  GOOGLE  ", "gemini"),
        ]
        for input_name, expected_canonical in test_cases:
            with self.subTest(input_name=input_name):
                provider = ProviderFactory.create_mock(input_name)
                self.assertEqual(provider.provider_name, expected_canonical)

    def test_custom_provider_registration_lifecycle(self):
        """Verify registering custom provider classes and using them through factory."""
        class DummyCustomProvider(BaseLLMProvider):
            @property
            def provider_name(self) -> str:
                return "custom_dummy"

            def format_tools(self, tools: List[ToolDefinition]) -> Any:
                return []

            def normalize_request(self, messages: List[Message], **kwargs) -> Dict[str, Any]:
                return {"test": True}

            def normalize_response(self, raw_response: Dict[str, Any]) -> LLMResponse:
                return LLMResponse(content="custom_response", provider=self.provider_name)

            def generate(self, messages: List[Message], **kwargs) -> LLMResponse:
                return LLMResponse(content="custom_generate", provider=self.provider_name)

        ProviderFactory.register_provider("custom_dummy", DummyCustomProvider)
        self.assertIn("custom_dummy", ProviderFactory.list_supported_providers())

        inst = ProviderFactory.create("custom_dummy", api_key="test-key")
        self.assertIsInstance(inst, DummyCustomProvider)
        self.assertEqual(inst.provider_name, "custom_dummy")
        resp = inst.generate([])
        self.assertEqual(resp.content, "custom_generate")

    def test_openai_normalize_response_malformed_wire_data(self):
        """Verify MockOpenAIProvider handles malformed / partial raw responses gracefully."""
        provider = MockOpenAIProvider()

        # 1. Empty dict
        resp1 = provider.normalize_response({})
        self.assertEqual(resp1.content, "")
        self.assertEqual(resp1.provider, "openai")
        self.assertEqual(resp1.finish_reason, "stop")

        # 2. None content in message
        raw2 = {"choices": [{"message": {"content": None}}]}
        resp2 = provider.normalize_response(raw2)
        self.assertEqual(resp2.content, "")

        # 3. Tool call arguments as invalid JSON string
        raw3 = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_bad",
                                "type": "function",
                                "function": {
                                    "name": "calc",
                                    "arguments": "{malformed json syntax",
                                },
                            }
                        ],
                    }
                }
            ]
        }
        resp3 = provider.normalize_response(raw3)
        self.assertTrue(resp3.has_tool_calls)
        self.assertEqual(resp3.tool_calls[0].arguments, {"_raw": "{malformed json syntax"})

        # 4. Tool call arguments as integer / non-dict non-string
        raw4 = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_int",
                                "type": "function",
                                "function": {
                                    "name": "calc",
                                    "arguments": 12345,
                                },
                            }
                        ],
                    }
                }
            ]
        }
        resp4 = provider.normalize_response(raw4)
        self.assertEqual(resp4.tool_calls[0].arguments, {})

    def test_anthropic_normalize_response_edge_cases(self):
        """Verify MockAnthropicProvider handles anomalous content blocks and stop reasons."""
        provider = MockAnthropicProvider()

        # 1. Content is a direct string rather than list of blocks
        raw1 = {"content": "Direct text string", "stop_reason": "end_turn"}
        resp1 = provider.normalize_response(raw1)
        self.assertEqual(resp1.content, "Direct text string")
        self.assertEqual(resp1.finish_reason, "stop")

        # 2. Unknown stop reason
        raw2 = {"content": [{"type": "text", "text": "Halted"}], "stop_reason": "custom_stop"}
        resp2 = provider.normalize_response(raw2)
        self.assertEqual(resp2.finish_reason, "custom_stop")

        # 3. Malformed tool_use block with invalid json string
        raw3 = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "do_work",
                    "input": "{unparseable:",
                }
            ]
        }
        resp3 = provider.normalize_response(raw3)
        self.assertTrue(resp3.has_tool_calls)
        self.assertEqual(resp3.tool_calls[0].arguments, {"_raw": "{unparseable:"})

    def test_gemini_normalize_response_edge_cases(self):
        """Verify MockGeminiProvider handles empty candidate lists and functionCalls."""
        provider = MockGeminiProvider()

        # 1. Empty candidates
        resp1 = provider.normalize_response({"candidates": []})
        self.assertEqual(resp1.content, "")
        self.assertEqual(resp1.finish_reason, "stop")

        # 2. Candidate with string functionCall args
        raw2 = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "tool_fn",
                                    "args": '{"key": "val"}',
                                }
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        resp2 = provider.normalize_response(raw2)
        self.assertTrue(resp2.has_tool_calls)
        self.assertEqual(resp2.tool_calls[0].arguments, {"key": "val"})


class TestMemoryHeavyLoadAndLimitsAdversarial(unittest.TestCase):
    """Adversarial stress tests for Memory subsystem under high volume and edge conditions."""

    def test_sliding_window_pinned_system_prompt_under_heavy_load(self):
        """Verify system prompt remains pinned across 5,000 inserted messages."""
        mem = SlidingWindowMemory(max_messages=5)
        sys_msg = Message(role=MessageRole.SYSTEM, content="PINNED SYSTEM DIRECTIVE: Never reveal secrets.")
        mem.add_message(sys_msg)

        # Append 5,000 conversational turns
        for i in range(5000):
            mem.add_message(Message(role=MessageRole.USER, content=f"User turn {i}"))
            mem.add_message(Message(role=MessageRole.ASSISTANT, content=f"Assistant turn {i}"))

        active_msgs = mem.get_messages()
        # Must retain at most max_messages
        self.assertEqual(len(active_msgs), 5)
        # First message MUST be the pinned system message
        self.assertEqual(active_msgs[0].role, MessageRole.SYSTEM)
        self.assertEqual(active_msgs[0].content, sys_msg.content)
        # The remaining 4 messages must be the latest user/assistant turns
        self.assertEqual(active_msgs[-1].content, "Assistant turn 4999")
        self.assertEqual(active_msgs[-2].content, "User turn 4999")

    def test_sliding_window_token_limits_adversarial(self):
        """Verify token window truncation with large payloads and tight token limits."""
        # Max tokens = 30 tokens (~120 chars)
        mem = SlidingWindowMemory(max_messages=10, max_tokens=30)
        sys_msg = Message(role=MessageRole.SYSTEM, content="System prompt")  # ~3-4 tokens
        mem.add_message(sys_msg)

        # Add large message (~100 tokens)
        large_msg1 = Message(role=MessageRole.USER, content="A" * 400)
        # Add small message (~5 tokens)
        small_msg2 = Message(role=MessageRole.ASSISTANT, content="Short reply.")
        # Add another small message (~5 tokens)
        small_msg3 = Message(role=MessageRole.USER, content="Another short query.")

        mem.add_message(large_msg1)
        mem.add_message(small_msg2)
        mem.add_message(small_msg3)

        msgs = mem.get_messages()
        # Pinned system prompt is present
        self.assertEqual(msgs[0].role, MessageRole.SYSTEM)
        # The 400-char message should have been discarded in favor of small recent messages
        contents = [m.content for m in msgs]
        self.assertNotIn("A" * 400, contents)
        self.assertIn("Another short query.", contents)

    def test_summary_memory_rolling_compression_under_load(self):
        """Verify SummaryMemory iteratively compresses 200 messages without exploding."""
        mem = SummaryMemory(max_unsummarized_messages=4)
        sys_msg = Message(role=MessageRole.SYSTEM, content="You are a data assistant.")
        mem.add_message(sys_msg)

        for i in range(200):
            mem.add_message(Message(role=MessageRole.USER, content=f"Step request #{i} for analysis"))
            mem.add_message(Message(role=MessageRole.ASSISTANT, content=f"Step answer #{i} with results"))

        active_msgs = mem.get_messages()
        # Should have the system message (with integrated summary) + unsummarized messages
        self.assertTrue(len(active_msgs) <= 5)
        # First message is SYSTEM with summary text
        self.assertEqual(active_msgs[0].role, MessageRole.SYSTEM)
        self.assertIn("Summary of previous conversation:", active_msgs[0].content)
        self.assertTrue(len(mem.summary) > 0)

    def test_summary_memory_provider_failure_fallback(self):
        """Verify SummaryMemory handles LLM provider exceptions during compression gracefully."""
        class FailingProvider(BaseLLMProvider):
            @property
            def provider_name(self) -> str:
                return "failing"
            def format_tools(self, tools): return []
            def normalize_request(self, messages, **kwargs): return {}
            def normalize_response(self, raw): return LLMResponse()
            def generate(self, messages, **kwargs):
                raise RuntimeError("Simulated API rate-limit exhaustion 429")

        mem = SummaryMemory(max_unsummarized_messages=2, provider=FailingProvider())
        mem.add_message(Message(role=MessageRole.USER, content="Hello 1"))
        mem.add_message(Message(role=MessageRole.ASSISTANT, content="Hello 2"))
        # This 3rd message triggers _compress(), where FailingProvider raises RuntimeError
        mem.add_message(Message(role=MessageRole.USER, content="Hello 3"))

        # Compression should not crash; it should fall back to deterministic distillation
        self.assertTrue(len(mem.summary) > 0)
        self.assertIn("[user]: Hello 1", mem.summary)

    def test_semantic_memory_high_volume_and_edge_queries(self):
        """Verify SemanticMemory handles 500 entries, stop-words only, and weird unicode."""
        mem = SemanticMemory()

        # Populate 500 entries
        for i in range(500):
            mem.store(
                key=f"item_{i}",
                content=f"Record {i}: specialized scientific calculation relating to protein folding and physics index {i}",
                metadata={"index": i},
            )

        # 1. Search with only stop words -> must return empty list without crash
        stop_results = mem.search("the a is in it for on with at", top_k=5)
        self.assertEqual(stop_results, [])

        # 2. Search with empty string or whitespace
        empty_results = mem.search("   ", top_k=5)
        self.assertEqual(empty_results, [])

        # 3. Search with special symbols and unicode
        sym_results = mem.search("@#$%^&*()_+~`", top_k=5)
        self.assertEqual(sym_results, [])

        # 4. Search for specific keyword
        hits = mem.search("protein folding", top_k=5)
        self.assertTrue(len(hits) > 0)
        self.assertIn("protein", hits[0]["content"])
        self.assertIn("folding", hits[0]["content"])

        # 5. Exact match bonus verification
        exact_hits = mem.search("specialized scientific calculation", top_k=1)
        self.assertTrue(len(exact_hits) > 0)
        self.assertGreaterEqual(exact_hits[0]["score"], 0.5)


class TestSQLiteTransactionsAndConcurrencyAdversarial(unittest.TestCase):
    """Adversarial stress tests for SQLiteStateStore ACID persistence and concurrency."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="omni_sqlite_test_")
        self.db_path = os.path.join(self.temp_dir, "test_acid.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persistence_across_process_restarts(self):
        """Verify state is preserved when store is completely closed and re-opened."""
        # Session 1: Create and write
        store1 = SQLiteStateStore(db_path=self.db_path)
        state1 = AgentState(
            session_id="session_restart_01",
            status="RUNNING",
            messages=[Message(role=MessageRole.USER, content="Persist this prompt")],
            scratchpad={"step1_result": [10, 20, 30]},
        )
        store1.save(state1)
        del store1

        # Session 2: Fresh instance pointing to the same file
        store2 = SQLiteStateStore(db_path=self.db_path)
        loaded = store2.load("session_restart_01")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, "session_restart_01")
        self.assertEqual(loaded.status, "RUNNING")
        self.assertEqual(len(loaded.messages), 1)
        self.assertEqual(loaded.messages[0].content, "Persist this prompt")
        self.assertEqual(loaded.scratchpad["step1_result"], [10, 20, 30])

    def test_high_concurrency_multi_threaded_writes(self):
        """Stress test SQLiteStateStore with 10 concurrent threads writing 200 distinct sessions."""
        store = SQLiteStateStore(db_path=self.db_path)
        num_threads = 10
        sessions_per_thread = 20
        total_sessions = num_threads * sessions_per_thread

        errors: List[Exception] = []

        def worker_write(thread_id: int):
            try:
                for i in range(sessions_per_thread):
                    sess_id = f"thread_{thread_id}_sess_{i}"
                    state = AgentState(
                        session_id=sess_id,
                        status="COMPLETED",
                        scratchpad={"thread": thread_id, "iteration": i, "token": uuid.uuid4().hex},
                    )
                    store.save(state)
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker_write, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Encountered concurrency exceptions: {errors}")

        all_sessions = store.list_sessions()
        self.assertEqual(len(all_sessions), total_sessions)

        # Verify sample states
        for t in range(num_threads):
            sample = store.load(f"thread_{t}_sess_0")
            self.assertIsNotNone(sample)
            self.assertEqual(sample.scratchpad["thread"], t)

    def test_concurrent_read_write_interleaving(self):
        """Stress test simultaneous reading, listing, and writing across threads."""
        store = SQLiteStateStore(db_path=self.db_path)
        stop_event = threading.Event()
        read_errors: List[Exception] = []
        write_errors: List[Exception] = []

        def writer_task():
            count = 0
            while not stop_event.is_set() and count < 50:
                try:
                    s = AgentState(
                        session_id=f"rw_sess_{count}",
                        status="ACTIVE",
                        scratchpad={"count": count},
                    )
                    store.save(s)
                    count += 1
                except Exception as e:
                    write_errors.append(e)

        def reader_task():
            while not stop_event.is_set():
                try:
                    store.list_sessions()
                    store.load("rw_sess_0")
                except Exception as e:
                    read_errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            w_futures = [executor.submit(writer_task) for _ in range(3)]
            r_futures = [executor.submit(reader_task) for _ in range(3)]

            # Wait for writers to complete
            for f in w_futures:
                f.result()
            stop_event.set()
            for f in r_futures:
                f.result()

        self.assertEqual(write_errors, [])
        self.assertEqual(read_errors, [])

    def test_sql_injection_resistance(self):
        """Verify malicious session_id strings cannot execute SQL injection attacks."""
        store = SQLiteStateStore(db_path=self.db_path)
        malicious_session_ids = [
            "session'; DROP TABLE agent_states; --",
            "test' OR '1'='1",
            "'; UPDATE agent_states SET status='HACKED'; --",
            "admin'--",
            'session"; DROP TABLE agent_states; --',
        ]

        for mal_id in malicious_session_ids:
            with self.subTest(malicious_id=mal_id):
                state = AgentState(session_id=mal_id, status="TESTING", scratchpad={"safe": True})
                store.save(state)

                # Verify loaded state matches exact string without table corruption
                loaded = store.load(mal_id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.session_id, mal_id)
                self.assertEqual(loaded.status, "TESTING")

        # Confirm table still exists and contains the rows
        all_sessions = store.list_sessions()
        self.assertGreaterEqual(len(all_sessions), len(malicious_session_ids))

    def test_large_state_payload(self):
        """Verify handling of large states (500KB JSON scratchpad)."""
        store = SQLiteStateStore(db_path=self.db_path)
        large_dict = {f"key_{i}": "X" * 1000 for i in range(500)}  # ~500 KB payload
        state = AgentState(
            session_id="large_payload_session",
            status="LARGE",
            scratchpad=large_dict,
        )
        store.save(state)

        loaded = store.load("large_payload_session")
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.scratchpad), 500)
        self.assertEqual(loaded.scratchpad["key_0"], "X" * 1000)

    def test_file_state_store_directory_traversal_sanitization(self):
        """Verify FileStateStore sanitizes path traversal attempts in session_id."""
        file_store_dir = os.path.join(self.temp_dir, "file_sessions")
        store = FileStateStore(directory=file_store_dir)

        malicious_id = "../../../traversal_test"
        state = AgentState(session_id=malicious_id, status="TRAP")
        store.save(state)

        # File must be confined within file_store_dir, never escape
        saved_files = list(store.directory.glob("*.json"))
        self.assertTrue(len(saved_files) == 1)
        self.assertEqual(saved_files[0].parent.resolve(), store.directory.resolve())


class TestReflectionEngineAndPlannerFailureAdversarial(unittest.TestCase):
    """Adversarial stress tests for ToolRouter, ReflectionEngine, and MultiStepPlanner."""

    def test_tool_router_handles_unhandled_tool_exception(self):
        """Verify ToolRouter intercepts tool exceptions without crashing."""
        registry = ToolRegistry()

        def crashing_fn(x: int):
            if x == 0:
                raise ZeroDivisionError("division by zero in tool")
            return 100 / x

        tool = FunctionTool(
            name="divider",
            description="Divides 100 by x",
            fn=crashing_fn,
            parameters_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        )
        registry.register(tool)

        # Also register a raw BaseTool that directly raises in execute()
        class CrashingBaseTool(BaseTool):
            @property
            def name(self) -> str: return "raw_crasher"
            @property
            def description(self) -> str: return "Crashes inside execute()"
            @property
            def parameters_schema(self) -> Dict[str, Any]: return {}
            def execute(self, **kwargs: Any) -> ToolResult:
                raise RuntimeError("Raw unhandled crash inside execute")

        registry.register(CrashingBaseTool())
        router = ToolRouter(registry)

        # 1. Dispatch FunctionTool causing ZeroDivisionError
        call1 = ToolCall(id="c1", name="divider", arguments={"x": 0})
        res1 = router.route(call1)
        self.assertFalse(res1.success)
        self.assertIsNone(res1.output)
        self.assertIn("division by zero in tool", res1.error)

        # 2. Dispatch CrashingBaseTool causing unhandled execute() exception
        call2 = ToolCall(id="c2", name="raw_crasher", arguments={})
        res2 = router.route(call2)
        self.assertFalse(res2.success)
        self.assertIsNone(res2.output)
        self.assertIn("Tool execution exception", res2.error)
        self.assertIn("Raw unhandled crash inside execute", res2.error)

    def test_tool_router_schema_validation_failures(self):
        """Verify ToolRouter rejects calls violating parameter JSON schema."""
        registry = ToolRegistry()
        tool = FunctionTool(
            name="strict_tool",
            description="Requires string title and positive integer count",
            fn=lambda title, count: f"{title}:{count}",
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "count": {"type": "integer", "minimum": 1},
                },
                "required": ["title", "count"],
            },
        )
        registry.register(tool)
        router = ToolRouter(registry)

        # 1. Missing required parameter
        call_missing = ToolCall(id="c2", name="strict_tool", arguments={"title": "Test"})
        res_missing = router.route(call_missing)
        self.assertFalse(res_missing.success)
        self.assertIn("Parameter validation error", res_missing.error)

        # 2. Type mismatch (passed string for integer)
        call_type = ToolCall(id="c3", name="strict_tool", arguments={"title": "Test", "count": "five"})
        res_type = router.route(call_type)
        self.assertFalse(res_type.success)
        self.assertIn("Parameter validation error", res_type.error)

        # 3. Nonexistent tool
        call_ghost = ToolCall(id="c4", name="ghost_tool", arguments={})
        res_ghost = router.route(call_ghost)
        self.assertFalse(res_ghost.success)
        self.assertIn("not found in registry", res_ghost.error)

    def test_reflection_engine_trace_generation(self):
        """Verify ReflectionEngine produces proper <think> tags for success and failure."""
        engine = ReflectionEngine()
        step = PlanStep(step_id=1, title="Test Step")

        # Success case
        success_res = ToolResult(success=True, output={"status": "ok"})
        ok, ok_trace = engine.evaluate_step(step, success_res)
        self.assertTrue(ok)
        self.assertIn("<think>", ok_trace)
        self.assertIn("completed successfully", ok_trace)
        self.assertIn("</think>", ok_trace)

        # Failure case
        fail_res = ToolResult(success=False, output=None, error="Network timeout 504")
        fail, fail_trace = engine.evaluate_step(step, fail_res)
        self.assertFalse(fail)
        self.assertIn("<think>", fail_trace)
        self.assertIn("failed", fail_trace)
        self.assertIn("Network timeout 504", fail_trace)
        self.assertIn("</think>", fail_trace)

    def test_planner_step_failure_skips_dependents_and_reports_cleanly(self):
        """Verify planner halts execution of dependent steps if parent step fails."""
        registry = ToolRegistry()

        def fail_tool():
            raise RuntimeError("Database connection unreachable")

        def dependent_tool(val: str):
            return f"Processed: {val}"

        registry.register(FunctionTool("fail_tool", "Always fails", fail_tool))
        registry.register(FunctionTool("dep_tool", "Depends on fail_tool", dependent_tool))
        router = ToolRouter(registry)

        planner = MultiStepPlanner(router=router)

        step1 = PlanStep(step_id=1, title="Failing Initial Step", tool_name="fail_tool")
        step2 = PlanStep(
            step_id=2,
            title="Dependent Step",
            tool_name="dep_tool",
            tool_input_template={"val": "$step1.output"},
            dependencies=[1],
        )

        plan = ExecutionPlan(goal="Test cascade failure", steps=[step1, step2])
        executed_plan = planner.execute_plan(plan)

        self.assertEqual(executed_plan.status, "FAILED")
        self.assertEqual(step1.status, StepStatus.FAILED)
        self.assertIn("Database connection unreachable", step1.error)

        # Step 2 must be skipped due to dependency failure
        self.assertEqual(step2.status, StepStatus.SKIPPED)
        self.assertIn("Skipped because prerequisite dependency failed", step2.error)
        self.assertIn("Plan execution failed", executed_plan.final_synthesis)

    def test_planner_detects_cyclic_dependencies_without_hanging(self):
        """Verify planner detects cyclic dependencies (A -> B -> A) and terminates."""
        registry = ToolRegistry()
        registry.register(FunctionTool("dummy", "Dummy", lambda: "ok"))
        router = ToolRouter(registry)
        planner = MultiStepPlanner(router=router)

        step1 = PlanStep(step_id=1, title="Step A", tool_name="dummy", dependencies=[2])
        step2 = PlanStep(step_id=2, title="Step B", tool_name="dummy", dependencies=[1])

        plan = ExecutionPlan(goal="Test cycle detection", steps=[step1, step2])

        start_time = time.time()
        executed_plan = planner.execute_plan(plan)
        elapsed = time.time() - start_time

        # Must terminate rapidly (< 1 second)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(executed_plan.status, "FAILED")
        self.assertEqual(step1.status, StepStatus.FAILED)
        self.assertEqual(step2.status, StepStatus.FAILED)
        self.assertIn("cycle detected", step1.error.lower())

    def test_parameter_interpolation_unresolved_references_resilience(self):
        """Verify parameter template resolution handles non-existent steps and paths safely."""
        scratchpad = {"existing_key": 999}
        step_outputs = {1: {"data": [1, 2, 3]}}

        # Non-existent step reference -> returns None
        res_nonexistent = resolve_parameter_reference("$step99.output", scratchpad, step_outputs)
        self.assertIsNone(res_nonexistent)

        # Non-existent sub-property -> returns None
        res_bad_sub = resolve_parameter_reference("$step1.output.missing_field", scratchpad, step_outputs)
        self.assertIsNone(res_bad_sub)

        # Embedded reference with nonexistent key -> leaves token intact
        res_embedded = resolve_parameter_reference("Prefix $step99.output Suffix", scratchpad, step_outputs)
        self.assertEqual(res_embedded, "Prefix $step99.output Suffix")

        # Recursive template resolution with nested structures
        nested_template = {
            "a": "$step1.output.data",
            "b": ["$scratchpad.existing_key", "$step99.output"],
            "c": {"inner": "$step1.output"},
        }
        resolved = resolve_template(nested_template, scratchpad, step_outputs)
        self.assertEqual(resolved["a"], [1, 2, 3])
        self.assertEqual(resolved["b"], [999, None])
        self.assertEqual(resolved["c"]["inner"], {"data": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
