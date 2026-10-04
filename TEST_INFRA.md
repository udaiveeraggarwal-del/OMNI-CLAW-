# OmniAgent Test Infrastructure Specification (`TEST_INFRA.md`)

## 1. Test Philosophy & Design Principles

The OmniAgent test architecture is engineered to guarantee production readiness, reliability, and security across all five architectural layers of the framework. Testing adheres to the following foundational principles:

1. **Opaque-Box Requirement Derivation**:
   Tests are derived exclusively from the authoritative specifications in `ORIGINAL_REQUEST.md` and the interface contracts in `PROJECT.md`. Tests do not inspect or depend on private implementation internals, allowing implementations to refactor without breaking behavioral guarantees.

2. **Progressive Testability & Isolation**:
   Every test is self-contained, independent of execution order, creates its own ephemeral state (isolated temporary directories, in-memory databases, dynamic loopback ports), and cleans up resources deterministically.

3. **Deterministic Verification & Offline-First Oracles**:
   E2E tests must never fail due to intermittent external network conditions, rate limits, or missing paid API keys. All network operations, LLM endpoints (OpenAI, Anthropic, Gemini), and external ERP/Social services have deterministic offline mock oracles with identical request/response wire schemas.

4. **Adversarial & Security-First Verification**:
   Specialized boundary tests subject the system to adversarial inputs, including SSRF attempts against cloud metadata endpoints (`169.254.169.254`), secret credential leakage in logs/traces, malicious JSON/YAML payloads, infinite recursion in workflow graphs, and proxy connection drops.

5. **Multi-Tier Testing Pyramid**:
   The test hierarchy is stratified into four rigorous tiers:
   - **Tier 1**: Individual Feature Coverage (>=5 test cases per feature)
   - **Tier 2**: Boundary & Adversarial Corner Cases (>=5 test cases per feature)
   - **Tier 3**: Cross-Feature Pairwise Interactions
   - **Tier 4**: Real-World End-to-End Application Scenarios

---

## 2. Feature Inventory Coverage Matrix

| Feature # | Feature Name | Layer / Milestone | Tier 1 Tests | Tier 2 Boundaries | Tier 3 Combinations | Tier 4 Scenarios | Target Status |
|---|---|---|---|---|---|---|---|
| F1-F2 | Canonical Models & Provider Normalization | M1 Core Engine | >=5 | >=5 | Pairwise with Planner | Scenario 1, 4 | Covered |
| F3 | Mock LLM Providers (OpenAI, Anthropic, Gemini) | M1 Core Engine | >=5 | >=5 | Provider switching | Scenario 1, 2, 3, 4, 5 | Covered |
| F4 | State Persistence (Memory, File, SQLite ACID) | M1 Core Engine | >=5 | >=5 | State + Planner | Scenario 2, 5 | Covered |
| F5 | Memory Subsystem (Sliding, Summary, Semantic) | M1 Core Engine | >=5 | >=5 | Memory + Multi-Step | Scenario 1, 5 | Covered |
| F6-F8 | Multi-Step Planner & Tool Router | M1 Core Engine | >=5 | >=5 | Planner + Skills | Scenario 1, 2, 3 | Covered |
| F9-F11 | Cryptography (AEAD, ECDH) & 3-Hop Onion Router | M2 Security | >=5 | >=5 | Crypto + Tunnel | Scenario 4 | Covered |
| F12-F14 | Zero-DNS-Leak SOCKS5 & Header Scrubber | M2 Security | >=5 | >=5 | Tunnel + Browser/LLM | Scenario 4 | Covered |
| F15-F16 | Browser Operate Skill & SSRF Guard | M3 Skills | >=5 | >=5 | Browser + Planner | Scenario 1 | Covered |
| F17 | Code Runner Sandbox & Secret Scrubbing | M3 Skills | >=5 | >=5 | Code + Planner | Scenario 2 | Covered |
| F18 | Odoo Builder (Pages & Products) | M3 Skills | >=5 | >=5 | Odoo + Workflow | Scenario 2 | Covered |
| F19 | Social Media (YouTube & Instagram) | M3 Skills | >=5 | >=5 | Social + Workflow | Scenario 3 | Covered |
| F20 | SEO Optimizer (Audits & Schema.org) | M3 Skills | >=5 | >=5 | SEO + Browser | Scenario 1 | Covered |
| F21 | Agent-as-Judge 100-pt Rubric | M3 Skills | >=5 | >=5 | Judge + Skills | Scenario 3 | Covered |
| F22-F27 | Visual Workflow Builder (Canvas, DAG, Deploy) | M4 Workflow UI | >=5 | >=5 | UI Runner + Skills | Scenario 5 | Covered |

