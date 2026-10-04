# Project: OmniAgent

Universal AI Agentic Framework inspired by DeepSeek, Claude Code, OpenClaw, AutoGPT, and LangChain distillation, supporting multiple LLMs, multi-step reasoning, privacy-preserving networking, pre-built skill ecosystem, and a visual workflow builder.

## Architecture

OmniAgent is architected into five decoupled layers communicating via strictly typed dataclass contracts:

```
+-------------------------------------------------------------------------------+
|                    R2: Visual Workflow Builder (Web UI)                       |
|   - Interactive Pan/Zoom Canvas with Grid Snapping & DOM Node Cards           |
|   - Dynamic SVG Bezier Connection System                                      |
|   - Bidirectional JSON & YAML Workflow Serialization                          |
|   - API Key Management Modal & Deployment Engine (Run / Webhook / Script)     |
+-------------------------------------------------------------------------------+
                                        | (HTTP / JSON / CLI)
                                        v
+-------------------------------------------------------------------------------+
|                  R1: Core Engine & Multi-LLM Abstraction                      |
|   - Canonical Schema: Message, ToolCall, ToolDefinition, TokenUsage           |
|   - Provider Normalization: OpenAI, Anthropic, Gemini, Mock Providers        |
|   - Memory Subsystem: Sliding Window, Summary Buffer, Semantic Store          |
|   - State Persistence: InMemory, File, SQLite ACID Stores                     |
|   - Multi-Step Planner: DeepSeek R1 Reasoning Traces, Plan-Act-Reflect Loop   |
|   - Tool Routing Engine: JSON Schema Validation, Dynamic Parameter Mapping    |
+-------------------------------------------------------------------------------+
                    |                                           |
                    v                                           v
+---------------------------------------+   +-----------------------------------+
|      R3: Pre-built Skill Ecosystem    |   | R4: Privacy & Security Network    |
| - browser_operate (Playwright / DOM)  |   | - AEAD: AES-256-GCM / ChaCha20    |
| - code_runner (Isolated Temp Sandbox) |   | - ECDH X25519 & HKDF-SHA256       |
| - odoo_builder (XML-RPC / Mock)       |   | - Tor-inspired 3-Hop Onion Router |
| - social_media (Instagram / YouTube)  |   | - Loopback SOCKS5 Proxy Gateway   |
| - seo_optimizer (Page Audit & Schema) |   | - Zero DNS Leaks & Scrubbing      |
| - Agent-as-Judge 100-pt Evaluation    |   | - NetworkSecurityContext Wrapper  |
+---------------------------------------+   +-----------------------------------+
```

---

## Feature Inventory

