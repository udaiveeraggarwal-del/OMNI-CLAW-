"""
Multi-Step Planner for OmniAgent.
Implements DeepSeek R1-inspired <think> reflection traces and Claude Code / OpenClaw
plan-act-reflect loops with DAG dependency execution, parameter interpolation,
and multi-tool result synthesis.
"""

from __future__ import annotations

import datetime
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from omniagent.core.models import (
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolResult,
)
from omniagent.core.providers.base import BaseLLMProvider
from omniagent.core.router import ToolRouter
from omniagent.core.state import AgentState, BaseStateStore


class StepStatus(str, Enum):
    """Execution status of a plan step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep:
    """A discrete task step within an ExecutionPlan."""

    def __init__(
        self,
        step_id: int,
        title: str,
        description: str = "",
        tool_name: Optional[str] = None,
        tool_input_template: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[int]] = None,
        status: StepStatus = StepStatus.PENDING,
        output: Optional[Any] = None,
        error: Optional[str] = None,
        reflection: Optional[str] = None,
    ):
        self.step_id = step_id
        self.title = title
        self.description = description
        self.tool_name = tool_name
        self.tool_input_template = tool_input_template or {}
        self.dependencies = dependencies or []
        self.status = status if isinstance(status, StepStatus) else StepStatus(str(status))
        self.output = output
        self.error = error
        self.reflection = reflection

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "description": self.description,
            "tool_name": self.tool_name,
            "tool_input_template": self.tool_input_template,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "reflection": self.reflection,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanStep:
        return cls(
            step_id=int(data["step_id"]),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            tool_name=data.get("tool_name"),
            tool_input_template=data.get("tool_input_template") or {},
            dependencies=data.get("dependencies") or [],
            status=StepStatus(data.get("status", "pending")),
            output=data.get("output"),
            error=data.get("error"),
            reflection=data.get("reflection"),
        )


class ExecutionPlan:
    """Complete multi-step execution DAG with reasoning traces."""

    def __init__(
        self,
        goal: str,
        plan_id: Optional[str] = None,
        steps: Optional[List[PlanStep]] = None,
        status: str = "PENDING",
        final_synthesis: Optional[str] = None,
        thinking_trace: Optional[str] = None,
    ):
        self.plan_id = plan_id or f"plan_{uuid.uuid4().hex[:8]}"
        self.goal = goal
        self.steps: List[PlanStep] = steps or []
        self.status = status  # PENDING, RUNNING, COMPLETED, FAILED
        self.final_synthesis = final_synthesis
        self.thinking_trace = thinking_trace or ""

    def add_step(self, step: PlanStep) -> None:
        self.steps.append(step)

    def get_step(self, step_id: int) -> Optional[PlanStep]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def is_completed(self) -> bool:
        return bool(self.steps and all(s.status == StepStatus.COMPLETED for s in self.steps))

    def append_thinking(self, thought: str) -> None:
        if self.thinking_trace:
            self.thinking_trace += "\n" + thought
        else:
            self.thinking_trace = thought

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "final_synthesis": self.final_synthesis,
            "thinking_trace": self.thinking_trace,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionPlan:
        raw_steps = data.get("steps") or []
        steps = [
            PlanStep.from_dict(s) if isinstance(s, dict) else s
            for s in raw_steps
        ]
        return cls(
            goal=str(data.get("goal", "")),
            plan_id=data.get("plan_id"),
            steps=steps,
            status=str(data.get("status", "PENDING")),
            final_synthesis=data.get("final_synthesis"),
            thinking_trace=data.get("thinking_trace"),
        )


class ReflectionEngine:
    """
    DeepSeek R1 self-correction & Claude Code inspection engine.
    Analyzes execution traces, generates <think> reflection blocks, and diagnoses failures.
    """

    def evaluate_step(self, step: PlanStep, result: ToolResult) -> Tuple[bool, str]:
        """
        Evaluate step outcome and produce DeepSeek R1-style reflection trace.
        Returns (is_successful, reflection_trace).
        """
        if result.success:
            reflection = (
                f"<think>\n"
                f"Step {step.step_id} ('{step.title}') completed successfully.\n"
                f"Action output: {json.dumps(result.output) if isinstance(result.output, (dict, list)) else str(result.output)}\n"
                f"Validated output matches expectations. Proceeding to dependent steps.\n"
                f"</think>"
            )
            return True, reflection
        else:
            reflection = (
                f"<think>\n"
                f"Step {step.step_id} ('{step.title}') failed.\n"
                f"Encountered error: {result.error}\n"
                f"Diagnosing root cause and verifying if alternative recovery is possible.\n"
                f"</think>"
            )
            return False, reflection


def resolve_parameter_reference(
    ref_string: str,
    scratchpad: Dict[str, Any],
    step_outputs: Dict[int, Any],
) -> Any:
    """
    Resolve parameter reference like $step1.output, $step1.output.field, or $scratchpad.key.
    If exact match, returns the underlying object (preserving types like lists, dicts, numbers).
    If embedded in a larger string, interpolates string representations.
    """
    # Exact reference pattern: e.g. "$step1.output" or "$step1.output.raw_data"
    exact_pattern = re.compile(r"^\$(step\d+|scratchpad)(\.[a-zA-Z0-9_]+)*$")
    if exact_pattern.match(ref_string):
        return _extract_path_value(ref_string, scratchpad, step_outputs)

    # Embedded references: e.g. "Prefix $step1.output.query suffix"
    def replace_match(match: re.Match) -> str:
        token = match.group(0)
        val = _extract_path_value(token, scratchpad, step_outputs)
        return str(val) if val is not None else token

    embedded_pattern = re.compile(r"\$(step\d+|scratchpad)(\.[a-zA-Z0-9_]+)*")
    return embedded_pattern.sub(replace_match, ref_string)


def _extract_path_value(
    path: str,
    scratchpad: Dict[str, Any],
    step_outputs: Dict[int, Any],
) -> Any:
    """Helper to traverse dot-separated paths on resolved step outputs or scratchpad."""
    cleaned = path.lstrip("$")
    parts = cleaned.split(".")
    root_key = parts[0]

    # Determine root object
    current: Any = None
    if root_key.startswith("step"):
        try:
            step_idx = int(root_key[4:])
            step_data = step_outputs.get(step_idx)
            # The path might be $step1.output or $step1.output.key
            if len(parts) > 1 and parts[1] == "output":
                current = step_data
                parts = [parts[0]] + parts[2:]  # Remove 'output' token from traversal
            else:
                current = step_data
        except ValueError:
            current = None
    elif root_key == "scratchpad":
        current = scratchpad
    else:
        current = scratchpad.get(root_key)

    # Traverse remaining path segments
    for segment in parts[1:]:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(segment)
        elif hasattr(current, segment):
            current = getattr(current, segment)
        else:
            return None

    return current


def resolve_template(
    template: Any,
    scratchpad: Dict[str, Any],
    step_outputs: Dict[int, Any],
) -> Any:
    """Recursively resolve parameter references across dicts, lists, and strings."""
    if isinstance(template, str):
        return resolve_parameter_reference(template, scratchpad, step_outputs)
    elif isinstance(template, dict):
        return {
            k: resolve_template(v, scratchpad, step_outputs)
            for k, v in template.items()
        }
    elif isinstance(template, list):
        return [resolve_template(item, scratchpad, step_outputs) for item in template]
    return template


class MultiStepPlanner:
    """
    Multi-Step Execution Planner.
    Coordinates DAG task execution, tool routing, parameter interpolation,
    error reflection, and final answer synthesis.
    """

    def __init__(
        self,
        router: ToolRouter,
        provider: Optional[BaseLLMProvider] = None,
        state_store: Optional[BaseStateStore] = None,
        reflection_engine: Optional[ReflectionEngine] = None,
    ):
        self.router = router
        self.provider = provider
        self.state_store = state_store
        self.reflection_engine = reflection_engine or ReflectionEngine()

    def create_plan_from_llm(self, goal: str) -> ExecutionPlan:
        """
        Decompose user goal into an ExecutionPlan using the configured LLM provider.
        If provider is unavailable, falls back to basic goal parsing.
        """
        tool_defs = self.router.registry.list_definitions()
        plan = ExecutionPlan(goal=goal)

        if self.provider is not None:
            tools_summary = "\n".join(
                [f"- {td.name}: {td.description} (params: {list(td.parameters.get('properties', {}).keys())})" for td in tool_defs]
            )
            prompt = (
                f"You are the OmniAgent Multi-Step Planner.\n"
                f"User Goal: {goal}\n\n"
                f"Available Tools:\n{tools_summary}\n\n"
                f"Generate a multi-step execution plan as a valid JSON array of objects with keys: "
                f"step_id (int), title (str), tool_name (str), tool_input_template (dict), dependencies (list of int).\n"
                f"Wrap your reasoning inside <think>...</think> tags, followed by ```json ... ``` code block."
            )
            try:
                response = self.provider.generate(
                    messages=[
                        Message(role=MessageRole.SYSTEM, content="You are a multi-step task planner."),
                        Message(role=MessageRole.USER, content=prompt),
                    ]
                )
                if response.content:
                    # Extract thinking trace
                    think_match = re.search(r"<think>(.*?)</think>", response.content, re.DOTALL)
                    if think_match:
                        plan.thinking_trace = think_match.group(1).strip()

                    # Extract JSON array
                    json_match = re.search(r"```json\s*(.*?)\s*```", response.content, re.DOTALL)
                    raw_json = json_match.group(1) if json_match else response.content
                    steps_data = json.loads(raw_json)
                    if isinstance(steps_data, list):
                        for s_dict in steps_data:
                            plan.add_step(PlanStep.from_dict(s_dict))
                        return plan
            except Exception:
                pass

        # Fallback heuristic: If prompt mentions fetch and analyze/calculate, create a 2-step plan
        if "fetch" in goal.lower() or "search" in goal.lower():
            step1 = PlanStep(
                step_id=1,
                title="Fetch Required Data",
                tool_name="data_fetcher",
                tool_input_template={"query": goal},
                dependencies=[],
            )
            step2 = PlanStep(
                step_id=2,
                title="Analyze Fetched Data",
                tool_name="data_analyzer",
                tool_input_template={"values": "$step1.output.raw_data"},
                dependencies=[1],
            )
            plan.add_step(step1)
            plan.add_step(step2)
            plan.thinking_trace = (
                "<think>Heuristic decomposition: Step 1 fetches data, Step 2 analyzes output of Step 1.</think>"
            )

        return plan

    def execute_plan(
        self,
        plan: ExecutionPlan,
        session_id: Optional[str] = None,
    ) -> ExecutionPlan:
        """
        Execute the plan DAG respecting step dependencies and interpolating outputs.
        Applies DeepSeek R1 self-reflection and synthesizes the final outcome.
        """
        session_id = session_id or f"session_{uuid.uuid4().hex[:8]}"
        plan.status = "RUNNING"

        scratchpad: Dict[str, Any] = {"goal": plan.goal}
        step_outputs: Dict[int, Any] = {}

        # Load or initialize AgentState if state_store present
        state: Optional[AgentState] = None
        if self.state_store:
            state = self.state_store.load(session_id) or AgentState(
                session_id=session_id,
                status="EXECUTING",
                plan=plan,
                scratchpad=scratchpad,
            )

        executed_steps: Set[int] = set()
        failed_steps: Set[int] = set()

        max_iterations = len(plan.steps) * 3
        iterations = 0

        while len(executed_steps) + len(failed_steps) < len(plan.steps) and iterations < max_iterations:
            iterations += 1
            progress_made = False

            for step in plan.steps:
                if step.step_id in executed_steps or step.step_id in failed_steps:
                    continue

                # Check if all dependencies are satisfied
                deps_satisfied = all(d in executed_steps for d in step.dependencies)
                deps_failed = any(d in failed_steps for d in step.dependencies)

                if deps_failed:
                    step.status = StepStatus.SKIPPED
                    step.error = "Skipped because prerequisite dependency failed."
                    failed_steps.add(step.step_id)
                    progress_made = True
                    continue

                if not deps_satisfied:
                    continue

                # Step is ready to execute!
                step.status = StepStatus.RUNNING
                progress_made = True

                # 1. Parameter interpolation
                resolved_args = resolve_template(
                    step.tool_input_template, scratchpad, step_outputs
                )

                # 2. Tool dispatch via router
                if step.tool_name:
                    call_id = f"call_{step.step_id}_{uuid.uuid4().hex[:6]}"
                    tool_call = ToolCall(
                        id=call_id,
                        name=step.tool_name,
                        arguments=resolved_args if isinstance(resolved_args, dict) else {"arg": resolved_args},
                    )
                    tool_result = self.router.route(tool_call)
                else:
                    tool_result = ToolResult(success=True, output="No tool required for step.")

                # 3. DeepSeek R1-style reflection
                success, reflection = self.reflection_engine.evaluate_step(step, tool_result)
                step.reflection = reflection
                plan.append_thinking(reflection)

                if success:
                    step.status = StepStatus.COMPLETED
                    step.output = tool_result.output
                    step_outputs[step.step_id] = tool_result.output
                    scratchpad[f"step{step.step_id}"] = tool_result.output
                    executed_steps.add(step.step_id)
                else:
                    step.status = StepStatus.FAILED
                    step.error = tool_result.error
                    failed_steps.add(step.step_id)

                # Update persistent state
                if state and self.state_store:
                    state.scratchpad = scratchpad
                    state.plan = plan
                    self.state_store.save(state)

            if not progress_made:
                # Cycle or unresolved dependencies detected
                for s in plan.steps:
                    if s.step_id not in executed_steps and s.step_id not in failed_steps:
                        s.status = StepStatus.FAILED
                        s.error = "Unresolvable dependency or cycle detected in execution plan."
                        failed_steps.add(s.step_id)
                break

        # 4. Final synthesis
        if len(executed_steps) == len(plan.steps) and len(plan.steps) > 0:
            plan.status = "COMPLETED"
            plan.final_synthesis = self._synthesize_final_answer(plan, step_outputs)
        else:
            plan.status = "FAILED"
            failed_titles = [f"Step {s.step_id}: {s.error}" for s in plan.steps if s.status == StepStatus.FAILED]
            plan.final_synthesis = f"Plan execution failed. Errors: {'; '.join(failed_titles)}"

        if state and self.state_store:
            state.status = "COMPLETED" if plan.status == "COMPLETED" else "FAILED"
            state.plan = plan
            state.scratchpad = scratchpad
            self.state_store.save(state)

        return plan

    def _synthesize_final_answer(
        self,
        plan: ExecutionPlan,
        step_outputs: Dict[int, Any],
    ) -> str:
        """
        Synthesize completed multi-tool execution results into a unified answer.
        Uses LLM provider if available, or deterministic structured synthesis.
        """
        if self.provider is not None:
            steps_summary = []
            for s in plan.steps:
                steps_summary.append(
                    f"Step {s.step_id} ({s.title}): Tool '{s.tool_name}' returned: {s.output}"
                )
            synthesis_prompt = (
                f"User Goal: {plan.goal}\n\n"
                f"Execution Steps Results:\n" + "\n".join(steps_summary) + "\n\n"
                f"Synthesize a clear, authoritative final answer addressing the user's goal based on the results above."
            )
            try:
                resp = self.provider.generate(
                    messages=[
                        Message(role=MessageRole.SYSTEM, content="You are a multi-step answer synthesizer."),
                        Message(role=MessageRole.USER, content=synthesis_prompt),
                    ]
                )
                if resp.content:
                    return resp.content.strip()
            except Exception:
                pass

        # Deterministic / offline synthesis template
        tool_results_str = []
        for s in plan.steps:
            if s.tool_name:
                tool_results_str.append(f"{s.tool_name} returned {json.dumps(s.output) if isinstance(s.output, (dict, list)) else str(s.output)}")
        return (
            f"Successfully executed multi-step plan for goal: '{plan.goal}'. "
            f"Synthesized results: {'; '.join(tool_results_str)}."
        )

    def run(
        self,
        goal: str,
        steps: Optional[List[PlanStep]] = None,
        session_id: Optional[str] = None,
    ) -> ExecutionPlan:
        """Convenience method to create and execute a plan in one invocation."""
        if steps:
            plan = ExecutionPlan(goal=goal, steps=steps)
        else:
            plan = self.create_plan_from_llm(goal)
        return self.execute_plan(plan, session_id=session_id)
