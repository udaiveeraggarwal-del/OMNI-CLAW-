# OmniAgent

OmniAgent is an early provider-neutral agent framework. The repository now includes its Python core, quota-aware provider routing, host-granted machine actions, browser controls, and initial Odoo and social-media connectors. The visual workflow builder and production-ready end-to-end product are still under development.

## Install the Python package

From the repository folder, run:

    python -m pip install -e .

Create the built-in registry from Python:

    from omniagent.skills import create_builtin_registry

    skills = create_builtin_registry()
    print([skill["id"] for skill in skills.list_skills()])

The SEO tools work on HTML and text supplied to the framework. They do not fetch pages or publish changes. Odoo and social skills accept injected connectors; no external account is contacted unless the host configures credentials/adapters and grants the action.

The browser skill provides short-lived Playwright sessions for navigation, page reading, and form filling. Network access is disabled by default. Direct egress requires a host-issued `network.direct` grant or host policy, and privacy-proxy egress remains blocked until the M2 readiness gate is cleared. Clicks require a host approval callback or `browser.write` grant; form filling does not submit. A network firewall is still required for production isolation.

## Provider routing

`ProviderPool` can rotate among user-configured providers. It only retries automatically on a definite quota/rate-limit response by default; ambiguous failures are returned without repeating a request that might already have incurred cost. Free routes are eligible by default. The host must explicitly enable `prepaid` or `paid` routes, and may set local request-per-minute and request-per-day ceilings.

For OpenAI-compatible gateways such as OpenRouter, use the OpenAI adapter with the gateway base URL and a key supplied by the user. Verify the model slug and free-tier limits at the provider before configuring them; availability and quotas can change.

```python
import os
from omniagent.core.providers.factory import ProviderFactory

pool = ProviderFactory.create_pool([
    {
        "route_id": "openrouter-free",
        "provider": "openai",
        "model": "provider/model:free",
        "api_key": os.environ["OPENROUTER_API_KEY"],
        "cost_tier": "free",
        "requests_per_minute": 10,
        "options": {"base_url": "https://openrouter.ai/api/v1"},
    },
])
```

An installed and authenticated Antigravity CLI can be configured as a separate `prepaid` route that uses its own local sign-in and account limits. It does not turn a Gemini consumer subscription into Gemini API quota or expose OAuth credentials to OmniAgent. Set `allowed_cost_tiers={"free", "prepaid"}` only when the host intentionally enables both classes.

## Host control and skill adapters

Machine operations require a `HostCapabilityGrant` created by trusted host code after consent. Grants expire and may scope filesystem roots, processes, and UI actions; the model cannot create or widen one. The desktop control tool is an adapter interface, so a host integration must provide the actual operating-system UI backend.

The Odoo connector targets the Odoo 19 JSON-2 interface. Odoo documents that API access depends on plan, and JSON-2 is limited to eligible Custom plans; other editions need a separately supported connector. Odoo network egress is opt-in. Social publishing and analytics require a separately injected platform adapter using the account owner's supported OAuth flow. The included in-memory adapters are local demos only.

## Code execution requirements

The code-runner skill executes Python and JavaScript only in Docker containers. It does not fall back to running generated code directly on the host. To use it:

1. Install and start Docker Desktop or another local Docker Engine.
2. Pull the configured language images while you are online:

       docker pull python:3.12-alpine
       docker pull node:22-alpine

3. Register the built-in skills and invoke the code_runner.run action.

Each run disables container networking, mounts the source read-only, drops Linux capabilities, applies CPU, memory, process, time, file-size, and output limits, and passes no host environment variables into the container.

The Docker backend is an MVP isolation boundary. A shared Docker daemon is not a dedicated virtual machine; production deployments that run untrusted code for multiple tenants should use isolated microVM workers and a controlled egress layer.

To install browser support, install the optional dependency and Chromium:

    python -m pip install -e ".[browser]"
    playwright install chromium

## Current scope

- Core: canonical messages, provider adapters, memory, persistence, tool routing, and planning.
- Skills started: namespaced skill manifests and registry; offline SEO audit and metadata drafts; Docker-backed Python and JavaScript execution; restricted browser actions; expiring machine capability grants; Odoo JSON-2 and social-provider adapter boundaries.
- In progress elsewhere: privacy/networking subsystem and visual workflow builder.
- External provider publishing requires host-injected adapters and user credentials; no production Instagram/YouTube publisher is bundled yet.
- The framework is an MVP: it does not claim anonymous/untrackable network communications, unrestricted account access, or a production-ready workflow deployment service.

See PROJECT.md for the milestone tracker and interface contracts, and AGENT_SYNC.md for coordination with Antigravity.
