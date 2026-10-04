# Original User Request

## 2026-10-04T08:03:08Z

Build "OmniAgent," a universal AI agentic framework inspired by DeepSeek/Claude Code/OpenClaw distillation, supporting multiple LLMs (OpenAI, Anthropic, Gemini, etc.), multi-step reasoning, and browser/API tools for full-stack autonomous web operations. This must be the complete production-ready framework with all specified domain skills (Odoo, Social Media, SEO). Use a standard full team.

Working directory: [local Antigravity workspace]
Integrity mode: development

## Requirements

### R1. Core Engine & LLM Abstraction
The system must normalize API requests and responses across major LLM providers based on user-provided API keys. It must handle state, multi-step planning, memory, and routing tool calls.

### R2. Visual Workflow Builder
The framework must provide a UI where non-technical users can enter an API key, drag-and-drop workflow nodes (triggers, LLM logic, tools), and deploy them.

### R3. Pre-built Skill Ecosystem
The framework must include functional skill integrations for browser automation, safe code execution, Odoo website building, and social media management (Instagram, YouTube).

## Verification Resources
The user will provide existing test scripts and evaluation guidelines in the working directory prior to the full test suite run. The team must utilize these resources for verification.

## Acceptance Criteria

### Core Engine Tests
- [ ] Automated tests pass for initializing the LLM abstraction layer with at least 3 different mock API providers.
- [ ] A multi-step planner test script successfully routes a mock user request through at least 2 distinct tools and returns a synthesized result.

### Workflow UI
- [ ] End-to-end browser tests (e.g., Playwright) verify that a user can drag a trigger node and an action node, connect them, and save the configuration to a JSON/YAML file.

### Skills Verification
- [ ] Programmatic integration tests confirm that the `browser_operate` and `code_runner` skills successfully initialize and execute safe sandbox commands.
- [ ] Agent-as-judge evaluation confirms the Odoo and Social Media skill definitions meet the standard schema requirements for the framework.


## 2026-10-04T08:10:15Z

The user has added a new high-priority requirement for the OmniAgent framework: The framework's network communications and architecture should be highly secured and encrypted, emphasizing privacy and anonymity similar to the Tor browser. Please integrate strong encryption, secure tunneling, and privacy-preserving networking protocols into the framework's architecture and current build.


## 2026-10-04T08:13:35Z

New requirement from the user: Actively distill the architecture, ideas, and frameworks of other leading AI models and agentic frameworks (e.g., DeepSeek, Claude Code, OpenClaw, AutoGPT, LangChain, etc.). Extract their best practices for memory management, token optimization, tool routing, and multi-step task planning, and integrate these distilled concepts into the OmniAgent architecture to optimize its overall performance and capabilities.


## 2026-10-04T08:29:19Z

New user requirement: The OmniAgent framework must be configured for deployment and hosting on Vercel. Please update the architecture and deployment strategy—specifically in Milestone 4 (Visual Workflow Builder / UI Server) and Milestone 5 (Integration)—to ensure the backend APIs and frontend UI are fully compatible with Vercel's serverless/edge environments. Ensure the team generates the necessary `vercel.json`, serverless function entry points (e.g., `api/index.py` or equivalent), and build configurations for seamless Vercel deployment.


## 2026-10-04T08:30:21Z

User update: Maximize the depth of the agentic capabilities (advanced reasoning, multi-step planning, complex tool chaining) to make the framework as powerful and intelligent as possible. Additionally, prioritize extreme 'ease of use' in the UI/UX design (Milestone 4) so it is exceptionally user-friendly for non-technical users.


## 2026-10-04T09:04:59Z

URGENT REQUIREMENT UPDATE: The user has set up a shared GitHub repository for cross-model collaboration with OpenAI Codex. 

Please immediately migrate your working directory and all future file writes/reads from `[local Antigravity workspace]` to the new shared repository at `[shared repository]`. 

Ensure all milestone trackers, test runners, and coding sub-agents are updated with this new root directory immediately.


## 2026-10-04T09:10:56Z

URGENT REMINDER: Ensure the Project Orchestrator and all active workers have actually shifted their working directory to `[shared repository]`. 

The latest progress report stated it was "queued for synchronization upon milestone packaging", but the user is actively working with Codex in the GitHub folder *right now*. Ensure all M2 code and subsequent M3/M4 generation is written directly to the GitHub folder immediately to prevent desync.


## 2026-10-04T09:16:18Z

URGENT COLLABORATION UPDATE: The external OpenAI Codex agent has officially claimed **Milestone 3 (Pre-built Skill Ecosystem)**. Codex is currently writing code in `omniagent/skills/` (including SEO audits and safe code execution).

**Directive:**
1. Do NOT write any code for Milestone 3 (`omniagent/skills/`). Leave that entirely to Codex to avoid merge conflicts.
2. Complete the testing and forensic audit for Milestone 2 (Privacy Network).
3. Once M2 is cleared, proceed directly to **Milestone 4 (Visual Workflow Builder UI)** and **Milestone 5 (Vercel Integration)**.


## 2026-10-04T09:17:49Z

URGENT MANDATE FROM USER: The user expects this framework to be a "top tier model which can change the world". 

Ensure that Milestone 4 (Visual Workflow Builder) and Milestone 5 (Vercel Integration) are engineered to the absolute highest standard of modern software architecture. Accept zero compromises on performance, UX polish, or reliability. Push the absolute limits of current agentic system design.


## 2026-10-04T13:52:04Z

# Teamwork Project Prompt — Draft

> Status: Ready for launch — awaiting user approval
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Full Team

Complete the OmniAgent framework to a full production state. This involves moving M2, M3, M4, and M5 from their current foundational/shell status to fully integrated, live operational systems.

Working directory: C:\Users\udaiv\OneDrive\Documents\GitHub\OMNI-CLAW-
Integrity mode: development

## Requirements

### R1. M2 Secure Network Layer Hardening
The managed SOCKS5 proxy must be upgraded to a real, bounded, full-duplex tunnel with explicit destination controls (ACLs). Do not treat or describe it as providing Tor-level anonymity.

### R2. M4 Visual Workflow Integration
The current UI is a visual shell. It must be wired to backend serialization (JSON/YAML), implement save/load functionality, and the `WorkflowRunner` must actually execute the deployed graph rather than reporting automatic success.

### R3. Autonomous Social Operations & Video Publishing
Port the logic from `auto-social-content-engine` and `autonomous-video-publisher` directly into OMNI-CLAW's M3 skill ecosystem. Move the Instagram and YouTube skills from drafting boundaries to complete real-account publishing, automated trend scanning, video scripting, and rendering pipelines.

### R4. M5 Final Integration & Test Suite Fixes
Fix all failing imports and unit tests, ensure the test suite runs flawlessly against the latest commit, and guarantee no mock data is returned when live mode is active.

## Acceptance Criteria

### Security & Tests
- [ ] `test_socks5_bidirectional_data_tunnel` passes against a local loopback fixture without hitting external plaintext endpoints.
- [ ] The full unit test suite runs with 0 failures at the latest commit.

### Workflow UI
- [ ] A user can construct a graph in the UI, hit "Run", and the backend `WorkflowRunner` actually executes the specific tools in the graph.

### Agent-as-Judge Evaluation
- [ ] An independent Agent-as-Judge script (`evaluator.py`) executes against the newly ported Social and Video publishing skills and scores them 100/100 based on the presence of end-to-end publishing capabilities.
