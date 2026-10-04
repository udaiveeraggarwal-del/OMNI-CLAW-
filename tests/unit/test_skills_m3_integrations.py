"""Offline tests for host-granted capabilities and business skill boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from omniagent.security.capabilities import HostCapabilityGrant
from omniagent.skills.browser_operate.skill import (
    BrowserPolicy,
    BrowserPolicyError,
    BrowserSessionManager,
    _BrowserSession,
)
from omniagent.skills.host_control.skill import HostControlSkill
from omniagent.skills.odoo_builder.mock_odoo import InMemoryOdooClient
from omniagent.skills.odoo_builder.skill import OdooBuilderSkill, OdooJson2Client
from omniagent.skills.social_media.mock_social import InMemorySocialAdapter
from omniagent.skills.social_media.skill import SocialMediaSkill


def find_tool(skill, name):
    return next(tool for tool in skill.get_tools() if tool.name == name)


class TestHostControl(unittest.TestCase):
    def test_file_access_is_scoped_and_requires_grant(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file_path = root / "note.txt"
            grant = HostCapabilityGrant.issue(
                scopes={"machine.fs.read", "machine.fs.write"},
                filesystem_roots=[root],
                ttl_seconds=60,
            )
            tool = find_tool(HostControlSkill(grant=grant), "host.control")
            written = tool.execute(action="write_file", path=str(file_path), content="works")
            read = tool.execute(action="read_file", path=str(file_path))
            blocked = tool.execute(action="read_file", path=str(root.parent / "outside.txt"))

            self.assertTrue(written.success)
            self.assertTrue(read.success)
            self.assertEqual(read.output["content"], "works")
            self.assertFalse(blocked.success)

    def test_expired_host_grant_denies_machine_action(self):
        grant = HostCapabilityGrant(
            scopes=frozenset({"machine.fs.read"}),
            full_machine=True,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        result = find_tool(HostControlSkill(grant=grant), "host.control").execute(
            action="list_directory", path="."
        )
        self.assertFalse(result.success)
        self.assertIn("expired", result.error)


class TestOdooSkill(unittest.TestCase):
    def test_mock_crm_write_requires_host_grant_and_then_succeeds(self):
        fake_client = InMemoryOdooClient()
        blocked = find_tool(OdooBuilderSkill(client=fake_client), "odoo.crm_create_lead").execute(
            name="Test lead"
        )
        allowed = find_tool(OdooBuilderSkill(
            client=fake_client,
            capability_grant=HostCapabilityGrant.issue(scopes={"odoo.crm.write"}, ttl_seconds=60),
        ), "odoo.crm_create_lead").execute(name="Approved lead")

        self.assertFalse(blocked.success)
        self.assertTrue(allowed.success)
        self.assertEqual(allowed.output["id"], 1)
        self.assertEqual(fake_client.leads[0]["name"], "Approved lead")

    def test_json2_create_uses_vals_list_and_bearer_key(self):
        class FakeResponse:
            status_code = 200
            headers = {"Content-Length": "2"}
            def json(self):
                return [19]

        class FakeSession:
            trust_env = True
            proxies = {}
            def post(self, url, **kwargs):
                self.request = (url, kwargs)
                return FakeResponse()

        session = FakeSession()
        client = OdooJson2Client(
            "https://example.odoo.com", "test-api-key", allow_direct_egress=True, session=session
        )
        with patch("omniagent.skills.odoo_builder.skill.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]):
            lead_id = client.create_lead({"name": "Offline test"})

        url, request = session.request
        self.assertEqual(lead_id, 19)
        self.assertEqual(url, "https://example.odoo.com/json/2/crm.lead/create")
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-api-key")
        self.assertEqual(request["json"], {"vals_list": [{"name": "Offline test"}]})
        self.assertFalse(session.trust_env)


class TestSocialSkill(unittest.TestCase):
    def test_publish_requires_grant_but_draft_is_available(self):
        adapter = InMemorySocialAdapter()
        skill = SocialMediaSkill(adapter=adapter)
        draft = find_tool(skill, "social.prepare_post").execute(
            platform="instagram", body="A reviewed post"
        )
        blocked = find_tool(skill, "social.publish").execute(
            platform="instagram", post=draft.output
        )
        allowed = find_tool(SocialMediaSkill(
            adapter=adapter,
            capability_grant=HostCapabilityGrant.issue(scopes={"social.publish"}, ttl_seconds=60),
        ), "social.publish").execute(platform="instagram", post=draft.output)

        self.assertTrue(draft.success)
        self.assertFalse(blocked.success)
        self.assertTrue(allowed.success)
        self.assertTrue(allowed.output["mock"])


class TestBrowserGrantBoundary(unittest.TestCase):
    def test_default_direct_egress_is_off_and_host_can_grant_it(self):
        manager = BrowserSessionManager()
        with self.assertRaises(BrowserPolicyError):
            manager._egress()

        granted = BrowserSessionManager(capability_grant=HostCapabilityGrant.issue(
            scopes={"network.direct"}, ttl_seconds=60
        ))
        self.assertEqual(granted._egress(), (None, False))

    def test_write_methods_require_explicit_browser_write_grant(self):
        class FakeLocator:
            clicked = False
            def click(self, **kwargs):
                self.clicked = True

        class FakePage:
            url = "https://example.com/form"
            locator_instance = FakeLocator()
            def locator(self, selector):
                return self.locator_instance

        def manager_with(grant):
            manager = BrowserSessionManager(
                policy=BrowserPolicy(allow_direct_egress=True),
                capability_grant=grant,
            )
            manager._sessions["a" * 32] = _BrowserSession(
                context=object(),
                page=FakePage(),
                created_at=time.monotonic(),
                last_used=time.monotonic(),
                proxy_url=None,
            )
            return manager

        with self.assertRaisesRegex(BrowserPolicyError, "disabled"):
            manager_with(None).click("a" * 32, "button[type=submit]")

        grant = HostCapabilityGrant.issue(scopes={"browser.write"}, ttl_seconds=60)
        result = manager_with(grant).click("a" * 32, "button[type=submit]")
        self.assertEqual(result["clicked"], "button[type=submit]")


if __name__ == "__main__":
    unittest.main()
