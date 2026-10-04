"""
Tier 2: Boundary, Corner Case, and Adversarial E2E Test Suite.
Validates >=5 discrete test cases per category across:
- Empty Prompts & Minimal Inputs
- Max Token Bounds & Large Payloads
- Tool Timeouts & Child Process Termination
- SSRF Cloud Metadata Block & Network Isolation
- Invalid JSON/YAML Schemas & Malformed Inputs
- Self-Loop Connections & DAG Cycle Detection
- Secret Credential Scrubbing
- Proxy Connection Drops & Network Error Handling
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import tempfile
import time
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
from tests.e2e.conftest import MockSOCKS5Server
from tests.e2e.contract_oracles import (
    ReferenceBrowserOperate,
    ReferenceCodeRunner,
    ReferenceFingerprintScrubber,
    ReferenceWorkflowRunner,
)


class TestTier2BoundariesAndCornerCases(unittest.TestCase):
    """Tier 2: Boundary, Corner Case, and Adversarial tests (>=5 per category)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="omni_e2e_t2_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Category 1: Empty Prompts & Minimal Inputs (5 tests)
    # --------------------------------------------------------------------------

    def test_empty_prompt_string_handling(self):
        """Test provider handles empty prompt string gracefully."""
        provider = ProviderFactory.create_mock("openai")
        resp = provider.generate(messages=[Message(role=MessageRole.USER, content="")])
        self.assertIsInstance(resp, LLMResponse)
        self.assertIsNotNone(resp.content)

    def test_whitespace_only_prompt(self):
        """Test provider handles whitespace-only prompt strings."""
        provider = ProviderFactory.create_mock("anthropic")
        resp = provider.generate(messages=[Message(role=MessageRole.USER, content="   \n\t  ")])
        self.assertIsInstance(resp, LLMResponse)
        self.assertIsNotNone(resp.content)

    def test_empty_messages_list(self):
        """Test provider handles empty messages list."""
        provider = ProviderFactory.create_mock("gemini")
        resp = provider.generate(messages=[])
        self.assertIsInstance(resp, LLMResponse)

    def test_tool_call_with_empty_arguments(self):
        """Test ToolCall handles completely empty argument dict without error."""
        tc = ToolCall(id="c_empty", name="ping", arguments={})
        self.assertEqual(tc.arguments, {})
        d = tc.to_dict()
        self.assertEqual(d["arguments"], {})

    def test_code_runner_empty_code(self):
        """Test code_runner rejects empty code without spawning child process."""
        runner = ReferenceCodeRunner()
        res = runner.execute(language="python", code="   ")
        self.assertFalse(res.success)
        self.assertIn("empty", res.error.lower())

    # --------------------------------------------------------------------------
    # Category 2: Max Token Bounds & Large Payloads (5 tests)
    # --------------------------------------------------------------------------

    def test_extreme_prompt_length_truncation(self):
        """Test SlidingWindowMemory enforces token bounds with large payload."""
        mem = SlidingWindowMemory(max_messages=10, max_tokens=100)
        # 10,000 characters is ~2,500 tokens
        huge_text = "OmniAgent " * 1000
        mem.add_message(Message(role=MessageRole.USER, content=huge_text))
        active = mem.get_messages()
        self.assertEqual(len(active), 1)

    def test_provider_max_tokens_parameter_enforcement(self):
        """Test provider accepts max_tokens parameter without failure."""
        provider = ProviderFactory.create_mock("openai")
        req = provider.normalize_request(
            messages=[Message(role=MessageRole.USER, content="Short")],
            max_tokens=5,
        )
        self.assertEqual(req.get("max_tokens"), 5)

    def test_summary_memory_under_burst_load(self):
        """Test SummaryMemory compresses multiple burst batches reliably."""
        provider = ProviderFactory.create_mock("openai")
        mem = SummaryMemory(max_unsummarized_messages=4, provider=provider)
        for i in range(20):
            mem.add_message(Message(role=MessageRole.USER, content=f"Burst message {i}"))

        messages = mem.get_messages()
        # Should have compressed into accumulating summary plus unsummarized messages
        self.assertLessEqual(len(messages), 10)

    def test_large_tool_output_truncation(self):
        """Test tool capturing large stdout payload (50KB) safely."""
        runner = ReferenceCodeRunner()
        script = "print('X' * 50000)"
        res = runner.execute(language="python", code=script)
        self.assertTrue(res.success)
        self.assertEqual(len(res.output), 50000)

    def test_token_usage_overflow_protection(self):
        """Test TokenUsage handles large numbers without overflow error."""
        usage = TokenUsage(prompt_tokens=10_000_000, completion_tokens=5_000_000)
        self.assertEqual(usage.total_tokens, 15_000_000)
        d = usage.to_dict()
        self.assertEqual(d["total_tokens"], 15_000_000)

    # --------------------------------------------------------------------------
    # Category 3: Tool Timeout & Child Process Termination (5 tests)
    # --------------------------------------------------------------------------

    def test_code_runner_python_sleep_timeout(self):
        """Test code_runner enforces timeout on blocking Python sleep."""
        runner = ReferenceCodeRunner()
        script = "import time; time.sleep(10)"
        start_t = time.time()
        res = runner.execute(language="python", code=script, timeout_seconds=0.2)
        elapsed = time.time() - start_t
        self.assertFalse(res.success)
        self.assertIn("timed out", res.error.lower())
        self.assertLess(elapsed, 4.0)

    def test_code_runner_infinite_loop_timeout(self):
        """Test code_runner enforces timeout on infinite while loop."""
        runner = ReferenceCodeRunner()
        script = "while True: pass"
        start_t = time.time()
        res = runner.execute(language="python", code=script, timeout_seconds=0.2)
        elapsed = time.time() - start_t
        self.assertFalse(res.success)
        self.assertIn("timed out", res.error.lower())
        self.assertLess(elapsed, 4.0)

    def test_code_runner_shell_hang_timeout(self):
        """Test code_runner terminates hanging shell commands."""
        import sys
        runner = ReferenceCodeRunner()
        script = f'"{sys.executable}" -c "import time; time.sleep(10)"'
        res = runner.execute(language="shell", code=script, timeout_seconds=0.2)
        self.assertFalse(res.success)
        self.assertIn("timed out", res.error.lower())

    def test_browser_operate_navigation_timeout(self):
        """Test browser_operate error handling on invalid / blank targets."""
        browser = ReferenceBrowserOperate()
        res = browser.execute(action="navigate", url="")
        self.assertFalse(res.success)
        self.assertIn("required", res.error.lower())

    def test_planner_step_timeout_handling(self):
        """Test MultiStepPlanner skips dependent steps when prerequisite times out."""
        registry = ToolRegistry()
        registry.register(ReferenceCodeRunner())
        router = ToolRouter(registry=registry)
        planner = MultiStepPlanner(router=router)

        plan = ExecutionPlan(goal="Run hanging script and process output")
        step1 = PlanStep(
            step_id=1,
            title="Hanging Step",
            tool_name="code_runner",
            tool_input_template={"language": "python", "code": "import time; time.sleep(5)", "timeout_seconds": 0.2},
        )
        step2 = PlanStep(
            step_id=2,
            title="Dependent Step",
            tool_name="code_runner",
            tool_input_template={"language": "python", "code": "print('never run')"},
            dependencies=[1],
        )
        plan.add_step(step1)
        plan.add_step(step2)

        executed_plan = planner.execute_plan(plan)
        self.assertEqual(executed_plan.steps[0].status, StepStatus.FAILED)
        self.assertEqual(executed_plan.steps[1].status, StepStatus.SKIPPED)

    # --------------------------------------------------------------------------
    # Category 4: SSRF Cloud Metadata Block & Network Isolation (5 tests)
    # --------------------------------------------------------------------------

    def test_browser_operate_ssrf_aws_metadata(self):
        """Test browser_operate blocks AWS metadata IP 169.254.169.254."""
        browser = ReferenceBrowserOperate()
        res = browser.execute(action="navigate", url="http://169.254.169.254/latest/meta-data/")
        self.assertFalse(res.success)
        self.assertIn("ssrf", res.error.lower())

    def test_browser_operate_ssrf_gcp_metadata(self):
        """Test browser_operate blocks GCP metadata hostname."""
        browser = ReferenceBrowserOperate()
        res = browser.execute(action="navigate", url="http://metadata.google.internal/computeMetadata/v1/")
        self.assertFalse(res.success)
        self.assertIn("ssrf", res.error.lower())

    def test_browser_operate_ssrf_localhost_loopback(self):
        """Test browser_operate blocks localhost loopback destinations."""
        browser = ReferenceBrowserOperate()
        res = browser.execute(action="navigate", url="http://127.0.0.1:8080/admin")
        self.assertFalse(res.success)
        self.assertIn("ssrf", res.error.lower())

    def test_browser_operate_ssrf_private_subnet_10(self):
        """Test browser_operate blocks private subnet 10.0.0.0/8."""
        browser = ReferenceBrowserOperate()
        res = browser.execute(action="navigate", url="http://10.0.0.1/internal-secrets")
        self.assertFalse(res.success)
        self.assertIn("ssrf", res.error.lower())

    def test_browser_operate_ssrf_forbidden_schemes(self):
        """Test browser_operate blocks non-HTTP/HTTPS schemes (file://)."""
        browser = ReferenceBrowserOperate()
        res = browser.execute(action="navigate", url="file:///etc/passwd")
        self.assertFalse(res.success)
        self.assertIn("ssrf", res.error.lower())

    # --------------------------------------------------------------------------
    # Category 5: Invalid JSON/YAML Schemas & Malformed Inputs (5 tests)
    # --------------------------------------------------------------------------

    def test_workflow_missing_schema_version(self):
        """Test workflow missing schema_version fails validation."""
        bad_wf = {"nodes": [], "edges": []}
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(bad_wf)
        self.assertFalse(is_valid)
        self.assertIn("schema_version", err)

    def test_workflow_missing_nodes_or_edges(self):
        """Test workflow missing nodes list fails validation."""
        bad_wf = {"schema_version": "1.0.0", "edges": []}
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(bad_wf)
        self.assertFalse(is_valid)
        self.assertIn("nodes", err)

    def test_workflow_malformed_edge_endpoints(self):
        """Test edge referencing non-existent node ID fails validation."""
        bad_wf = {
            "schema_version": "1.0.0",
            "nodes": [{"id": "n1", "type": "trigger"}],
            "edges": [{"source": "n1", "target": "non_existent_node"}],
        }
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(bad_wf)
        self.assertFalse(is_valid)
        self.assertIn("does not exist", err)

    def test_tool_router_invalid_type_arguments(self):
        """Test ToolRouter catches type mismatch in tool arguments."""
        registry = ToolRegistry()
        registry.register(ReferenceCodeRunner())
        router = ToolRouter(registry=registry)

        # timeout_seconds passed as array instead of number
        call = ToolCall(
            id="c_bad_type",
            name="code_runner",
            arguments={"language": "python", "code": "print(1)", "timeout_seconds": ["invalid", "type"]},
        )
        res = router.route(call)
        self.assertFalse(res.success)
        self.assertIn("error", res.error.lower())

    def test_message_malformed_json_dict(self):
        """Test Message.from_dict handles missing optional fields gracefully."""
        minimal = {"role": "user", "content": "hello"}
        msg = Message.from_dict(minimal)
        self.assertEqual(msg.role, MessageRole.USER)
        self.assertEqual(msg.content, "hello")
        self.assertIsNone(msg.tool_calls)

    # --------------------------------------------------------------------------
    # Category 6: Self-Loop Connections & DAG Cycle Detection (5 tests)
    # --------------------------------------------------------------------------

    def test_workflow_self_loop_detected(self):
        """Test edge connecting node to itself is rejected."""
        loop_wf = {
            "schema_version": "1.0.0",
            "nodes": [{"id": "n1", "type": "trigger"}],
            "edges": [{"source": "n1", "target": "n1"}],
        }
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(loop_wf)
        self.assertFalse(is_valid)
        self.assertIn("self-loop", err.lower())

    def test_workflow_two_node_circular_cycle(self):
        """Test two-node cycle A -> B -> A is detected."""
        cycle_wf = {
            "schema_version": "1.0.0",
            "nodes": [{"id": "A", "type": "trigger"}, {"id": "B", "type": "action"}],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "B", "target": "A"},
            ],
        }
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(cycle_wf)
        self.assertFalse(is_valid)
        self.assertIn("cycle", err.lower())

    def test_workflow_multi_node_indirect_cycle(self):
        """Test multi-node circular dependency A -> B -> C -> A is detected."""
        cycle_wf = {
            "schema_version": "1.0.0",
            "nodes": [
                {"id": "A", "type": "trigger"},
                {"id": "B", "type": "llm"},
                {"id": "C", "type": "action"},
            ],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "B", "target": "C"},
                {"source": "C", "target": "A"},
            ],
        }
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(cycle_wf)
        self.assertFalse(is_valid)
        self.assertIn("cycle", err.lower())

    def test_planner_circular_step_dependencies(self):
        """Test MultiStepPlanner handles circular step dependencies without deadlock."""
        registry = ToolRegistry()
        def dummy_action():
            return "ok"
        registry.register(FunctionTool(name="dummy", description="dummy", fn=dummy_action))
        router = ToolRouter(registry=registry)
        planner = MultiStepPlanner(router=router)

        plan = ExecutionPlan(goal="Cycle test")
        s1 = PlanStep(step_id=1, title="S1", tool_name="dummy", dependencies=[2])
        s2 = PlanStep(step_id=2, title="S2", tool_name="dummy", dependencies=[1])
        plan.add_step(s1)
        plan.add_step(s2)

        executed = planner.execute_plan(plan)
        # Neither can satisfy dependency so loop breaks safely
        self.assertNotEqual(executed.status, "RUNNING")

    def test_planner_self_dependent_step(self):
        """Test MultiStepPlanner handles step depending on itself."""
        registry = ToolRegistry()
        router = ToolRouter(registry=registry)
        planner = MultiStepPlanner(router=router)

        plan = ExecutionPlan(goal="Self dependency")
        s = PlanStep(step_id=1, title="S1", tool_name="dummy", dependencies=[1])
        plan.add_step(s)

        executed = planner.execute_plan(plan)
        self.assertNotEqual(executed.steps[0].status, StepStatus.COMPLETED)

    # --------------------------------------------------------------------------
    # Category 7: Secret Credential Scrubbing (5 tests)
    # --------------------------------------------------------------------------

    def test_code_runner_scrubs_openai_api_key(self):
        """Test code_runner redacts OpenAI API keys in stdout."""
        runner = ReferenceCodeRunner()
        script = 'print("api_key: sk-proj-1234567890abcdef1234567890")'
        res = runner.execute(language="python", code=script)
        self.assertTrue(res.success)
        self.assertNotIn("sk-proj-1234567890abcdef", res.output)
        self.assertIn("[REDACTED]", res.output)

    def test_code_runner_scrubs_aws_secret(self):
        """Test code_runner redacts AWS secret access keys."""
        runner = ReferenceCodeRunner()
        script = 'print("AWS_SECRET_ACCESS_KEY: wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLE")'
        res = runner.execute(language="python", code=script)
        self.assertTrue(res.success)
        self.assertNotIn("wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLE", res.output)
        self.assertIn("[REDACTED]", res.output)

    def test_code_runner_scrubs_bearer_token(self):
        """Test code_runner redacts bearer tokens."""
        runner = ReferenceCodeRunner()
        script = 'print("bearer: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")'
        res = runner.execute(language="python", code=script)
        self.assertTrue(res.success)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", res.output)
        self.assertIn("[REDACTED]", res.output)

    def test_code_runner_environment_sanitization(self):
        """Test host secret environment variables are not leaked to sandbox."""
        os.environ["SECRET_TEST_TOKEN"] = "ultra_confidential_secret_val"
        runner = ReferenceCodeRunner()
        script = 'import os; print(os.environ.get("SECRET_TEST_TOKEN", "NOT_FOUND"))'
        res = runner.execute(language="python", code=script)
        self.assertTrue(res.success)
        self.assertEqual(res.output, "NOT_FOUND")

    def test_fingerprint_scrubber_strips_tracking_headers(self):
        """Test FingerprintScrubber removes tracking headers and normalizes User-Agent."""
        raw_headers = {
            "Host": "api.example.com",
            "X-Forwarded-For": "203.0.113.195",
            "X-Real-IP": "203.0.113.195",
            "Sec-CH-UA": '"Chromium";v="120"',
            "User-Agent": "CustomAgent/1.0",
        }
        scrubbed = ReferenceFingerprintScrubber.scrub_headers(raw_headers)
        self.assertNotIn("X-Forwarded-For", scrubbed)
        self.assertNotIn("X-Real-IP", scrubbed)
        self.assertNotIn("Sec-CH-UA", scrubbed)
        self.assertIn("Mozilla/5.0", scrubbed["User-Agent"])
        self.assertIn("Accept-Language", scrubbed)

    # --------------------------------------------------------------------------
    # Category 8: Proxy Connection Drops & Error Handling (5 tests)
    # --------------------------------------------------------------------------

    def test_proxy_unreachable_port_error_handling(self):
        """Test connecting to dead proxy port fails with clean exception."""
        dead_port = 59998
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self.assertRaises(Exception):
            s.connect(("127.0.0.1", dead_port))
        s.close()

    def test_proxy_abrupt_disconnect_during_handshake(self):
        """Test SOCKS5 proxy handles immediate client socket drop without crash."""
        proxy = MockSOCKS5Server()
        proxy.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", proxy.port))
            # Close abruptly before sending anything
            s.close()
            time.sleep(0.1)
            self.assertTrue(proxy.running)
        finally:
            proxy.stop()

    def test_proxy_invalid_socks_version(self):
        """Test sending non-SOCKS5 version (SOCKS4 \x04) is rejected."""
        proxy = MockSOCKS5Server()
        proxy.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", proxy.port))
            # SOCKS4 connect
            s.sendall(b"\x04\x01\x00\x50\x7f\x00\x00\x01user\x00")
            try:
                resp = s.recv(2)
                # Proxy closes or returns no SOCKS5 handshake
                self.assertNotEqual(resp, b"\x05\x00")
            except (ConnectionResetError, ConnectionAbortedError):
                pass  # Connection forcibly closed by server is expected rejection
            s.close()
        finally:
            proxy.stop()

    def test_proxy_unsupported_auth_method(self):
        """Test proxy handles unsupported authentication method gracefully."""
        proxy = MockSOCKS5Server()
        proxy.start()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", proxy.port))
            # Send VER=5, NMETHODS=1, METHOD=0x02 (Username/Password)
            s.sendall(b"\x05\x01\x02")
            resp = s.recv(2)
            # Server defaults to 0x00 or closes
            s.close()
            self.assertTrue(proxy.running)
        finally:
            proxy.stop()

    def test_network_security_context_disabled_fallback(self):
        """Test provider creates direct session when security context is disabled."""
        provider = ProviderFactory.create_mock("openai")
        provider.set_security_context(NetworkSecurityContext(enabled=False, proxy_url=None))
        session = provider.get_http_session()
        self.assertEqual(session.proxies, {})


if __name__ == "__main__":
    unittest.main()
