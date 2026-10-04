"""
Unit tests for MultiStepPlanner, Router, Memory, and State Persistence in OmniAgent.
Directly verifies Acceptance Criterion:
- A multi-step planner test script successfully routes a mock user request through at least 2 distinct tools and returns a synthesized result.
"""

import os
import shutil
import tempfile
import unittest

from omniagent.core.memory import (
    SemanticMemory,
    SlidingWindowMemory,
    SummaryMemory,
)
from omniagent.core.models import (
    Message,
    MessageRole,
    ToolCall,
    ToolResult,
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
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.router import FunctionTool, ToolRegistry, ToolRouter
from omniagent.core.state import (
    AgentState,
    FileStateStore,
    InMemoryStateStore,
    SQLiteStateStore,
)


class TestPlannerAndCoreSubsystems(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.registry = ToolRegistry()

        # Tool 1: data_fetcher
        def fetch_data(query: str):
            return {
                "source": "mock_db",
                "query": query,
                "raw_data": [10.0, 20.0, 30.0, 40.0],
            }

        fetcher = FunctionTool(
            name="data_fetcher",
            description="Fetches raw numeric dataset for a query",
            fn=fetch_data,
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        )

        # Tool 2: data_analyzer
        def analyze_data(values: list):
            nums = [float(x) for x in values]
            mean_val = sum(nums) / len(nums) if nums else 0.0
            sum_val = sum(nums)
            return {"mean": mean_val, "sum": sum_val, "count": len(nums)}

        analyzer = FunctionTool(
            name="data_analyzer",
            description="Performs statistical calculations over an array of numbers",
            fn=analyze_data,
            parameters_schema={
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                },
                "required": ["values"],
            },
        )

        self.registry.register(fetcher)
        self.registry.register(analyzer)
        self.router = ToolRouter(self.registry)
        self.mock_provider = ProviderFactory.create_mock("openai")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_acceptance_multistep_planner_routes_two_tools_and_synthesizes(self):
        """
        Acceptance Criterion Verification:
        A multi-step planner test script successfully routes a mock user request
        through at least 2 distinct tools (data_fetcher + data_analyzer) and returns a synthesized result.
        """
        planner = MultiStepPlanner(
            router=self.router,
            provider=self.mock_provider,
        )

        # Build 2-step plan with dependency and parameter reference interpolation
        step1 = PlanStep(
            step_id=1,
            title="Fetch Numbers",
            tool_name="data_fetcher",
            tool_input_template={"query": "financial_records"},
            dependencies=[],
        )
        step2 = PlanStep(
            step_id=2,
            title="Compute Statistical Summary",
            tool_name="data_analyzer",
            tool_input_template={"values": "$step1.output.raw_data"},
            dependencies=[1],
        )

        plan = ExecutionPlan(
            goal="Fetch financial records and compute statistical summary.",
            steps=[step1, step2],
        )

        executed_plan = planner.execute_plan(plan)

        # 1. Verify overall plan status
        self.assertEqual(executed_plan.status, "COMPLETED")
        self.assertTrue(executed_plan.is_completed())

        # 2. Verify Step 1 executed successfully and output contains raw_data
        s1 = executed_plan.get_step(1)
        self.assertIsNotNone(s1)
        self.assertEqual(s1.status, StepStatus.COMPLETED)
        self.assertEqual(s1.output["query"], "financial_records")
        self.assertEqual(s1.output["raw_data"], [10.0, 20.0, 30.0, 40.0])

        # 3. Verify Step 2 received interpolated output from Step 1 and executed
        s2 = executed_plan.get_step(2)
        self.assertIsNotNone(s2)
        self.assertEqual(s2.status, StepStatus.COMPLETED)
        self.assertEqual(s2.output["count"], 4)
        self.assertEqual(s2.output["mean"], 25.0)
        self.assertEqual(s2.output["sum"], 100.0)

        # 4. Verify DeepSeek R1-style reflection trace was recorded
        self.assertIsNotNone(s1.reflection)
        self.assertIn("<think>", s1.reflection)
        self.assertIn("completed successfully", s1.reflection)
        self.assertIsNotNone(s2.reflection)
        self.assertIn("<think>", s2.reflection)

        # 5. Verify final synthesis mentions both tools / outputs
        self.assertIsNotNone(executed_plan.final_synthesis)
        self.assertTrue(len(executed_plan.final_synthesis) > 0)
        # Should reference financial records or data analysis outputs
        synth_lower = executed_plan.final_synthesis.lower()
        self.assertTrue(
            "financial" in synth_lower
            or "analyzer" in synth_lower
            or "100" in synth_lower
            or "25" in synth_lower
            or "result" in synth_lower
        )

    def test_parameter_interpolation_helpers(self):
        scratchpad = {"user": "Alice", "batch_id": 99}
        step_outputs = {
            1: {"raw_data": [1, 2, 3], "status": "ok"},
            2: {"summary": {"total": 6}},
        }

        # Exact match object extraction
        val1 = resolve_parameter_reference("$step1.output.raw_data", scratchpad, step_outputs)
        self.assertEqual(val1, [1, 2, 3])

        val2 = resolve_parameter_reference("$step2.output.summary.total", scratchpad, step_outputs)
        self.assertEqual(val2, 6)

        val3 = resolve_parameter_reference("$scratchpad.user", scratchpad, step_outputs)
        self.assertEqual(val3, "Alice")

        # Embedded string interpolation
        text = "Hello $scratchpad.user, total was $step2.output.summary.total."
        res_text = resolve_parameter_reference(text, scratchpad, step_outputs)
        self.assertEqual(res_text, "Hello Alice, total was 6.")

        # Recursive template resolution
        template = {
            "values": "$step1.output.raw_data",
            "meta": {"author": "$scratchpad.user", "extra": [10, "$step2.output.summary.total"]},
        }
        res_tmpl = resolve_template(template, scratchpad, step_outputs)
        self.assertEqual(res_tmpl["values"], [1, 2, 3])
        self.assertEqual(res_tmpl["meta"]["author"], "Alice")
        self.assertEqual(res_tmpl["meta"]["extra"], [10, 6])

    def test_router_schema_validation(self):
        # Valid execution
        valid_call = ToolCall(id="c1", name="data_fetcher", arguments={"query": "test"})
        res = self.router.route(valid_call)
        self.assertTrue(res.success)
        self.assertEqual(res.output["query"], "test")

        # Invalid schema (missing required parameter 'query')
        invalid_call = ToolCall(id="c2", name="data_fetcher", arguments={"invalid_key": 123})
        bad_res = self.router.route(invalid_call)
        self.assertFalse(bad_res.success)
        self.assertIn("Parameter validation error", bad_res.error)

        # Nonexistent tool
        missing_call = ToolCall(id="c3", name="ghost_tool", arguments={})
        missing_res = self.router.route(missing_call)
        self.assertFalse(missing_res.success)
        self.assertIn("not found in registry", missing_res.error)

    def test_state_stores_acid_and_persistence(self):
        state = AgentState(
            session_id="session-test-123",
            status="RUNNING",
            scratchpad={"step1": [1, 2, 3]},
        )
        state.add_message(Message(role=MessageRole.USER, content="Hello State"))

        # 1. InMemoryStateStore
        mem_store = InMemoryStateStore()
        mem_store.save(state)
        loaded_mem = mem_store.load("session-test-123")
        self.assertIsNotNone(loaded_mem)
        self.assertEqual(loaded_mem.session_id, "session-test-123")
        self.assertEqual(len(loaded_mem.messages), 1)
        self.assertEqual(loaded_mem.scratchpad["step1"], [1, 2, 3])
        self.assertTrue(mem_store.exists("session-test-123"))
        mem_store.delete("session-test-123")
        self.assertFalse(mem_store.exists("session-test-123"))

        # 2. FileStateStore
        file_store = FileStateStore(directory=self.temp_dir)
        file_store.save(state)
        loaded_file = file_store.load("session-test-123")
        self.assertIsNotNone(loaded_file)
        self.assertEqual(loaded_file.session_id, "session-test-123")
        self.assertIn("session-test-123", file_store.list_sessions())
        file_store.delete("session-test-123")
        self.assertIsNone(file_store.load("session-test-123"))

        # 3. SQLiteStateStore
        db_path = os.path.join(self.temp_dir, "test_state.db")
        sql_store = SQLiteStateStore(db_path=db_path)
        sql_store.save(state)
        loaded_sql = sql_store.load("session-test-123")
        self.assertIsNotNone(loaded_sql)
        self.assertEqual(loaded_sql.session_id, "session-test-123")
        self.assertEqual(loaded_sql.scratchpad["step1"], [1, 2, 3])
        self.assertIn("session-test-123", sql_store.list_sessions())
        sql_store.delete("session-test-123")
        self.assertIsNone(sql_store.load("session-test-123"))

    def test_memory_subsystem(self):
        # 1. SlidingWindowMemory (pins initial system prompt)
        win_mem = SlidingWindowMemory(max_messages=3)
        win_mem.add_message(Message(role=MessageRole.SYSTEM, content="System Prompt"))
        win_mem.add_message(Message(role=MessageRole.USER, content="Msg 1"))
        win_mem.add_message(Message(role=MessageRole.ASSISTANT, content="Msg 2"))
        win_mem.add_message(Message(role=MessageRole.USER, content="Msg 3"))
        win_mem.add_message(Message(role=MessageRole.ASSISTANT, content="Msg 4"))

        msgs = win_mem.get_messages()
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0].role, MessageRole.SYSTEM)
        self.assertEqual(msgs[0].content, "System Prompt")
        self.assertEqual(msgs[-1].content, "Msg 4")

        # 2. SummaryMemory
        sum_mem = SummaryMemory(max_unsummarized_messages=2)
        sum_mem.add_message(Message(role=MessageRole.SYSTEM, content="System Instructions"))
        sum_mem.add_message(Message(role=MessageRole.USER, content="Step 1 details"))
        sum_mem.add_message(Message(role=MessageRole.ASSISTANT, content="Step 1 completed"))
        sum_mem.add_message(Message(role=MessageRole.USER, content="Step 2 details"))
        sum_mem.add_message(Message(role=MessageRole.ASSISTANT, content="Step 2 completed"))

        active_msgs = sum_mem.get_messages()
        self.assertTrue(len(active_msgs) > 0)
        self.assertTrue(len(sum_mem.summary) > 0)

        # 3. SemanticMemory
        sem_mem = SemanticMemory()
        sem_mem.store("fact_1", "OmniAgent supports OpenAI, Anthropic, and Gemini LLMs.")
        sem_mem.store("fact_2", "PostgreSQL database storage is scalable.")
        sem_mem.store("fact_3", "DeepSeek R1 reasoning provides think reflection traces.")

        results = sem_mem.search("OmniAgent supported providers", top_k=2)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["key"], "fact_1")

        r1_results = sem_mem.search("DeepSeek reasoning reflection", top_k=1)
        self.assertEqual(r1_results[0]["key"], "fact_3")


if __name__ == "__main__":
    unittest.main()
