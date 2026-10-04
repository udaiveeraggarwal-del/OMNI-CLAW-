"""
Tool Router and Registry subsystem for OmniAgent.
Enforces jsonschema validation, strict parameter checking, and safe tool execution.
"""

from __future__ import annotations

import abc
import inspect
from typing import Any, Callable, Dict, List, Optional
import jsonschema

from omniagent.core.models import ToolCall, ToolDefinition, ToolResult


class BaseTool(abc.ABC):
    """Abstract base class for all tools available to the OmniAgent runtime."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier of the tool."""
        pass

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Detailed description of the tool purpose and capabilities."""
        pass

    @property
    @abc.abstractmethod
    def parameters_schema(self) -> Dict[str, Any]:
        """JSON Schema dictionary defining input parameter types and constraints."""
        pass

    def to_definition(self) -> ToolDefinition:
        """Convert into canonical ToolDefinition for LLM prompting."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters_schema,
        )

    @abc.abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool action with the supplied keyword arguments."""
        pass


class FunctionTool(BaseTool):
    """Convenience wrapper creating a BaseTool from a Python callable."""

    def __init__(
        self,
        name: str,
        description: str,
        fn: Callable[..., Any],
        parameters_schema: Optional[Dict[str, Any]] = None,
    ):
        self._name = name
        self._description = description
        self._fn = fn
        self._schema = parameters_schema or self._auto_generate_schema(fn)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return self._schema

    def _auto_generate_schema(self, fn: Callable[..., Any]) -> Dict[str, Any]:
        """Heuristic schema generation from Python function type annotations."""
        sig = inspect.signature(fn)
        properties: Dict[str, Any] = {}
        required: List[str] = []

        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            p_type = type_map.get(param.annotation, "string")
            properties[param_name] = {"type": p_type}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            res = self._fn(**kwargs)
            if isinstance(res, ToolResult):
                return res
            return ToolResult(success=True, output=res)
        except Exception as ex:
            return ToolResult(success=False, output=None, error=str(ex))


class ToolRegistry:
    """Central catalog of registered tools for an agent."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check whether a tool is registered."""
        return name in self._tools

    def unregister(self, name: str) -> bool:
        """Remove a tool by name."""
        return self._tools.pop(name, None) is not None

    def list_tools(self) -> List[BaseTool]:
        """Return all registered tool instances."""
        return list(self._tools.values())

    def list_definitions(self) -> List[ToolDefinition]:
        """Export all tools as canonical ToolDefinitions."""
        return [tool.to_definition() for tool in self._tools.values()]

    def clear(self) -> None:
        """Clear all registered tools."""
        self._tools.clear()


class ToolRouter:
    """
    Executes tool calls requested by an LLM or planner.
    Performs JSON Schema argument validation before dispatching to the tool.
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def route(self, tool_call: ToolCall) -> ToolResult:
        """
        Validate and execute the specified ToolCall.
        Guarantees safe execution without raising unhandled exceptions to the caller.
        """
        tool = self.registry.get(tool_call.name)
        if not tool:
            return ToolResult(
                success=False,
                output=None,
                error=f"Tool '{tool_call.name}' not found in registry.",
                metadata={"tool_name": tool_call.name, "arguments": tool_call.arguments},
            )

        # Validate arguments against the tool's JSON Schema
        schema = tool.parameters_schema
        if schema:
            try:
                jsonschema.validate(instance=tool_call.arguments, schema=schema)
            except jsonschema.ValidationError as ve:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Parameter validation error: {ve.message}",
                    metadata={
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "schema_path": list(ve.path),
                    },
                )
            except Exception as e:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Schema validation failure: {str(e)}",
                    metadata={"tool_name": tool_call.name},
                )

        # Execute the tool safely
        try:
            exec_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
            result = tool.execute(**exec_args)
            if not isinstance(result, ToolResult):
                result = ToolResult(success=True, output=result)
            return result
        except Exception as ex:
            return ToolResult(
                success=False,
                output=None,
                error=f"Tool execution exception: {str(ex)}",
                metadata={"tool_name": tool_call.name, "arguments": tool_call.arguments},
            )