---

## 3. Test Suite Architecture

```
tests/e2e/
├── __init__.py
├── conftest.py                  # Pytest fixtures, mock servers, loopback helpers
├── test_tier1_features.py       # Tier 1: Exhaustive feature coverage (>=5 per feature)
├── test_tier2_boundaries.py     # Tier 2: Boundary, corner case, and stress tests (>=5 per category)
├── test_tier3_combinations.py   # Tier 3: Cross-feature pairwise interaction tests
├── test_tier4_scenarios.py      # Tier 4: 5 Real-world application scenarios
└── run_all_e2e.py               # Standalone runner with colored CLI reporting and exit codes
```

### 3.1 Tier 1: Feature Coverage Specifications
- **Core Models & Providers**:
  - Test serialization and deserialization of `Message`, `ToolCall`, `TokenUsage`, `LLMResponse`, `ToolResult`.
  - Test `MockOpenAIProvider`, `MockAnthropicProvider`, `MockGeminiProvider` wire schema translation.
  - Test `ProviderFactory` dynamic resolution by name.
- **Memory & State**:
  - Test `SlidingWindowMemory` context trimming.
  - Test `SummaryMemory` rolling compaction.
  - Test `SemanticMemory` relevance retrieval.
  - Test `InMemoryStateStore`, `FileStateStore`, `SQLiteStateStore` transactional save/load.
- **Tool Routing & Planning**:
  - Test `ToolRegistry` registration and JSON schema validation.
  - Test `ToolRouter` dynamic argument dispatch.
  - Test `MultiStepPlanner` plan generation and step execution.
- **Skills**:
  - Test `browser_operate` DOM navigation and content extraction.
  - Test `code_runner` Python, JS, and Shell execution with output capture.
  - Test `odoo_builder` product and page creation with mock XML-RPC backend.
  - Test `social_media` YouTube script generation and Instagram caption/tag formatting.
  - Test `seo_optimizer` page health score (0-100) and JSON-LD schema generation.
- **Privacy & Security**:
  - Test AES-256-GCM / ChaCha20-Poly1305 symmetric encryption.
  - Test ECDH X25519 key exchange and HKDF derivation.
  - Test 3-hop onion circuit peeling math.
  - Test SOCKS5 proxy handshake and remote domain parsing (`ATYP=0x03`).
- **Visual Workflow**:
  - Test workflow graph compilation and DAG topological sorting.
  - Test bidirectional JSON & YAML serialization.

### 3.2 Tier 2: Boundary & Corner Cases Specifications
- **Empty & Extreme Tokens**:
  - Empty prompt string, zero token handling, Unicode control character handling.
  - Context overflow beyond max token window with graceful degradation.
- **Tool Timeouts & Process Limits**:
  - Code runner timeout enforcement when child process sleeps or loops infinitely.
  - Child process tree termination and orphaned process cleanup.
- **SSRF Blocklist & Network Isolation**:
  - Target URL pointing to AWS/GCP metadata (`http://169.254.169.254/latest/meta-data`).
  - Target URL pointing to private subnets (`http://127.0.0.1:8080`, `http://10.0.0.1`, `http://192.168.1.1`).
  - Dangerous URL schemes blocked (`file:///etc/passwd`, `gopher://`).
