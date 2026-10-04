"""Built-in and third-party skills for the OmniAgent runtime."""

from omniagent.skills.base import BaseSkill, SkillRegistry
from omniagent.skills.browser_operate import BrowserOperateSkill
from omniagent.skills.code_runner import CodeRunnerSkill
from omniagent.skills.seo_optimizer import SeoOptimizerSkill
from omniagent.skills.odoo_builder import OdooBuilderSkill, OdooJson2Client
from omniagent.skills.social_media import SocialMediaSkill
from omniagent.skills.host_control import HostControlSkill


def create_builtin_registry(
    tool_registry=None,
    *,
    host_grant=None,
    desktop_backend=None,
    odoo_client=None,
    odoo_approval_callback=None,
    social_adapter=None,
    social_approval_callback=None,
) -> SkillRegistry:
    """Create a fresh registry; external accounts and host access are injected by the host."""
    registry = SkillRegistry(tool_registry=tool_registry)
    registry.register(BrowserOperateSkill(capability_grant=host_grant))
    registry.register(SeoOptimizerSkill())
    registry.register(CodeRunnerSkill())
    registry.register(HostControlSkill(grant=host_grant, desktop_backend=desktop_backend))
    registry.register(OdooBuilderSkill(
        client=odoo_client,
        approval_callback=odoo_approval_callback,
        capability_grant=host_grant,
    ))
    registry.register(SocialMediaSkill(
        adapter=social_adapter,
        approval_callback=social_approval_callback,
        capability_grant=host_grant,
    ))
    return registry


__all__ = [
    "BaseSkill",
    "SkillRegistry",
    "BrowserOperateSkill",
    "SeoOptimizerSkill",
    "CodeRunnerSkill",
    "OdooBuilderSkill",
    "OdooJson2Client",
    "SocialMediaSkill",
    "HostControlSkill",
    "create_builtin_registry",
]