Every requirement from the initial request, additions, and survey reports is inventoried and mapped to a milestone below.

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Canonical Message & Tool Models | Standard dataclasses for `Message`, `ToolCall`, `ToolDefinition`, `TokenUsage`, `LLMResponse`. | M1 | Survey 1 (R1) |
| 2 | Multi-Provider Normalization | Normalized adapters converting canonical models to/from OpenAI, Anthropic, Gemini wire schemas. | M1 | Survey 1 (R1) |
| 3 | 3 Mock API Providers | Offline, deterministic mock implementations for OpenAI, Anthropic, and Gemini wire formats. | M1 | Survey 1 (R1 Acceptance) |
| 4 | State Persistence Store | Layered storage engine supporting in-memory, file-based JSON, and SQLite persistence. | M1 | Survey 1 (R1) |
| 5 | Hierarchical Memory Subsystem | Sliding context window, summary rolling buffer, and semantic keyword retrieval store. | M1 | Survey 1 & Distillation |
| 6 | Multi-Step Reasoning Planner | DeepSeek R1 `<think>` reflection & Claude Code/OpenClaw plan-act-reflect execution loop. | M1 | Survey 1 & Distillation |
| 7 | Tool Registry & Schema Router | Registry validating tool calls via `jsonschema` and routing parameters safely. | M1 | Survey 1 (R1) |
| 8 | Multi-Tool Routing & Synthesis | Planner successfully routes >=2 tools (`data_fetcher` + `data_analyzer`) to synthesized output. | M1 | Survey 1 (R1 Acceptance) |
| 9 | Cryptographic Primitives & AEAD | AES-256-GCM, ChaCha20-Poly1305, ECDH X25519, and HKDF-SHA256 key derivation. | M2 | Survey 4 (R4) |
| 10 | Tor-Inspired 3-Hop Onion Routing | Telescoping circuit establishment (Entry Guard, Middle Relay, Exit Node) with 512-byte cells. | M2 | Survey 4 (R4) |
| 11 | Layered Concentric Encryption | Nested encryption per hop with sequential onion peeling and reverse encryption. | M2 | Survey 4 (R4) |
| 12 | Zero-DNS-Leak SOCKS5 Gateway | Loopback SOCKS5 proxy server supporting remote domain resolution (`ATYP=0x03`). | M2 | Survey 4 (R4) |
| 13 | Fingerprint & Header Scrubbing | Stripping tracking headers, normalising User-Agent, and masking browser fingerprints. | M2 | Survey 4 (R4) |
| 14 | Network Security Context | Seamless routing of outbound LLM and tool HTTP/WebSocket requests through privacy tunnel. | M2 | Survey 4 (R4) |
| 15 | Canonical Skill Schema Contract | Formal JSON Schema (`omni_skill_schema.json`) defining actions, parameters, authentication. | M3 | Survey 3 (R3) |
| 16 | Browser Operate Skill | Browser automation supporting Playwright (Chrome/Edge channels) and headless DOM fallback with SSRF blocklist. | M3 | Survey 3 (R3 & Acceptance) |
| 17 | Code Runner Skill | Isolated sandbox executing Python, JS, and Shell with tempdir, secret scrubbing, and timeouts. | M3 | Survey 3 (R3 & Acceptance) |
| 18 | Odoo Builder Skill | Odoo website page creation and eCommerce product management with XML-RPC & Mock connector. | M3 | Survey 3 (R3) |
| 19 | Social Media Skill | Instagram publishing & analytics, YouTube structured script generation, metadata & analytics. | M3 | Survey 3 (R3) |
| 20 | SEO Optimizer Skill | Technical page audits, health scoring (0-100), keyword density, and Schema.org JSON-LD generation. | M3 | Survey 3 (R3) |
| 21 | Agent-as-Judge Evaluator | 100-point rubric evaluator validating Odoo and Social Media skill schema compliance. | M3 | Survey 3 (R3 Acceptance) |
| 22 | Interactive Canvas & Grid Snapping | Infinite pan/zoom canvas with grid snapping guides and DOM node cards. | M4 | Survey 2 (R2) |
| 23 | Draggable Component Palette | Sidebar categorizing Trigger, LLM, Tool, and Action node templates. | M4 | Survey 2 (R2) |
| 24 | SVG Bezier Connection System | Dynamic cubic bezier wires connecting output ports to input ports with validation. | M4 | Survey 2 (R2) |
| 25 | Bidirectional JSON & YAML Serialization | Full workflow graph export and import restoring node states and edges. | M4 | Survey 2 (R2 Acceptance) |
| 26 | API Key Management Modal | Non-technical settings panel for entering and masking LLM keys safely. | M4 | Survey 2 (R2) |
| 27 | Deployment Modalities | Live run visual step highlighting, Webhook REST endpoint deployment, and standalone script export. | M4 | Survey 2 (R2) |
| 28 | Playwright E2E Test Suite | Browser tests verifying drag trigger -> drag action -> wire connect -> save JSON/YAML file. | M4 | Survey 2 (R2 Acceptance) |
| 29 | Architectural Distillation Synthesis | Integration of DeepSeek, Claude Code, OpenClaw, AutoGPT, and LangChain best practices. | M5 | User Distillation Mandate |
| 30 | End-to-End System Hardening | Full multi-tier test suite execution (Tiers 1-4) and adversarial coverage hardening (Tier 5). | M5 | E2E Hardening Mandate |
| 31 | Vercel Deployment Configuration | Serverless/edge API entry point (`api/index.py`), `vercel.json` routing, static build config, and serverless compatibility. | M4 | User Vercel Mandate |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Core Engine & LLM Abstraction | Canonical models, OpenAI/Anthropic/Gemini/Mock providers, state store, memory, multi-step planner, tool router. | none | DONE (71 unit tests passed, 118 E2E tests passed, Gate PASSED) |
| M2 | Privacy & Security Network Protocols | Cryptographic primitives (AEAD, ECDH), Tor 3-hop onion router, loopback SOCKS5 gateway, DNS leak protection, fingerprint scrubber. | M1 | IN_PROGRESS (worker_m2: 5c82058f-1a3a-4c48-a8ec-1007414d3a58) |
| M3 | Pre-built Skill Ecosystem | `browser_operate`, safe `code_runner`, `odoo_builder` (XML-RPC/Mock), `social_media` (Instagram/YouTube), `seo_optimizer`, Agent-as-Judge evaluation. | M1 | IN_PROGRESS (skill registry, SEO tools, Docker code runner) |
| M4 | Visual Workflow Builder UI | Embedded web application, canvas drag-and-drop, SVG bezier wires, JSON/YAML serialization, API key modal, deployment engine, Vercel serverless entry (`api/index.py`, `vercel.json`), Playwright E2E tests. | M1, M3 | PLANNED |
| M5 | Integrated System, Distillation & Hardening | Cross-layer integration, architectural distillation synthesis, Vercel deployment verification, passing 100% of E2E test suite (Tiers 1-4), adversarial hardening (Tier 5). | M1, M2, M3, M4 | PLANNED |