- **Schema Validation & Malformed Payloads**:
  - Invalid JSON / YAML workflow structure missing required nodes or edges.
  - Invalid tool call arguments failing `jsonschema` validation.
- **DAG Cycle Detection**:
  - Self-loop connections (`node_A -> node_A`).
  - Circular multi-node dependencies (`node_A -> node_B -> node_A`) detected with clear error.
- **Credential Scrubbing**:
  - Host environment variable leakage protection (`API_KEY`, `AWS_SECRET_ACCESS_KEY`, `TOKEN`).
  - Scrubbing sensitive headers and tokens in logged execution traces.
- **Proxy Drops & Fallbacks**:
  - Network connection drop / unreachable proxy port handled without unhandled exception.

### 3.3 Tier 3: Cross-Feature Combinations
- **Combo 1**: Multi-step planner generating and executing Python code via `code_runner` to solve a math/data task.
- **Combo 2**: Multi-step planner issuing browser commands via `browser_operate` and reflecting on page contents.
- **Combo 3**: Multi-step planner routing LLM requests through `NetworkSecurityContext` and encrypted proxy tunnel.
- **Combo 4**: Workflow DAG executing sequential skill invocations (`odoo_builder` product creation -> `social_media` promotion post).
- **Combo 5**: Multi-turn planner preserving conversation history across session reboots using `SQLiteStateStore` and `SlidingWindowMemory`.

### 3.4 Tier 4: Real-World Application Scenarios
- **Scenario 1 (Autonomous Research Agent)**:
  `browser_operate` navigates to target documentation -> `seo_optimizer` audits technical health -> `MultiStepPlanner` synthesizes an executive summary report.
- **Scenario 2 (E-Commerce Automation)**:
  Input data parsed by `code_runner` -> `odoo_builder` creates eCommerce product categories, products, and website landing pages -> outputs live store catalog.
- **Scenario 3 (Social Media Content Engine)**:
  Trend input parsed -> LLM generates viral video concept -> `social_media` creates 5-part YouTube script (Hook, Intro, Scenes, Script, CTA) + Instagram carousel post -> `Agent-as-Judge` scores schema compliance >= 90/100.
- **Scenario 4 (Privacy-Preserving Agent Operation)**:
  Loopback SOCKS5 proxy active -> LLM request routed through encrypted tunnel -> tracking headers stripped -> target web data fetched with zero DNS leakage.
- **Scenario 5 (Visual Workflow Execution Pipeline)**:
  Complex workflow JSON with Trigger, LLM reasoning, Tool execution, and Output aggregation loaded -> validated -> executed by `WorkflowRunner` -> produces synthesized result payload.

---

## 4. Test Runner & Execution Specification

The E2E test suite can be executed in two interchangeable ways:

### Standard Command
```bash
python tests/e2e/run_all_e2e.py
```

### Pytest Command
```bash
pytest tests/e2e -v
```

### Execution Metrics & Exit Codes
- **Exit Code 0**: All test cases across all 4 Tiers passed successfully.
- **Exit Code 1**: One or more test failures, errors, or assertion violations occurred.
- **Execution Budget**: Entire suite must execute in under 30 seconds on standard developer hardware.

---

## 5. Coverage Thresholds & Quality Gates

| Metric | Required Threshold |
|---|---|
| Tier 1 Feature Tests | >= 60 tests (>=5 per feature) |
| Tier 2 Boundary Tests | >= 40 tests (>=5 per category) |
| Tier 3 Cross-Feature Tests | >= 10 tests |
| Tier 4 Real-World Scenarios | >= 5 end-to-end scenarios |
| Total E2E Tests | >= 115 tests |
| Test Pass Rate | 100.0% |
| Flakiness / Retries Needed | 0 |
| Offline / Network Independence | 100% (No external API keys required) |
