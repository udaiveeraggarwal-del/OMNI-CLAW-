"""Built-in and third-party skills for the OmniAgent runtime."""

from omniagent.skills.base import BaseSkill, SkillRegistry
from omniagent.skills.code_runner import CodeRunnerSkill
from omniagent.skills.seo_optimizer import SeoOptimizerSkill


def create_builtin_registry(tool_registry=None) -> SkillRegistry:
    """Create a fresh registry with the built-in skills enabled."""
    registry = SkillRegistry(tool_registry=tool_registry)
    registry.register(SeoOptimizerSkill())
    registry.register(CodeRunnerSkill())
    return registry


__all__ = [
    "BaseSkill",
    "SkillRegistry",
    "SeoOptimizerSkill",
    "CodeRunnerSkill",
    "create_builtin_registry",
]