---

## Interface Contracts

### M1 ↔ M2 (Core Engine ↔ Privacy Network)
- `NetworkSecurityContext(enabled: bool, proxy_url: Optional[str], route_dns_remotely: bool, scrub_fingerprints: bool)`
- `PrivacyNetworkManager.get_proxy_url() -> str` (e.g. `socks5://127.0.0.1:9055`)
- `BaseLLMProvider.set_security_context(ctx: NetworkSecurityContext)`
- Outbound HTTP requests from LLM providers use `requests.Session(proxies={"http": proxy, "https": proxy})` when privacy context is enabled.

### M1 ↔ M3 (Core Engine ↔ Pre-built Skills)
- `BaseTool.to_definition() -> ToolDefinition`
- `BaseTool.execute(**kwargs) -> ToolResult(success: bool, output: Any, error: Optional[str], metadata: dict)`
- `ToolRegistry.register(tool: BaseTool)`
- `ToolRouter.route(tool_call: ToolCall) -> ToolResult`
- Each skill package exports a class inheriting `BaseTool` or registering its actions with `ToolRegistry`.

### M2 ↔ M3 (Privacy Network ↔ Web Skills)
- `browser_operate` receives `--proxy-server=socks5://127.0.0.1:<port>` when privacy is enabled.
- Web scrapers and API clients in `seo_optimizer` and `social_media` utilize `NetworkSecurityContext.create_session()`.

### M1/M3 ↔ M4 (Core & Skills ↔ Visual Workflow Builder)
- Workflow JSON/YAML Schema:
  ```json
  {
    "schema_version": "1.0.0",
    "metadata": {"name": "string", "description": "string"},
    "nodes": [{"id": "string", "type": "trigger|llm|tool|action", "subtype": "string", "position": {"x": 0, "y": 0}, "config": {}}],
    "edges": [{"id": "string", "source": "node_id", "source_port": "port_id", "target": "node_id", "target_port": "port_id"}]
  }
  ```
- Workflow Runner Engine:
  `WorkflowRunner.execute_workflow(workflow_dict: dict, input_payload: dict, session_id: str) -> dict`
- Server Endpoints:
  - `GET /` -> Serves embedded visual builder UI.
  - `GET /api/skills` -> Returns available skill tools for canvas palette.
  - `POST /api/workflows/validate` -> Validates JSON/YAML workflow schema.
  - `POST /api/workflows/run` -> Executes workflow and returns live execution step traces.
  - `POST /api/deployments/{id}/run` -> Webhook REST execution endpoint.

---

## Code Layout

