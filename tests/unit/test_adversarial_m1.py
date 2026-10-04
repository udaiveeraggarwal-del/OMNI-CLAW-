"""
Adversarial Stress Test Suite for Milestone 1 (omniagent.core).
Empirically stress-tests edge cases, extreme inputs, unexpected finish reasons,
tool execution exceptions, planner cycles, cascading failures, state concurrency,
and provider wire schema variations.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from typing import Any, Dict, List

from omniagent.core.memory import (
    SemanticMemory,
    SlidingWindowMemory,
    SummaryMemory,
)
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
from omniagent.core.planner import (
    ExecutionPlan,
    MultiStepPlanner,
    PlanStep,
    StepStatus,
    resolve_parameter_reference,
    resolve_template,
)
from omniagent.core.providers.anthropic_provider import (
    AnthropicProvider,
    MockAnthropicProvider,
)
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.providers.gemini_provider import (
    GeminiProvider,
    MockGeminiProvider,
)
from omniagent.core.providers.openai_provider import (
    MockOpenAIProvider,
    OpenAIProvider,
)
from omniagent.core.router import BaseTool, FunctionTool, ToolRegistry, ToolRouter
from omniagent.core.state import (
    AgentState,
    FileStateStore,
    InMemoryStateStore,
    SQLiteStateStore,
)


class RawExceptionTool(BaseTool):
    """Raw BaseTool that raises directly inside execute() without wrapping."""

    @property
    def name(self) -> str:
        return "raw_exception_tool"

    @property
    def description(self) -> str:
        return "A raw tool that raises an exception during execution"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **kwargs: Any) -> ToolResult:
        raise RuntimeError("Uncaught raw tool crash")


class TestExtremeInputsAndModels(unittest.TestCase):
    """Stress tests canonical models and input normalization against extreme/malformed data."""

    def test_message_with_empty_and_extreme_unicode(self):
        # Empty string
        m_empty = Message(role=MessageRole.USER, content="")
        self.assertEqual(m_empty.content, "")
        self.assertEqual(m_empty.to_dict()["content"], "")

        # Massive string (100k characters)
        big_text = "A" * 100_000
        m_big = Message(role=MessageRole.USER, content=big_text)
        self.assertEqual(len(m_big.content), 100_000)
        self.assertEqual(len(m_big.to_dict()["content"]), 100_000)

        # Unicode, emojis, RTL, special characters
        unicode_text = "🚀🔥🤖 こんにちは! مرحبا بالعالم! \n\t\r \\/\"' <think>tag</think>"
        m_uni = Message(role=MessageRole.USER, content=unicode_text)
        d = m_uni.to_dict()
        rebuilt = Message.from_dict(d)
        self.assertEqual(rebuilt.content, unicode_text)

    def test_tool_call_malformed_arguments(self):
        # Non-JSON string arguments
        tc_str = ToolCall(id="c1", name="tool", arguments="invalid json string {not valid}")
        self.assertIn("_raw", tc_str.arguments)
        self.assertEqual(tc_str.arguments["_raw"], "invalid json string {not valid}")

        # Integer / non-dict arguments
        tc_int = ToolCall(id="c2", name="tool", arguments=12345)
        self.assertEqual(tc_int.arguments, {"value": 12345})

        # None arguments
        tc_none = ToolCall(id="c3", name="tool", arguments=None)
        self.assertEqual(tc_none.arguments, {"value": None})

        # Deeply nested arguments
        nested = {"level1": {"level2": {"level3": [1, 2, {"k": "v"}]}}}
        tc_nest = ToolCall(id="c4", name="tool", arguments=nested)
        self.assertEqual(tc_nest.arguments["level1"]["level2"]["level3"][2]["k"], "v")

    def test_model_from_dict_with_explicit_none_collections(self):
        """
        Verify deserialization behavior when raw JSON dictionaries contain explicit None/null values.
        Verifies hardening: from_dict on AgentState, LLMResponse, and ExecutionPlan converts None to empty list.
        """
        # Message with null tool_calls (handled safely)
        msg = Message.from_dict({"role": "user", "content": "hi", "tool_calls": None})
        self.assertIsNone(msg.tool_calls)

        # AgentState with null messages -> converts safely to empty list
        state = AgentState.from_dict({"session_id": "s1", "messages": None})
        self.assertEqual(state.messages, [])

        # LLMResponse with null tool_calls -> converts safely to empty list
        resp = LLMResponse.from_dict({"content": "hi", "tool_calls": None})
        self.assertEqual(resp.tool_calls, [])

        # ExecutionPlan with null steps -> converts safely to empty list
        plan = ExecutionPlan.from_dict({"goal": "g", "steps": None})
        self.assertEqual(plan.steps, [])


class TestProviderWireVariationAdversarial(unittest.TestCase):
    """Stress tests provider normalization against incomplete, unexpected, and error payloads."""

    def test_openai_missing_and_unexpected_fields(self):
        p = MockOpenAIProvider()

        # 1. Empty choices
        resp1 = p.normalize_response({"choices": []})
        self.assertEqual(resp1.content, "")
        self.assertEqual(resp1.finish_reason, "stop")

        # 2. Unexpected finish reasons (e.g. content_filter, length)
        resp2 = p.normalize_response({
            "choices": [{
                "message": {"content": "filtered", "role": "assistant"},
                "finish_reason": "content_filter",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        self.assertEqual(resp2.finish_reason, "content_filter")
        self.assertEqual(resp2.content, "filtered")

        # 3. Choice with tool_calls having string arguments
        resp3 = p.normalize_response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "test_fn", "arguments": '{"query": "deepseek"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        })
        self.assertEqual(len(resp3.tool_calls), 1)
        self.assertEqual(resp3.tool_calls[0].arguments, {"query": "deepseek"})

    def test_openai_null_fields_hardening(self):
        """Verify OpenAIProvider handles null message or null usage safely."""
        p = MockOpenAIProvider()

        # When choice message is None
        resp1 = p.normalize_response({"choices": [{"message": None}]})
        self.assertEqual(resp1.content, "")
        self.assertEqual(resp1.tool_calls, [])

        # When usage is None
        resp2 = p.normalize_response({
            "choices": [{"message": {"content": "ok"}}],
            "usage": None,
        })
        self.assertEqual(resp2.content, "ok")
        self.assertEqual(resp2.usage.total_tokens, 0)

    def test_anthropic_missing_and_unexpected_fields(self):
        p = MockAnthropicProvider()

        # 1. Unexpected stop reasons (e.g. max_tokens, stop_sequence)
        resp1 = p.normalize_response({
            "content": [{"type": "text", "text": "partial text"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        self.assertEqual(resp1.finish_reason, "length")
        self.assertEqual(resp1.content, "partial text")

        # 2. Unknown stop reason preserves value
        resp2 = p.normalize_response({
            "content": [{"type": "text", "text": "custom stop"}],
            "stop_reason": "safety_block",
        })
        self.assertEqual(resp2.finish_reason, "safety_block")

    def test_anthropic_null_fields_hardening(self):
        """Verify AnthropicProvider handles null usage safely."""
        p = MockAnthropicProvider()
        resp = p.normalize_response({
            "content": [{"type": "text", "text": "hi"}],
            "usage": None,
        })
        self.assertEqual(resp.content, "hi")
        self.assertEqual(resp.usage.total_tokens, 0)

    def test_gemini_missing_and_unexpected_fields(self):
        p = MockGeminiProvider()

        # 1. Safety block / unusual finishReason
        resp1 = p.normalize_response({
            "candidates": [{
                "content": {"parts": [{"text": "unsafe block"}]},
                "finishReason": "SAFETY",
            }],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7},
        })
        self.assertEqual(resp1.finish_reason, "safety")
        self.assertEqual(resp1.content, "unsafe block")

        # 2. MAX_TOKENS finishReason
        resp2 = p.normalize_response({
            "candidates": [{
                "content": {"parts": [{"text": "truncated"}]},
                "finishReason": "MAX_TOKENS",
            }],
        })
        self.assertEqual(resp2.finish_reason, "length")

    def test_gemini_null_fields_hardening(self):
        """Verify GeminiProvider handles null finishReason or null usageMetadata safely."""
        p = MockGeminiProvider()

        # When finishReason is None
        resp1 = p.normalize_response({
            "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": None}],
        })
        self.assertEqual(resp1.finish_reason, "stop")
        self.assertEqual(resp1.content, "hi")

        # When usageMetadata is None
        resp2 = p.normalize_response({
            "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            "usageMetadata": None,
        })
        self.assertEqual(resp2.content, "hi")
        self.assertEqual(resp2.usage.total_tokens, 0)


class TestToolRouterAdversarial(unittest.TestCase):
    """Stress tests ToolRouter with schema mismatches, tool exceptions, and variadic functions."""

    def setUp(self):
        self.registry = ToolRegistry()

        def div_tool(a: float, b: float) -> float:
            return a / b

        self.registry.register(FunctionTool(
            name="div_tool",
            description="Division tool",
            fn=div_tool,
            parameters_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        ))

        def exploding_tool() -> None:
            raise RuntimeError("Fatal hardware failure simulation")

        self.registry.register(FunctionTool(
            name="exploding_tool",
            description="Explodes when called",
            fn=exploding_tool,
            parameters_schema={"type": "object"},
        ))

        self.registry.register(RawExceptionTool())
        self.router = ToolRouter(self.registry)

    def test_schema_mismatch_type_error(self):
        tc = ToolCall(id="c1", name="div_tool", arguments={"a": "not_a_number", "b": 2.0})
        res = self.router.route(tc)
        self.assertFalse(res.success)
        self.assertIn("Parameter validation error", res.error)

    def test_schema_mismatch_additional_properties(self):
        tc = ToolCall(id="c2", name="div_tool", arguments={"a": 10.0, "b": 2.0, "forbidden": True})
        res = self.router.route(tc)
        self.assertFalse(res.success)
        self.assertIn("Parameter validation error", res.error)

    def test_schema_mismatch_missing_property(self):
        tc = ToolCall(id="c3", name="div_tool", arguments={"a": 10.0})
        res = self.router.route(tc)
        self.assertFalse(res.success)
        self.assertIn("Parameter validation error", res.error)

    def test_function_tool_internal_exception_handling(self):
        # FunctionTool intercepts exceptions and populates ToolResult.error
        tc_div = ToolCall(id="c4", name="div_tool", arguments={"a": 10.0, "b": 0.0})
        res_div = self.router.route(tc_div)
        self.assertFalse(res_div.success)
        self.assertIn("division by zero", res_div.error.lower())

        tc_exp = ToolCall(id="c5", name="exploding_tool", arguments={})
        res_exp = self.router.route(tc_exp)
        self.assertFalse(res_exp.success)
        self.assertIn("Fatal hardware failure simulation", res_exp.error)

    def test_raw_base_tool_router_exception_isolation(self):
        # Raw BaseTool raises unhandled exception in execute() -> ToolRouter isolates it
        tc_raw = ToolCall(id="c6", name="raw_exception_tool", arguments={})
        res_raw = self.router.route(tc_raw)
        self.assertFalse(res_raw.success)
        self.assertIn("Tool execution exception", res_raw.error)
        self.assertIn("Uncaught raw tool crash", res_raw.error)

    def test_nonexistent_tool_safe_handling(self):
        tc = ToolCall(id="c7", name="ghost_tool", arguments={"x": 1})
        res = self.router.route(tc)
        self.assertFalse(res.success)
        self.assertIn("not found in registry", res.error)

    def test_function_tool_variadic_args_hardening(self):
        """Verify FunctionTool._auto_generate_schema excludes *args and **kwargs from properties and required."""
        def variadic_fn(*args, **kwargs):
            return "ok"

        tool = FunctionTool("variadic_tool", "desc", variadic_fn)
        schema = tool.parameters_schema
        self.assertNotIn("args", schema["properties"])
        self.assertNotIn("kwargs", schema["properties"])
        self.assertNotIn("args", schema["required"])
        self.assertNotIn("kwargs", schema["required"])

        # Also verify routing to this tool succeeds with arbitrary arguments
        self.registry.register(tool)
        res = self.router.route(ToolCall(id="c_var", name="variadic_tool", arguments={"foo": "bar"}))
        self.assertTrue(res.success)
        self.assertEqual(res.output, "ok")


class TestPlannerAdversarial(unittest.TestCase):
    """Stress tests MultiStepPlanner with cycle detection, cascading failures, and missing references."""

    def setUp(self):
        self.registry = ToolRegistry()

        def step1_tool():
            return {"items": [1, 2, 3], "status": "done"}

        def step2_tool(data: list):
            return {"sum": sum(data)}

        def broken_tool():
            raise ValueError("Upstream API unreachable")

        self.registry.register(FunctionTool("step1_tool", "S1", step1_tool))
        self.registry.register(FunctionTool("step2_tool", "S2", step2_tool, {
            "type": "object",
            "properties": {"data": {"type": "array"}},
            "required": ["data"],
        }))
        self.registry.register(FunctionTool("broken_tool", "Broken", broken_tool))
        self.router = ToolRouter(self.registry)
        self.planner = MultiStepPlanner(router=self.router)

    def test_cyclical_dependency_clean_failure(self):
        s1 = PlanStep(step_id=1, title="S1", tool_name="step1_tool", dependencies=[3])
        s2 = PlanStep(step_id=2, title="S2", tool_name="step1_tool", dependencies=[1])
        s3 = PlanStep(step_id=3, title="S3", tool_name="step1_tool", dependencies=[2])
        plan = ExecutionPlan(goal="Cycle", steps=[s1, s2, s3])

        executed = self.planner.execute_plan(plan)
        self.assertEqual(executed.status, "FAILED")
        self.assertFalse(executed.is_completed())
        for s in executed.steps:
            self.assertEqual(s.status, StepStatus.FAILED)
            self.assertIn("cycle", s.error.lower())

    def test_self_dependency_clean_failure(self):
        s1 = PlanStep(step_id=1, title="Self", tool_name="step1_tool", dependencies=[1])
        plan = ExecutionPlan(goal="Self", steps=[s1])
        executed = self.planner.execute_plan(plan)
        self.assertEqual(executed.status, "FAILED")
        self.assertEqual(s1.status, StepStatus.FAILED)

    def test_cascading_dependency_failure(self):
        # S1 fails -> S2 depends on S1 -> S3 depends on S2 -> S4 independent
        s1 = PlanStep(step_id=1, title="Broken", tool_name="broken_tool", dependencies=[])
        s2 = PlanStep(step_id=2, title="Dep1", tool_name="step2_tool", tool_input_template={"data": "$step1.output.items"}, dependencies=[1])
        s3 = PlanStep(step_id=3, title="Dep2", tool_name="step1_tool", dependencies=[2])
        s4 = PlanStep(step_id=4, title="Independent", tool_name="step1_tool", dependencies=[])

        plan = ExecutionPlan(goal="Cascade", steps=[s1, s2, s3, s4])
        executed = self.planner.execute_plan(plan)

        self.assertEqual(executed.status, "FAILED")
        self.assertEqual(s1.status, StepStatus.FAILED)
        self.assertEqual(s2.status, StepStatus.SKIPPED)
        self.assertEqual(s3.status, StepStatus.SKIPPED)
        self.assertEqual(s4.status, StepStatus.COMPLETED)

    def test_empty_plan_execution(self):
        plan = ExecutionPlan(goal="Empty", steps=[])
        executed = self.planner.execute_plan(plan)
        self.assertEqual(executed.status, "FAILED")
        self.assertFalse(executed.is_completed())

    def test_missing_parameter_reference_interpolation(self):
        scratchpad = {"batch": 1}
        step_outputs = {1: {"data": [10, 20]}}

        # Nonexistent step
        res1 = resolve_parameter_reference("$step99.output.items", scratchpad, step_outputs)
        self.assertIsNone(res1)

        # Nonexistent field on existing step
        res2 = resolve_parameter_reference("$step1.output.nonexistent", scratchpad, step_outputs)
        self.assertIsNone(res2)

        # Embedded reference to nonexistent field retains token safely
        res3 = resolve_parameter_reference("Prefix $step99.output.x Suffix", scratchpad, step_outputs)
        self.assertEqual(res3, "Prefix $step99.output.x Suffix")


class TestStatePersistenceAndMemoryAdversarial(unittest.TestCase):
    """Stress tests state persistence under concurrency, memory limits, and edge-case session IDs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sliding_window_memory_token_budget_boundary(self):
        mem = SlidingWindowMemory(max_messages=10, max_tokens=20)
        sys_msg = Message(role=MessageRole.SYSTEM, content="System Prompt")
        mem.add_message(sys_msg)
        mem.add_message(Message(role=MessageRole.USER, content="Hello 1"))
        mem.add_message(Message(role=MessageRole.USER, content="Hello 2"))
        mem.add_message(Message(role=MessageRole.USER, content="Hello 3"))

        msgs = mem.get_messages()
        self.assertEqual(msgs[0].role, MessageRole.SYSTEM)
        self.assertEqual(msgs[0].content, "System Prompt")
        self.assertLessEqual(len(msgs), 4)

    def test_summary_memory_compression_resilience(self):
        sum_mem = SummaryMemory(max_unsummarized_messages=2)
        for i in range(10):
            sum_mem.add_message(Message(role=MessageRole.USER, content=f"Step detail {i}"))
            sum_mem.add_message(Message(role=MessageRole.ASSISTANT, content=f"Result for step {i}"))

        active_msgs = sum_mem.get_messages()
        self.assertTrue(len(active_msgs) > 0)
        self.assertTrue(len(sum_mem.summary) > 0)

    def test_semantic_memory_edge_cases(self):
        sem = SemanticMemory()
        self.assertEqual(sem.search(""), [])
        self.assertEqual(sem.search("the a is and"), [])
        sem.store("k1", "Python 3.14 with asyncio and multiprocessing support 🚀")
        sem.store("k2", "Quantum computing and topological qubits")
        results = sem.search("Python 🚀", top_k=5)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["key"], "k1")

    def test_concurrent_state_persistence(self):
        """Verify thread-safe operations on InMemoryStateStore, SQLiteStateStore, and FileStateStore."""
        mem_store = InMemoryStateStore()
        db_path = os.path.join(self.temp_dir, "concurrent_test.db")
        sql_store = SQLiteStateStore(db_path=db_path)
        file_store = FileStateStore(directory=os.path.join(self.temp_dir, "concurrent_files"))

        for store in [mem_store, sql_store, file_store]:
            errors = []

            def worker(thread_idx: int):
                try:
                    for i in range(15):
                        sess = f"session_t{thread_idx}_{i}"
                        st = AgentState(session_id=sess, scratchpad={"val": i})
                        store.save(st)
                        loaded = store.load(sess)
                        if not loaded or loaded.scratchpad.get("val") != i:
                            errors.append(f"Mismatch in {store.__class__.__name__}")
                        store.delete(sess)
                except Exception as ex:
                    errors.append(f"Exception in {store.__class__.__name__}: {ex}")

            threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Concurrent errors occurred in {store.__class__.__name__}: {errors}")

    def test_file_state_store_punctuation_session_id_collision_hardening(self):
        """Verify FileStateStore maps distinct punctuation-only session IDs without collision."""
        store = FileStateStore(directory=self.temp_dir)
        s1 = AgentState(session_id="!@#$", scratchpad={"agent": 1})
        s2 = AgentState(session_id="%%%%", scratchpad={"agent": 2})

        store.save(s1)
        store.save(s2)

        l1 = store.load("!@#$")
        l2 = store.load("%%%%")

        self.assertIsNotNone(l1)
        self.assertIsNotNone(l2)
        self.assertEqual(l1.scratchpad["agent"], 1)
        self.assertEqual(l2.scratchpad["agent"], 2)

    def test_file_state_store_concurrent_same_session_safety(self):
        """Verify concurrent writes to the exact same session file do not cause PermissionError."""
        file_dir = os.path.join(self.temp_dir, "same_sess_concurrent")
        store = FileStateStore(directory=file_dir)
        shared_session = "shared_hot_session"
        errors = []

        def writer(worker_id: int):
            try:
                for i in range(10):
                    st = AgentState(session_id=shared_session, scratchpad={"writer": worker_id, "iter": i})
                    store.save(st)
                    loaded = store.load(shared_session)
                    self.assertIsNotNone(loaded)
            except Exception as ex:
                errors.append(f"Worker {worker_id} crashed: {ex}")

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Same-session concurrent errors: {errors}")


if __name__ == "__main__":
    unittest.main()
