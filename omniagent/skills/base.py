"""Contracts and registry for versioned, namespaced OmniAgent skills.

Skills are ordinary Python objects, but registration is explicit: importing a
module never executes a skill. This keeps plugin discovery separate from
activation and gives hosts one place to inspect the tools exposed to a model.
"""

from __future__ import annotations

import abc
import re
from typing import Any, Dict, List

import jsonschema

from omniagent.core.models import ToolDefinition
from omniagent.core.router import BaseTool, ToolRegistry


_SKILL_ID = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


class BaseSkill(abc.ABC):
    """Base contract for a skill package.

    Each action name must use the form skill_id.action. A skill returns fresh
    tool instances from get_tools(), allowing hosts to configure actions per
    agent without sharing mutable tool state accidentally.
    """

    @property
    @abc.abstractmethod
    def skill_id(self) -> str:
        """Stable machine-readable identifier, such as seo."""

    @property
    @abc.abstractmethod
    def version(self) -> str:
        """Skill package version in major.minor.patch form."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Short user-facing description of the skill."""

    @abc.abstractmethod
    def get_tools(self) -> List[BaseTool]:
        """Return the actions provided by this skill."""

    def manifest(self) -> Dict[str, Any]:
        """Return a JSON-serializable manifest without activating the skill."""
        self._validate_identity()
        tools = self.get_tools()
        self._validate_tools(tools)
        return {
            "schema_version": "1.0.0",
            "id": self.skill_id,
            "version": self.version,
            "description": self.description,
            "actions": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                }
                for tool in tools
            ],
        }

    def _validate_identity(self) -> None:
        if not isinstance(self.skill_id, str) or not _SKILL_ID.fullmatch(self.skill_id):
            raise ValueError("skill_id must be 2-48 lowercase letters, digits, or underscores")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version or ""):
            raise ValueError("skill version must use major.minor.patch form")
        if not self.description or not self.description.strip():
            raise ValueError("skill description must not be empty")

    def _validate_tools(self, tools: List[BaseTool]) -> None:
        if not isinstance(tools, list):
            raise TypeError("get_tools() must return a list")
        seen = set()
        for tool in tools:
            if not isinstance(tool, BaseTool):
                raise TypeError("every skill action must implement BaseTool")
            if not tool.name.startswith(f"{self.skill_id}."):
                raise ValueError(
                    f"action {tool.name!r} must be namespaced with {self.skill_id!r}"
                )
            if tool.name in seen:
                raise ValueError(f"duplicate action name: {tool.name}")
            seen.add(tool.name)
            jsonschema.Draft202012Validator.check_schema(tool.parameters_schema)


class SkillRegistry:
    """Explicit registry of enabled skills and their model-visible actions."""

    def __init__(self, tool_registry: ToolRegistry | None = None):
        self.tool_registry = tool_registry or ToolRegistry()
        self._skills: Dict[str, BaseSkill] = {}
        self._skill_tool_names: Dict[str, List[str]] = {}

    def register(self, skill: BaseSkill) -> None:
        """Validate and register a skill, rejecting collisions atomically."""
        if not isinstance(skill, BaseSkill):
            raise TypeError("skill must inherit from BaseSkill")
        skill._validate_identity()
        if skill.skill_id in self._skills:
            raise ValueError(f"skill already registered: {skill.skill_id}")

        tools = skill.get_tools()
        skill._validate_tools(tools)
        existing_names = {tool.name for tool in self.tool_registry.list_tools()}
        collisions = existing_names.intersection(tool.name for tool in tools)
        if collisions:
            raise ValueError(f"tool name already registered: {sorted(collisions)[0]}")

        # All input validation happens before the first mutation.
        for tool in tools:
            self.tool_registry.register(tool)
        self._skills[skill.skill_id] = skill
        self._skill_tool_names[skill.skill_id] = [tool.name for tool in tools]

    def get(self, skill_id: str) -> BaseSkill | None:
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Dict[str, Any]]:
        return [self._skills[key].manifest() for key in sorted(self._skills)]

    def list_definitions(self) -> List[ToolDefinition]:
        return self.tool_registry.list_definitions()

    def unregister(self, skill_id: str) -> bool:
        """Remove a skill and its actions from this registry."""
        skill = self._skills.pop(skill_id, None)
        if skill is None:
            return False
        for tool_name in self._skill_tool_names.pop(skill_id, []):
            self.tool_registry.unregister(tool_name)
        return True