```
C:\Users\udaiv\.gemini\antigravity\scratch\omniagent\
├── omniagent/
│   ├── __init__.py
│   ├── core/                           # M1: Core Engine & LLM Abstraction
│   │   ├── __init__.py
│   │   ├── models.py                   # Canonical Message, ToolCall, LLMResponse, etc.
│   │   ├── state.py                    # InMemoryStateStore, FileStateStore, SQLiteStateStore
│   │   ├── memory.py                   # SlidingWindowMemory, SummaryMemory, SemanticMemory
│   │   ├── planner.py                  # MultiStepPlanner, ExecutionPlan, ReflectionEngine
│   │   ├── router.py                   # ToolRegistry, ToolRouter, BaseTool
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── base.py                 # BaseLLMProvider
│   │       ├── openai_provider.py      # OpenAI Provider & MockOpenAIProvider
│   │       ├── anthropic_provider.py   # Anthropic Provider & MockAnthropicProvider
│   │       ├── gemini_provider.py      # Gemini Provider & MockGeminiProvider
│   │       └── factory.py              # ProviderFactory (resolves by name / mock mode)
│   ├── security/                       # M2: Privacy & Security Network Protocols
│   │   ├── __init__.py
│   │   ├── crypto.py                   # AEAD (AES-GCM / ChaCha20-Poly1305), ECDH X25519, HKDF
│   │   ├── onion.py                    # 3-hop Onion Router, Cell protocol (512B), Relays
│   │   ├── proxy.py                    # Embedded loopback SOCKS5 proxy server
│   │   ├── scrubber.py                 # Fingerprint & header scrubber (HTTP & Playwright)
│   │   └── manager.py                  # PrivacyNetworkManager, NetworkSecurityContext
│   ├── skills/                         # M3: Pre-built Skill Ecosystem
│   │   ├── __init__.py
│   │   ├── base.py                     # OmniSkillDefinition, SkillAction, omni_skill_schema.json
│   │   ├── browser_operate/
│   │   │   ├── __init__.py
│   │   │   └── skill.py                # Browser automation (Playwright + DOM fallback, SSRF guard)
│   │   ├── code_runner/
│   │   │   ├── __init__.py
│   │   │   └── skill.py                # Safe code execution sandbox (Python/JS/Shell)
│   │   ├── odoo_builder/
│   │   │   ├── __init__.py
│   │   │   ├── skill.py                # Odoo website & eCommerce builder
│   │   │   └── mock_odoo.py            # Stateful in-memory Odoo XML-RPC / JSON-RPC mock
│   │   ├── social_media/
│   │   │   ├── __init__.py
│   │   │   ├── skill.py                # Instagram publishing & YouTube structured script generator
│   │   │   └── mock_social.py          # Stateful in-memory Social Media mock
│   │   ├── seo_optimizer/
│   │   │   ├── __init__.py
│   │   │   └── skill.py                # Technical SEO audit, keyword analysis, JSON-LD generator
│   │   └── judge/
│   │       ├── __init__.py
│   │       └── evaluator.py            # Agent-as-Judge 100-point rubric schema evaluator
│   ├── ui/                             # M4: Visual Workflow Builder
│   │   ├── __init__.py
│   │   ├── server.py                   # Embedded HTTP/API server (FastAPI/Starlette or stdlib)
│   │   ├── runner.py                   # WorkflowRunner (graph compiler & step executor)
│   │   ├── static/                     # Embedded Web App assets
│   │   │   ├── index.html              # Clean single-page canvas & layout
│   │   │   ├── css/
│   │   │   │   └── styles.css          # Modern dark/light theme, node cards, grid styling
│   │   │   └── js/
│   │   │       ├── app.js              # Canvas controller, event bus, drag & drop
│   │   │       ├── nodes.js            # Node templates, port definitions, renderers
│   │   │       ├── wires.js            # SVG Bezier connection engine & port snapping
│   │   │       ├── serializer.js       # Bidirectional JSON & YAML export/import
│   │   │       └── modal.js            # API key management & deployment modals
│   └── cli.py                          # Unified CLI entry points
├── tests/
│   ├── e2e/                            # Opaque-box E2E test suite (Tiers 1-4)
│   ├── unit/
│   │   ├── test_models.py
│   │   ├── test_providers.py           # Acceptance criterion: >=3 mock providers init
│   │   ├── test_planner.py             # Acceptance criterion: route >=2 tools & synthesize
│   │   ├── test_security.py            # R4 encryption, onion routing, socks5 tunneling
│   │   ├── test_skills_integration.py  # Acceptance criterion: browser_operate & code_runner
│   │   ├── test_skills_judge.py        # Acceptance criterion: Agent-as-Judge evaluation
│   │   └── test_workflow_e2e.py        # Acceptance criterion: Playwright UI drag/connect/save
│   └── conftest.py
├── ORIGINAL_REQUEST.md
├── PROJECT.md
└── README.md
```

### M3 implementation status — 2026-10-04

- Added a versioned, namespaced skill contract and registry with action JSON Schema validation.
- Added an offline SEO HTML audit and reviewable metadata / WebPage JSON-LD draft. These tools do not fetch pages or publish edits.
- Added a Python and JavaScript code runner that fails closed without Docker and uses no network, a read-only root, reduced privileges, resource limits, and capped output.
- Browser automation, Odoo and social-media connectors, and judge evaluation remain unimplemented.