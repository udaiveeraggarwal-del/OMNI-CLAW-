# OmniAgent

OmniAgent is an early, provider-neutral agent framework. The repository currently contains its Python core and the first offline SEO and sandboxed code-runner skills. It is not yet the complete visual, business-automation product described in the project plan.

## Install the Python package

From the repository folder, run:

    python -m pip install -e .

Create the built-in registry from Python:

    from omniagent.skills import create_builtin_registry

    skills = create_builtin_registry()
    print([skill["id"] for skill in skills.list_skills()])

The SEO tools work on HTML and text supplied to the framework. They do not fetch pages or publish changes.

## Code execution requirements

The code-runner skill executes Python and JavaScript only in Docker containers. It does not fall back to running generated code directly on the host. To use it:

1. Install and start Docker Desktop or another local Docker Engine.
2. Pull the configured language images while you are online:

       docker pull python:3.12-alpine
       docker pull node:22-alpine

3. Register the built-in skills and invoke the code_runner.run action.

Each run disables container networking, mounts the source read-only, drops Linux capabilities, applies CPU, memory, process, time, file-size, and output limits, and passes no host environment variables into the container.

The Docker backend is an MVP isolation boundary. A shared Docker daemon is not a dedicated virtual machine; production deployments that run untrusted code for multiple tenants should use isolated microVM workers and a controlled egress layer.

## Current scope

- Core: canonical messages, provider adapters, memory, persistence, tool routing, and planning.
- Skills started: namespaced skill manifests and registry; offline SEO audit and metadata drafts; Docker-backed Python and JavaScript execution.
- In progress elsewhere: privacy/networking subsystem.
- Not implemented yet: browser operator, Odoo connector, Instagram/YouTube publishing, visual workflow builder, deployment UI, and business-level approvals.

See PROJECT.md for the milestone tracker and interface contracts, and AGENT_SYNC.md for coordination with Antigravity.
