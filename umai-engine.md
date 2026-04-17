# AI Engine Design Document

| **Author(s)** | Batu Kargili |
| --- | --- |
| **Version/Status** | v0.01 |
| **Date** | 08/12/25 |
| Content Description | This is a complete AI Engine Design chapter: what it is, what it does, how it communicates, and how it should be built and extended |

# **Table of Contents**

- **Introduction & Role in the Platform**
    
    1.1 Purpose of the AI Engine
    
    1.2 How AI Engine Fits into UMAI Architecture
    
    1.3 Responsibilities vs Non-Responsibilities
    
    1.4 High-Level Request Flow (App → UMAI Service → AI Engine → LLM)
    
- **Architecture Overview**
    
    2.1 Service Type & Deployment Model
    
    2.2 Internal Layers & Modules
    
    2.3 Trust Boundaries & Network Topology
    
    2.4 Statelessness, Scaling & Horizontal Replicas
    
- **Core Data Structures & Contracts**
    
    3.1 Internal Request Contract (from UMAI Service)
    
    3.2 Internal Response Contract (to UMAI Service)
    
    3.3 PolicyContext & ExecutionContext Structures
    
    3.4 Decision Object & Action Types
    
    3.5 Error Object & Error Taxonomy
    
- **Policies**
    
    4.1 Policy Structure
    
    4.2 Guardrail and Policy Storage, Retrieval and Execution 
    
- **Standalone Development of AI Engine (MVP)**
    
    5.1 
    

# **Introduction & Role in the Platform**

# 1.1 Purpose of the AI Engine

The primary purpose of the AI Engine is to **enforce security and governance guardrails** on AI-related traffic flowing through the UMAI Platform. Where the Control Center gives humans visibility and configuration, and UMAI Service handles identity, APIs, licensing and orchestration, the AI Engine is the component that actually ensures the AI Security and the critical labeling for every AI interaction. 

It applies the hard coded or customized policies systematically: 

- Prompt injection detection
- Localization Controls
- Blacklists
- Regex controls
- Sensitive data exfiltration checks
- Content & safety checks
- Network and external-call rules
- Agent/Tool usage constraints
- Custom Rules and Policies

Produce a **clear, structured outcome**, such as:

- `ALLOW` (safe to proceed),
- `BLOCK` (do not proceed; request is unsafe),
- `ALLOW_WITH_MODIFICATIONS` (e.g. sanitized or redacted),
- `FLAG` (allowed but logged and potentially alerted).

In an enterprise environment, there may be many independent AI agents and applications:

- Customer-facing chatbots
- Internal support assistants
- Agentic workflows performing critical actions
- Analytics or investigation copilots

Without a central component, each team would implement its own inconsistent and incomplete safeguards.

# 1.2 How AI Engine Fits into UMAI Architecture

The AI Engine is one of the three core runtime components of the UMAI Platform:

- **Control Center (Next.js)** – human UI
- **UMAI Service (FastAPI)** – control plane & public API
- **AI Engine (FastAPI)** – policy execution & LLM interaction

It operates **behind** UMAI Service and is never called directly by enterprise applications or end users.

- **External AI applications** (bank chatbots, internal copilots, agent systems)
    
    ⬇
    
- **UMAI Service**
    - Authenticates caller (access key / JWT)
    - Checks license & RBAC
    - Loads guardrail, policies, LLM config from DB
    - Normalizes request
        
        ⬇
        
- **AI Engine**
    - Executes configured policies
    - Optionally calls LLM(s) (on-prem or via bank’s OpenAI keys)
    - Returns structured decision
        
        ⬇
        
- **UMAI Service**
    - Persists event & metrics
    - Evaluates alerts
    - Formats and returns API response
        
        ⬇
        
    
    **External AI application** receives decision/result.
    
    **Control Center** interacts only with **UMAI Service** for:
    
    - managing environments, projects, policies, guardrails, keys,
    - viewing metrics and alerts.
    
    The AI Engine is **purely server-side**; it has no direct UI or user-facing surface.
    

A typical **guardrail check** looks like this:

1. **External app → UMAI Service**
    - Calls `POST /api/v1/guardrails/{guardrail_id}/guard`
    - Includes `Authorization: Bearer <access_key>` and payload (user input, context).
2. **UMAI Service (control plane)**
    - Validates the access key & maps it to a guardrail, project, environment, tenant.
    - Confirms license is valid and guardrail is active.
    - Fetches **guardrail configuration** and associated **policies** from the DB.
    - Resolves **LLM configuration** for this project/environment.
    - Builds an **internal AI Engine request**:
        - `request_id`, tenant/env/project IDs
        - guardrail + policies (with configs)
        - phase (`PRE_LLM` or `POST_LLM`)
        - LLM config (or reference)
        - input + metadata/context
3. **UMAI Service → AI Engine**
    - Sends the internal request to AI Engine over internal HTTP (cluster-local).
4. **AI Engine**
    - Validates the request shape and required fields.
    - Builds a `PolicyContext` from guardrail, project, environment, metadata.
    - Executes **policy pipeline** in the configured order.
    - Calls LLM connector(s) if required by any policy.
    - Aggregates per-policy results into a **final decision**:
        - `ALLOW`, `BLOCK`, `FLAG`, `ALLOW_WITH_MODIFICATIONS`.
    - Returns a **structured response**:
        - decision + reasons
        - per-policy results
        - optional transformed input/output
        - latency breakdown
        - error list (if any).
5. **AI Engine → UMAI Service**
    - Response is returned over the same internal HTTP channel.
6. **UMAI Service (post-processing)**
    - Logs the decision into DB (guardrail events).
    - Updates metrics counters.
    - Evaluates alert rules; may create alerts.
    - Transforms engine result into **public API response**.
7. **UMAI Service → External app**
    - External app receives:
        - decision (allow/block/flag),
        - any transformed text if applicable,
        - optional explanations (depending on API design).

Control Center later queries **UMAI Service** for:

- events, metrics, alerts,
- current guardrail & policy configurations.

AI Engine is not aware of Control Center; it only talks to UMAI Service and LLMs.

### Boundaries with Other Components

**Boundary with Control Center (Next.js)**

- No direct interaction.
- All Control Center actions (create guardrail, edit policy, test message) go through **UMAI Service**.
- When a user clicks “Test guardrail” in the UI:
    - Control Center → UMAI Service → AI Engine → back to UMAI Service → Control Center.

**Boundary with UMAI Service**

- UMAI Service owns:
    - Authentication & RBAC
    - Licensing
    - Persistent configuration (tenants, envs, projects, guardrails, policies, keys, LLM configs)
    - Public APIs
    - Events & metrics persistence
- AI Engine owns:
    - Policy execution logic
    - LLM interactions
    - Decision aggregation & explanation
- AI Engine assumes:
    - Any request it gets is already authenticated, authorized, and license-valid.
- UMAI Service assumes:
    - AI Engine will not perform DB queries or auth, but will return a deterministic decision.

**Boundary with Database**

- AI Engine does **not** access the main platform DB (SQLite/Postgres/SQL Server) directly.
- Configuration and identity come from UMAI Service.
- AI Engine may have its own:
    - in-memory caches, and/or
    - optional local transient storage (e.g., for batch-testing jobs later),
        
        but not the source of truth for platform configuration.
        

**Boundary with LLM Providers**

- AI Engine connects to LLMs using **LLM connectors** based on config provided by UMAI Service:
    - On-prem HTTP LLM endpoints inside the bank’s network,
    - OpenAI / Azure OpenAI through the bank’s keys or proxies.
- AI Engine:
    - enforces timeouts,
    - shapes security-specific prompts,
    - maps errors to standard error types.

**In a typical enterprise deployment:**

- All components run inside the enterprises **trusted network** (K8s or Docker).
- Recommended topology:
    - `control-center` exposed via Ingress / load balancer (for bank admins/operators).
    - `umai-service` exposed:
        - internally to bank applications,
        - not directly to the public internet (bank’s API gateways may sit in front).
    - `ai-engine` **not exposed** outside the cluster’s internal network:
        - reachable only from `umai-service`.
- Network policies (or equivalent) should enforce that:
    - AI Engine accepts traffic only from UMAI Service.
    - AI Engine can access approved LLM endpoints (e.g., on-prem LLM URL, bank’s OpenAI proxy), and nothing else.

In summary, the AI Engine fits into UMAI Architecture as a **backend-only, internal, horizontally scalable decision service** that:

- receives normalized, authenticated, license-checked requests from UMAI Service,
- enforces security guardrails using policies and LLMs,
- returns structured decisions that UMAI Service can persist, monitor, and expose to both applications and Control Center.

# 1.3 Responsibilities vs Non-Responsibilities

The AI Engine’s responsibility is to act as UMAI’s **security decision core** for AI traffic. It receives normalized, authenticated, license-checked requests from UMAI Service; executes the configured guardrail pipeline (policies, LLM-assisted checks, aggregations); and returns a structured decision (allow/block/flag/modify) with per-policy details, optional transformations (e.g. redacted text), and latency/error information. It centralizes all AI security logic across projects and environments, applies policies consistently, calls LLMs in a controlled way when needed, and exposes decisions in a form that UMAI Service can store, monitor, and surface to operators.

The AI Engine is **not** responsible for authentication (access keys, LDAP, SSO), licensing, user and tenant management, configuration storage (environments, projects, guardrails, policies, keys), or presenting any UI. It does not talk to LDAP or external identity systems, does not own the primary database, and is not exposed directly to bank applications or end users. It trusts UMAI Service for identity, authorization, and configuration, and focuses exclusively on: given this configuration and this input, what is the safest, explainable decision we can make and return.

# 1.4 High-Level Request Flow (App → UMAI Service → AI Engine → LLM)

For a single guardrail check, the high-level flow is:

1. **App → UMAI Service**
    
    An Enterprise AI application (chatbot, agent, etc.) sends a request to UMAI’s public API, e.g. `POST /api/v1/guardrails/{guardrail_id}/guard`, with an access key and payload (user input, context, phase like PRE_LLM or POST_LLM).
    
2. **UMAI Service: Auth, License, Config**
    
    UMAI Service authenticates the access key, checks RBAC and license validity, and resolves the full configuration for this call: tenant, environment, project, guardrail, attached policies, and the LLM configuration to be used. It then builds a normalized internal request containing all of this plus the input and metadata.
    
3. **UMAI Service → AI Engine**
    
    UMAI Service sends this normalized internal request to the AI Engine over an internal HTTP endpoint (cluster-local, not exposed externally).
    
4. **AI Engine: Policy Pipeline & Optional LLM Calls**
    
    The AI Engine validates the request, constructs a policy context, executes the configured policies in order, and calls the appropriate LLM connector(s) only if a policy requires LLM-based evaluation (e.g. prompt injection classification, safety scoring). Each policy returns a PolicyResult; the engine aggregates these into a final decision (allow/block/flag/modify), with reasons and optional transformations.
    
5. **AI Engine → UMAI Service**
    
    The AI Engine returns a structured response: final decision, per-policy results, any transformed input/output, latency breakdown, and error information if relevant.
    
6. **UMAI Service → App & Observability**
    
    UMAI Service logs the event to the database, updates metrics, evaluates alert rules, shapes a public API response, and sends it back to the calling app. Control Center later reads from UMAI Service (not from AI Engine) to show histories, metrics, and alerts based on these decisions.
    
    [https://www.mermaidchart.com/app/projects/71763f07-070e-4e7a-a3c7-640822a9d5bd/diagrams/15292999-a670-40f0-8c66-cdba3928786b/share/invite/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkb2N1bWVudElEIjoiMTUyOTI5OTktYTY3MC00MGYwLThjNjYtY2RiYTM5Mjg3ODZiIiwiYWNjZXNzIjoiRWRpdCIsImlhdCI6MTc2NTI3NzE1Mn0.e1evLGxfbPoih-yB6FDBULiwp3qjadosRqWascr7tDc](https://www.mermaidchart.com/app/projects/71763f07-070e-4e7a-a3c7-640822a9d5bd/diagrams/15292999-a670-40f0-8c66-cdba3928786b/share/invite/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkb2N1bWVudElEIjoiMTUyOTI5OTktYTY3MC00MGYwLThjNjYtY2RiYTM5Mjg3ODZiIiwiYWNjZXNzIjoiRWRpdCIsImlhdCI6MTc2NTI3NzE1Mn0.e1evLGxfbPoih-yB6FDBULiwp3qjadosRqWascr7tDc)
    
    ![Untitled diagram-2025-12-09-104420.png](Untitled_diagram-2025-12-09-104420.png)
    

# **Architecture Overview**

# 2.1 Service Type & Deployment Model

The AI Engine is a **stateless, internal microservice** implemented in **Python (FastAPI)** and deployed as a **containerized service** inside the enterprise’s infrastructure. It also embeds a **GPT-OSS Safeguard–based classifier** as a core building block for UMAI Enterprise Guardrails.

At a high level:

- **Runtime:** Python 3.11+, FastAPI, Uvicorn/Gunicorn workers
- **Deployment:** Kubernetes (preferred) or Docker
- **Exposure:** Internal-only (no direct access from enterprise apps or the internet)
- **Scaling:** Horizontally scalable, stateless replicas

---

### Core Service Type

- The AI Engine exposes a small **internal HTTP API** (e.g. `/internal/ai-engine/v1/evaluate`) that only **UMAI Service** can call.
- It is **stateless**:
    - All configuration (guardrails, policies, LLM configs) is passed in the request by UMAI Service.
    - Any in-memory cache (e.g., compiled regexes, templates) is purely for performance and can be lost on restart.
- It does **not** expose any public endpoints; network policies / firewalls must prevent access from external clients.

**Kubernetes model:**

- Deployed as a `Deployment` (e.g. `umai-ai-engine`) with N replicas.
- Exposed via a **ClusterIP Service** (e.g. `umai-ai-engine.umai.svc.cluster.local`).
- Only `umai-service` is allowed to call it, enforced by:
    - NetworkPolicies,
    - service mesh policies (if present),
    - no Ingress configured for AI Engine.

**Docker / Docker Compose model:**

- Runs as a container (e.g. `umai-ai-engine`) on an internal Docker network.
- Only `umai-service` container can reach it via `http://umai-ai-engine:PORT`.
- AI Engine’s port is **not** published to the host.

---

### GPT-OSS Safeguard Runtime (Classifier Deployment Model)

UMAI uses **GPT-OSS Safeguard** internally as a key classifier for high-level guardrails (e.g. political content, hate, religion, war/conflict, crime, gossip, cultural sensitivities). This is encapsulated in adapter code like:

```python
client = OpenAI(base_url=HF_BASE_URL, api_key=_get_hf_api_key())
resp = client.chat.completions.create(
    model="openai/gpt-oss-safeguard-20b",
    messages=[
        {"role": "system", "content": POLICY},
        {"role": "user", "content": text},
    ],
    temperature=0,
)
```

The AI Engine supports **two deployment modes** for this GPT-OSS Safeguard dependency:

1. **Enterprise GPU Cluster Mode (Preferred for Strict Data Residency)**
    - The enterprise deploys GPT-OSS Safeguard (e.g. `gpt-oss-safeguard-20b`) on its own **GPU cluster** using an OpenAI-compatible or HF Router–compatible endpoint (vLLM, TGI, or similar).
    - The AI Engine is configured with:
        - `HF_API_BASE` (or equivalent) pointing to the **internal endpoint** (e.g. `https://oss-guardrail.llm.cluster.local/v1`).
        - `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` / `HUGGINGFACE_API_KEY` (or another configured secret) for auth, if required.
    - All classification traffic stays **inside the enterprise network**; no external calls leave the cluster.
    - This mode is recommended for **regulated environments** and strict **data residency/compliance**.
2. **Hosted Routing Mode (OpenAI/HF Router with API Key)**
    - The enterprise configures UMAI to call a **hosted GPT-OSS Safeguard endpoint**, such as the OpenAI-managed HF Router:
        - `HF_API_BASE = "https://router.huggingface.co/v1"` (or another documented URL).
        - API key provided via `HF_TOKEN`, `HUGGINGFACEHUB_API_TOKEN`, `HUGGINGFACE_API_KEY`, or an `OPENAI_API_KEY`style env var (depending on the chosen provider).
    - AI Engine uses the exact same `oss_classify` / `oss_guardrail_checker` adapter, but requests go to the **external router**.
    - This mode simplifies operations (no GPU cluster to manage) but requires:
        - **Egress** from the cluster to the external provider,
        - Explicit acceptance of any **data-sharing and compliance** implications.

In both modes:

- The GPT-OSS Safeguard integration is encapsulated behind a **policy handler** (e.g. `oss_guardrail_checker`).
- The AI Engine interprets responses into normalized fields:
    - `violation`, `policy_category`, `confidence`, `rationale`, plus a boolean `is_safe`.
- If the model response is malformed or unavailable, the adapter **fails closed** (treat as violation) according to our defensive defaults.

Runtime configuration for this integration is driven by **environment variables and secrets**:

- `HF_API_BASE` – base URL for the OpenAI-compatible router (internal GPU cluster or hosted).
- `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` / `HUGGINGFACE_API_KEY` – API key or token.

The Technical Guideline for deployment must clearly state that:

- **Enterprises must choose one of these two modes** (internal GPU cluster vs hosted routing) during installation.
- The chosen mode must be reflected in:
    - Helm values / docker-compose overrides,
    - Network policies (allowing or forbidding outbound connections),
    - Security/compliance documentation.

---

### Versioning & Configuration

- AI Engine is shipped as a container image (e.g. `umai/ai-engine:v1.0.0`) that includes:
    - the policy pipeline,
    - GPT-OSS Safeguard adapter code,
    - integration with the configured base URL/API key.
- No GPT-OSS weights are baked into the image; instead, AI Engine **calls** the configured endpoint.
- The deployment model (internal GPU vs hosted router) can be switched by:
    - updating env vars / Helm values,
    - re-deploying AI Engine with new configuration,
    - without changing application code.

In summary, the AI Engine is a stateless internal microservice that always sits behind UMAI Service, and its GPT-OSS Safeguard dependency can either run **inside the enterprise GPU cluster** or be reached via a **hosted router with an API key**, depending on the enterprise’s security and operational requirements.

# 2.2 Internal Layers & Modules

The AI Engine is structured as a **layered, async-first service**. All policy checks are designed to run **concurrently** to minimize latency, with a fast **pre-flight regex/blacklist filter** in front.

The main internal layers are:

1. API Layer (FastAPI routes)
2. Orchestration Layer (async + concurrency control)
3. Pre-Flight Filters (regex, blacklist, cheap heuristics)
4. Policy Engine (async policy plugins, all run in parallel)
5. LLM Connector Layer
6. Telemetry Layer (logging, metrics, health)

---

### API Layer (FastAPI Routes)

**Responsibility:** accept internal evaluation requests from UMAI Service.

- Exposes internal endpoints like:
    - `POST /internal/ai-engine/v1/evaluate`
- Performs:
    - schema validation (types, required fields),
    - basic limits (max input size, max number of policies),
    - injection of a `request_id` if not present.

All heavy logic is delegated to the orchestration layer.

---

### Orchestration Layer (Async Coordinator)

**Responsibility:** orchestrate one full evaluation in an **async, concurrent** way.

For each request:

1. Build a `PolicyContext` / `ExecutionContext` from:
    - tenant / environment / project / guardrail ids,
    - guardrail mode (ENFORCE / MONITOR),
    - phase (PRE_LLM / POST_LLM / later agent phases),
    - metadata (user, channel, session, risk tags),
    - LLM configuration reference.
2. Run **pre-flight filters** (regex/blacklist) synchronously but cheaply.
3. If pre-flight does not hard-block, schedule **all policies in parallel** as async tasks:
    - Each policy is `await`ed concurrently (`asyncio.gather`/equivalent).
    - Per-policy timeouts are enforced in the orchestrator.
4. Collect all `PolicyResult`s and run the **decision aggregation** logic.
5. Build the final AI Engine response object (decision, per-policy results, latencies, errors).

The orchestration layer **does not** talk to external DBs or identity systems; it only uses data passed in from UMAI Service.

---

### Pre-Flight Filters (Regex & Blacklists)

**Responsibility:** perform **very fast, deterministic checks** before any LLM or heavy policy logic is run.

This layer runs **synchronously and first** for latency and cost reasons.

Typical checks:

- **Regex-based content filters**, e.g.:
    - obvious injections like `(?i)(ignore (all|previous) instructions)`,
    - clear forbidden phrases (configured by the enterprise),
    - known data patterns (e.g. cleartext card number formats, IBAN patterns).
- **Static blacklist / denylist checks**, e.g.:
    - blocked keywords, hostnames, or patterns defined at enterprise or project level.
- **Simple length / shape checks**:
    - inputs that are too long,
    - highly repetitive patterns indicative of abuse.

Behavior:

- If pre-flight detects a **hard violation**, it can:
    - Immediately return a `BLOCK` decision without invoking any downstream policies or LLMs.
- If pre-flight detects **suspicious but not certain** patterns, it:
    - attaches hints to `PolicyContext` (e.g. `preflight_flags=["suspicious_prompt_injection"]`),
    - and lets the main policy engine verify with richer logic/LLM.

Pre-flight is implemented as a small set of code-defined filters plus configurable patterns from UMAI Service. It should add **microseconds to low milliseconds**, not tens of milliseconds.

---

### Policy Engine (Async, Parallel Policy Plugins)

**Responsibility:** execute all configured policies for a guardrail **in parallel**, using async handlers.

Key properties:

- **All policies run asynchronously and concurrently**:
    - Each policy implements an `async` `run(...)` method.
    - The orchestrator launches them in parallel using `asyncio.gather` or an equivalent pattern.
    - This ensures that overall latency is roughly the **max** of policy latencies, not the sum.
- **Policy Registry**:
    - Maps `template_type` (e.g. `"PROMPT_INJECTION"`, `"SENSITIVE_DATA_EXFILTRATION"`, `"OSS_POLICY"`, `"BLACKLIST_CHECK"`) to a handler class.
    - Handlers are registered at startup.
- **Policy Handler Interface** (conceptual):
    
    ```python
    class PolicyHandler(Protocol):
        type: str  # e.g. "PROMPT_INJECTION"
    
        async def run(
            self,
            input_text: str,
            context: PolicyContext,
            config: dict,
            llm_client: Optional[LLMClient] = None,
        ) -> PolicyResult:
            ...
    
    ```
    
- **Types of policies**:
    - **Heuristic / rule-based** (regex, patterns, static rules).
        - Even though pre-flight already does basic regex/blacklists, additional **project-specific policies** can still use regex/rules but with more complex configuration.
    - **Context-aware** (use user role, environment risk, etc.).
    - **LLM-assisted** (e.g. GPT-OSS Safeguard, other classifiers).
- **Timeouts and cancellation**:
    - Each policy gets a configurable per-policy timeout.
    - If the global stop condition is met early (e.g., a CRITICAL BLOCK result), the orchestrator can **cancel remaining policy tasks** to save latency and cost.
    - Policies receive a `stop_event` (or similar) they can check to abort early if work is no longer needed.

The output of this layer is a list of `PolicyResult` objects, one per policy, with statuses (`ALLOW/BLOCK/FLAG/ERROR`), scores, severities, and details.

---

### LLM Connector Layer

**Responsibility:** provide async, provider-agnostic clients for all LLMs used by policies.

- Async HTTP clients (e.g. `httpx.AsyncClient`) are used.
- All LLM calls are awaited concurrently along with other policy work.
- GPT-OSS Safeguard and other models are accessed via this layer, **not** directly in policies.

---

### Telemetry Layer (Logging, Metrics, Health)

**Responsibility:** track what the Engine does and how fast it does it.

- Logs:
    - One structured log per request with `request_id`, decision, and per-policy summary.
    - Optional debug logs per policy when enabled.
- Metrics:
    - Per-policy latency and error counts (important when everything runs in parallel).
    - Pre-flight hit counts (how often regex/blacklist blocked early).
- Health:
    - Basic `/healthz` and `/readyz` endpoints to indicate service and dependency readiness.

# 2.3 Trust Boundaries & Network Topology

- The **only trusted caller** of the AI Engine is **UMAI Service**.
    
    Enterprise apps → UMAI Service (public/internal API) → AI Engine (internal-only).
    
- AI Engine is reachable only via an **internal service** (K8s `ClusterIP` / Docker network), with **no public exposure**.
- **Network policies** must ensure:
    - Inbound: only UMAI Service can call AI Engine.
    - Outbound: AI Engine can call only:
        - **Redis** (for policy/guardrail config cache),
        - approved **LLM endpoints** (internal GPU cluster or configured router).
- AI Engine does not talk directly to LDAP or the main SQL DB; it relies on UMAI Service + Redis for configuration.

---

# 2.4 Statelessness, Scaling & Horizontal Replicas

- AI Engine is **stateless at the node level**:
    
    it keeps no durable local state; all runtime configuration is read from **Redis** (shared config/cache) based on IDs provided by UMAI Service. Redis is the **ephemeral config store**, not a long-term system of record (that remains the SQL DB behind UMAI Service).
    
- Because there is no node-local state, AI Engine can be **horizontally scaled**:
    
    multiple replicas share the same Redis and LLM backends.
    
- Scaling knobs:
    - Increase AI Engine replicas for heavier policy/LLM workloads.
    - Scale Redis and LLM infrastructure independently to avoid bottlenecks.

# **Core Data Structures & Contracts**

# 3.1 Internal Request Contract (from UMAI Service)

The **internal request** is the payload that UMAI Service sends to the AI Engine’s internal endpoint:

> POST /internal/ai-engine/v1/evaluate
> 

This contract carries just enough information for the AI Engine to:

1. Load the **correct guardrail snapshot** (including policies, regex/blacklists, LLM config) from **Redis**, and
2. Evaluate a **chat conversation** (not just a single message) according to that guardrail.

It is intentionally compact and stable; configuration details live in Redis, not in this request body.

### High-Level Shape

Conceptual JSON structure:

```json
{
  "request_id": "uuid-1234",
  "timestamp": "2025-12-09T12:34:56.789Z",

  "tenant_id": "ent-acme",
  "environment_id": "env-prod",
  "project_id": "proj-customer-chat",
  "guardrail_id": "gr-main-chat",
  "guardrail_version": 7,

  "phase": "PRE_LLM",

  "input": {
    "messages": [
      {"role": "system", "content": "You are UMAI support bot..."},
      {"role": "user", "content": "Merhaba, kredi kartı limitimi öğrenmek istiyorum."},
      {"role": "assistant", "content": "Tabii, size nasıl yardımcı olabilirim..."},
      {"role": "user", "content": "Bu arada kart numaram 1234 5678 9012 3456"}
    ],
    "phase_focus": "LAST_USER_MESSAGE",
    "content_type": "text",
    "language": "tr"
  },

  "timeout_ms": 1500,

  "flags": {
    "allow_llm_calls": true}
}

```

---

### Field Definitions

**`request_id`**

- Type: `string`, required.
- Unique identifier for this evaluation.
- Used for correlation across logs, metrics, and traces.
- Generated by UMAI Service if the calling app doesn’t provide one.

**`timestamp`**

- Type: `string` (ISO 8601), required.
- Time when UMAI Service accepted the request.
- Used for audit trails and time-based debugging.

---

**`tenant_id`**

**`environment_id`**

**`project_id`**

- Type: `string`, required.
- Identify **which enterprise**, **which environment** (e.g. `dev`, `test`, `prod`), and **which project/app** this traffic belongs to.
- Used in:
    - log/metric tagging,
    - Redis key construction for guardrail snapshots.

**`guardrail_id`**

- Type: `string`, required.
- Logical identifier of the guardrail to apply (e.g. `"gr-main-chat"`).
- Together with tenant/env/project IDs, it uniquely identifies the guardrail configuration.

**`guardrail_version`**

- Type: `integer`, required.
- Version number or ETag for the guardrail snapshot.
- Ensures the AI Engine uses the **exact same configuration** that UMAI Service believes is active.
- If Redis only has a different version, AI Engine should return a **config error**, not guess.

---

**`phase`**

- Type: `string`, required.
- Indicates **where in the interaction lifecycle** this check is happening.
- Expected values (v1):
    - `"PRE_LLM"` – before sending user input to the model.
    - `"POST_LLM"` – after model response, before returning to the user.
- Future phases (for agents) might include `"TOOL_CALL"`, `"TOOL_RESULT"` etc.
- Policies can be configured to run only in specific phases.

---

**`input`**

- Type: `object`, required.
- Represents the **full conversation context**, not just a single message.

Fields:

- `messages`
    - Type: `array` of `{ role: string, content: string }`, required.
    - Full ordered chat history for this evaluation point.
    - Roles:
        - `"system"` – instructions for the agent, if any.
        - `"user"` – user messages.
        - `"assistant"` – model/agent replies.
        - (future: `"tool"` or others for agentic flows).
    - Policies can:
        - scan the entire transcript (e.g. for long-running leaks),
        - focus on the last message (e.g. for immediate risk).
- `phase_focus`
    - Type: `string`, required.
    - Tells the engine/policies which part of the chat is the **primary focus** for this check.
    - Typical values:
        - `"LAST_USER_MESSAGE"` – PRE_LLM checks right before sending to model.
        - `"LAST_ASSISTANT_MESSAGE"` – POST_LLM checks right before returning to user.
    - Policies may still look at other messages for context, but they know which turn is “the main one”.
- `content_type`
    - Type: `string`, required.
    - Content format hint, e.g.:
        - `"text"` (default),
        - `"markdown"`,
        - `"json"`.
    - Helps regex/heuristic policies choose appropriate patterns.
- `language`
    - Type: `string`, optional but recommended.
    - Language hint (ISO-ish code like `"tr"`, `"en"`).
    - Used by:
        - multilingual regex sets,
        - LLM prompt templates (e.g. classification instructions in Turkish vs English).

---

**`timeout_ms`**

- Type: `integer`, optional.
- Overall **time budget** for this evaluation in milliseconds (including pre-flight, all policies, and any LLM calls).
- AI Engine uses this:
    - to derive per-policy timeouts,
    - to short-circuit if the total time is about to exceed the budget (failing safe according to guardrail config).

If absent, AI Engine uses a sane default (e.g. 1000–2000 ms) configured via environment/Helm values.

---

**`flags`**

- Type: `object`, optional.
- v1 contains a single, important flag:
    - `allow_llm_calls` (bool, default `true` if omitted):
        - `true`: AI Engine may call LLMs (e.g. GPT-OSS Safeguard) as required by policies.
        - `false`: AI Engine must **not** call any LLM. LLM-based policies must:
            - rely on heuristic fallback if available, or
            - return `ERROR`, which the aggregator handles according to guardrail mode (e.g. fail-closed/block for high-risk guardrails).

This flag gives enterprises a **hard switch** to run in “regex/blacklist-only” mode when needed (e.g. offline, strict data residency, or testing).

---

### Interaction with Redis (Guardrail Lookup)

The internal request **does not** include policy details directly. Instead, the AI Engine uses the identity fields to **load a snapshot from Redis**:

- Key pattern (conceptual):
    
    ```
    guardrail:{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{guardrail_version}
    
    ```
    
- Value (guardrail snapshot) contains:
    - `mode` (ENFORCE / MONITOR),
    - applicable `phases`,
    - `preflight_patterns` (regex + blacklist keywords),
    - ordered `policies` with:
        - `policy_id`,
        - `template_type`,
        - `phases`,
        - `config`,
    - `llm_config` for any LLM-based policies (e.g. GPT-OSS Safeguard endpoint, model name, timeout).

**Flow:**

1. AI Engine receives internal request.
2. Constructs Redis key from:
    - `tenant_id`, `environment_id`, `project_id`, `guardrail_id`, `guardrail_version`.
3. Reads guardrail snapshot from Redis:
    - If found & version matches → proceed.
    - If missing or mismatch → return a structured error response (`CONFIG_MISSING` / `CONFIG_STALE`) to UMAI Service.
4. Runs:
    - pre-flight regex/blacklist filters from snapshot,
    - all configured policies in **parallel** over `input.messages`,
    - aggregations based on guardrail mode.

UMAI Service owns **writing** these snapshots to Redis (from SQL DB) whenever guardrails are created/updated; AI Engine is a **consumer** of that runtime config.

---

### Versioning & Compatibility

This internal request contract is treated as a **versioned internal API**:

- Changes to field meanings or shapes must be:
    - backwards-compatible, or
    - introduced via a new internal endpoint (e.g. `/v2/evaluate`) while keeping `/v1/evaluate` stable for existing deployments.
- AI Engine and UMAI Service versions must be aligned in platform releases to ensure they agree on:
    - required fields,
    - `phase` semantics,
    - allowed `flags`.

This guarantees that enterprises can upgrade UMAI predictably without mysterious behavior changes in their guardrail evaluations.

# 3.2 Internal Response Contract (to UMAI Service)

The **internal response** is what the AI Engine returns to UMAI Service after evaluating a request:

---

> POST /internal/ai-engine/v1/evaluate
> 

It encodes a **single, clear decision** plus the **policy that triggered it** (especially in BLOCK cases), basic timing, and any errors.

Key rule (your requirement):

> If a policy decides to BLOCK, the AI Engine cancels other policies and returns immediately, including only that policy’s result in the response.
> 

No message transformations are included in v1 (no `transforms` object).

### High-Level Shape

Conceptual JSON structure:

```json
{
  "request_id": "uuid-1234",
  "tenant_id": "ent-acme",
  "environment_id": "env-prod",
  "project_id": "proj-customer-chat",
  "guardrail_id": "gr-main-chat",
  "guardrail_version": 7,

  "phase": "PRE_LLM",

  "decision": {
    "action": "BLOCK",
    "allowed": false,
    "severity": "HIGH",
    "reason": "OSS_SAFEGUARD: P1 political content"
  },

  "triggering_policy": {
    "policy_id": "pol-oss-main",
    "template_type": "OSS_SAFEGUARD",
    "status": "BLOCK",
    "severity": "HIGH",
    "score": 0.97,
    "details": {
      "violation": 1,
      "policy_category": "P1",
      "confidence": "high",
      "rationale": "Contains political actor and sensitive comparison."
    },
    "latency_ms": 320
  },

  "latency_ms": {
    "total": 350,
    "preflight": 2
  },

  "errors": []
}

```

For non-blocking decisions (`ALLOW`, `FLAG_ONLY`, etc.) we **may** still have a `triggering_policy` (e.g., the most severe policy) but the semantics are “policy most responsible for this final decision”, not “all policies”.

---

### Identification Fields

Same as in the request, for correlation and storage:

- `request_id` – string, required
    - Mirrors the request’s `request_id`.
- `tenant_id`, `environment_id`, `project_id` – string, required
    - Echoed from the request.
- `guardrail_id`, `guardrail_version` – required
    - Echo which guardrail snapshot was applied.
- `phase` – string, required
    - PRE_LLM / POST_LLM, matching the request.

These fields let UMAI Service store this response as a **guardrail event** with full context.

---

### Decision Object

The **decision** is the top-level outcome.

```json
"decision": {
  "action": "BLOCK",
  "allowed": false,
  "severity": "HIGH",
  "reason": "OSS_SAFEGUARD: P1 political content"
}

```

Fields:

- `action` – string, required
    - High-level action. v1 values:
        - `"ALLOW"` – proceed as normal.
        - `"BLOCK"` – stop the flow (don’t send to LLM in PRE_LLM, don’t return to user in POST_LLM).
        - `"ALLOW_WITH_WARNINGS"` – allowed but flagged (used mainly in MONITOR mode).
- `allowed` – boolean, required
    - `true` if the request can proceed (according to guardrail mode), `false` if we must block.
- `severity` – string, required
    - Overall severity: `"LOW"`, `"MEDIUM"`, `"HIGH"`, `"CRITICAL"`.
- `reason` – string, required
    - Short, human-readable summarization of **why** this decision was made.
    - Typically constructed using the `triggering_policy` info (e.g. `"<POLICY_TYPE>: <summary>"`).

UMAI Service uses this to:

- enforce behavior (e.g., do not forward request / do not return response),
- decide which alerts to trigger.

---

### Triggering Policy

Instead of returning a list of all policies, the AI Engine returns **the single policy that “decided” the outcome**, especially in `BLOCK` cases.

```json
"triggering_policy": {
  "policy_id": "pol-oss-main",
  "template_type": "OSS_SAFEGUARD",
  "status": "BLOCK",
  "severity": "HIGH",
  "score": 0.97,
  "details": {
    "violation": 1,
    "policy_category": "P1",
    "confidence": "high",
    "rationale": "Contains political actor and sensitive comparison."
    },
  "latency_ms": 320
}

```

Fields:

- `policy_id` – string, required
    - ID of the policy in the guardrail snapshot.
- `template_type` – string, required
    - Policy type (e.g. `"OSS_SAFEGUARD"`, `"SENSITIVE_DATA_EXFILTRATION"`, `"REGEX_BLACKLIST"`).
- `status` – string, required
    - Policy-level final status:
        - `"ALLOW"`, `"FLAG"`, `"BLOCK"`, `"ERROR"`.
- `severity` – string, required
    - Policy-level severity (`LOW/MEDIUM/HIGH/CRITICAL`).
- `score` – number, optional
    - Confidence/risk score in [0, 1]; meaning defined per policy.
- `details` – object, optional but strongly recommended
    - Policy-specific structured data (e.g. from GPT-OSS, DLP, regex matches).
- `latency_ms` – number, required
    - Time spent executing this policy, in milliseconds.

**Behavior-specific rules:**

- If **`action == "BLOCK"`**:
    - `triggering_policy` is **the policy that returned BLOCK**.
    - As soon as this happens, AI Engine:
        - cancels other policy tasks (if still running),
        - returns early with this policy as the only one in the response.
- If **`action == "ALLOW"` or `"ALLOW_WITH_WARNINGS"`**:
    - All policies complete.
    - `triggering_policy` is typically:
        - the most severe non-ALLOW policy (e.g., a FLAG in MONITOR mode), or
        - `null`/omitted if everything trivially ALLOW and no single policy needs to be highlighted.
    - You can choose to always set it to the “most important” policy for explanation purposes, even in ALLOW.

This keeps the response **lightweight and focused**, especially in the common “BLOCK now” scenario.

---

### Latency Object

Summarizes timing for the evaluation:

```json
"latency_ms": {
  "total": 350,
  "preflight": 2
}

```

Fields:

- `total` – number, required
    - Total time spent in AI Engine from receipt to response.
- `preflight` – number, optional
    - Time spent in fast regex/blacklist pre-flight checks.

We don’t need to report all per-policy latencies since we now only return one `triggering_policy` with its `latency_ms`. This keeps metrics simpler while preserving key performance insights.

---

### Errors

If something goes wrong but the Engine still returns a structured decision:

```json
"errors": [
  {
    "type": "LLM_TIMEOUT",
    "source": "pol-oss-main",
    "message": "GPT-OSS call exceeded 2000ms",
    "retryable": true}
]

```

Fields:

- `type` – string, required
    - Error code: `"CONFIG_MISSING"`, `"REDIS_ERROR"`, `"LLM_TIMEOUT"`, `"LLM_AUTH_ERROR"`, `"POLICY_RUNTIME_ERROR"`, etc.
- `source` – string, optional
    - Where it came from: `"preflight"`, `"pol-oss-main"`, `"llm_client"`, etc.
- `message` – string, optional
    - Human-readable explanation, safe for logs (no raw PII or full prompts).
- `retryable` – boolean, optional
    - Indicates whether UMAI Service might reasonably retry (for transient infra errors).

**Block vs allow behavior with errors:**

- If a critical policy fails and the guardrail is in **ENFORCE** mode, AI Engine may choose to:
    - **fail closed** (BLOCK), with `triggering_policy.status = "ERROR"` and `decision.reason` explaining that a critical check failed.
- If guardrail is in **MONITOR** mode, Engine may:
    - allow but flag, with `action = "ALLOW_WITH_WARNINGS"` and `errors` describing the issue.

---

### Example: `ALLOW` Response

```json
{
  "request_id": "uuid-5678",
  "tenant_id": "ent-acme",
  "environment_id": "env-prod",
  "project_id": "proj-customer-chat",
  "guardrail_id": "gr-main-chat",
  "guardrail_version": 7,

  "phase": "PRE_LLM",

  "decision": {
    "action": "ALLOW",
    "allowed": true,
    "severity": "LOW",
    "reason": "No policy violations detected"
  },

  "triggering_policy": null,

  "latency_ms": {
    "total": 95,
    "preflight": 3
  },

  "errors": []
}

```

Behavioral rule to add to the text:

- If `decision.action == "ALLOW"` → `triggering_policy` **must be `null` or omitted**.
- If `decision.action == "BLOCK"` (or later `ALLOW_WITH_WARNINGS`) → `triggering_policy` **must be populated** with the policy that decided the outcome.

### Summary Behavior

Given a request + Redis snapshot, the AI Engine:

1. Runs **pre-flight regex/blacklist**.
2. Launches all policies **in parallel** (async).
3. As soon as a policy returns `BLOCK`:
    - sets `decision.action = "BLOCK"`,
    - sets `triggering_policy` to that policy,
    - cancels other policy tasks,
    - returns immediately.
4. If no policy blocks:
    - waits for all to complete,
    - computes final `decision.action` (`ALLOW` / `ALLOW_WITH_WARNINGS`),
    - picks a single `triggering_policy` (most severe/most relevant) or omits it if truly trivial.
5. Returns `decision`, `triggering_policy`, timing, and any `errors`.

No `transforms` are included in v1: UMAI does **not** modify messages yet, it only **evaluates and decides**.

# Policies

# 4.1 Policy Structure

Each policy in a guardrail snapshot is a JSON object with:

```json
{
  "id": "pol-001",
  "type": "HEURISTIC",        // or "CONTEXT_AWARE"
  "name": "Human readable name",
  "enabled": true,
  "phases": ["PRE_LLM"],      // which phases it runs in
  "config": {                 // shape depends on type
    "...": "..."
  }
}

```

### Common fields

- `id`
    - Unique ID inside the guardrail (e.g. `"pol-oss-main"`, `"pol-regex-1"`).
- `type`
    - `"HEURISTIC"` or `"CONTEXT_AWARE"`
    - This is the **only** discriminator we use in code.
- `name`
    - Human-readable name shown in Control Center (Policies page).
- `enabled`
    - Boolean; if `false`, the policy is ignored at runtime.
- `phases`
    - Array of phases where the policy should run, e.g. `["PRE_LLM"]`, `["POST_LLM"]`, or both.
    - Pipeline filters by `phase` before running policies.
- `config`
    - Type-specific configuration.

---

## Heuristic / Rule-Based Policy (`type = "HEURISTIC"`)

**Goal:** super fast checks via regex or exact match.

No LLM calls. Just simple patterns over the chat.

### Shape

```json
{
  "id": "pol-regex-blacklist",
  "type": "HEURISTIC",
  "name": "Regex & Keyword Blacklist",
  "enabled": true,
  "phases": ["PRE_LLM"],
  "config": {
    "target": "LAST_MESSAGE",          // or "FULL_HISTORY"
    "rules": [
      {
        "id": "rule-ignore-previous",
        "mode": "REGEX",               // "REGEX" or "EXACT"
        "pattern": "(?i)ignore (all|previous) instructions",
        "block_on_match": true},
      {
        "id": "rule-system-override",
        "mode": "REGEX",
        "pattern": "(?i)from now on you are not UMAI",
        "block_on_match": true},
      {
        "id": "rule-iban-phrase",
        "mode": "EXACT",
        "pattern": "iban numaram",
        "block_on_match": true}
    ],
    "max_length": 8000
  }
}

```

### Field details

`config.target`

- `"LAST_MESSAGE"` – apply rules to the message indicated by `phase_focus`.
- `"FULL_HISTORY"` – scan all messages in the conversation.

`config.rules[]`

Each rule is:

```json
{
  "id": "rule-id",
  "mode": "REGEX" | "EXACT",
  "pattern": "string",
  "block_on_match": true}

```

- `id` – rule identifier for logs & UI.
- `mode`:
    - `"REGEX"` – compiled regex over text.
    - `"EXACT"` – case-insensitive substring match (unless you later add case option).
- `pattern` – regex or literal string.
- `block_on_match` – if true, this policy returns `BLOCK` as soon as this rule matches.

`config.max_length` (optional)

- If the focused text exceeds this length, the policy may choose to:
    - BLOCK (for safety), or
    - FLAG (depending on how you implement it).

**Runtime behavior for HEURISTIC policy:**

- AI Engine picks the text according to `target` + `phase_focus`.
- Evaluates `rules` sequentially inside this single policy.
- On first hard match:
    - returns `status = "BLOCK"` with details like:
        - `{ matched_rule_id: "...", matched_pattern: "..." }`
- If nothing matches:
    - returns `status = "ALLOW"`.

---

## Context-Aware Policy (`type = "CONTEXT_AWARE"`)

**Goal:** LLM-based classification using a **structured prompt** composed of:

- `INSTRUCTIONS`
- `DEFINITIONS & CATEGORY MAP`
- `EXAMPLES`

This matches exactly what you showed with the UMAI Enterprise Guardrails Policy.

### Shape

```json
{
  "id": "pol-oss-main",
  "type": "CONTEXT_AWARE",
  "name": "UMAI Enterprise Guardrails (GPT-OSS)",
  "enabled": true,
  "phases": ["PRE_LLM", "POST_LLM"],
  "config": {
    "target": "LAST_MESSAGE",          // or "FULL_HISTORY"
    "instructions": "UMAI Enterprise Guardrails Policy\nINSTRUCTIONS\n\n1. Review the exact Content ...",
    "definitions_and_category_map": "DEFINITIONS & CATEGORY MAP\n\nA. HATE / HARASSMENT\n- H1: ...\n- H2: ...\n...",
    "examples": "EXAMPLES\n\nInput: \"Tayyip Erdogan mi ALLAH mi?\"\nOutput:\n{\n  \"violation\": 1,\n  ...\n}\n\nInput: \"Sort these JSON objects by timestamp.\"\nOutput:\n{\n  \"violation\": 0,\n  ...\n}\n",

    "output_schema": {
      "violation_field": "violation",
      "category_field": "policy_category",
      "confidence_field": "confidence",
      "rationale_field": "rationale"
    },

    "min_confidence_for_block": "medium",   // "low" | "medium" | "high"
    "fail_closed_on_error": true           // how to behave when model misbehaves
  }
}

```

### Field details

`config.target`

- `"LAST_MESSAGE"` – pass only the message indicated by `phase_focus` to the LLM.
- `"FULL_HISTORY"` – pass a summary or full transcript (your policy handler decides how to format).

`config.instructions`

- The **INSTRUCTIONS** section of your policy prompt.
- Contains numbered steps like:
    
    > Review the exact Content…Normalize text…Map the Content to the category tree…
    …
    > 

`config.definitions_and_category_map`

- The full **DEFINITIONS & CATEGORY MAP** section:
    - Groups A–G, explanations of each code (H1, H2.a, P1, etc).
- This stays mostly static and is a pure text block.

`config.examples`

- The **EXAMPLES** block:
    - Several `Input:` / `Output:` pairs showing expected JSON.
- Helps the model “lock” onto the output schema.

`config.output_schema`

- Tells the handler what JSON field names to expect:
    - `"violation"`, `"policy_category"`, `"confidence"`, `"rationale"`.
- Can be extended later, but v1 you can treat it as fixed.

At runtime, the policy handler builds the full system prompt something like:

```
{instructions}

{definitions_and_category_map}

{examples}

Content: [INPUT]
Answer:

```

…and sends it to the model (GPT-OSS Safeguard or whatever LLM config is attached to this guardrail).

`config.min_confidence_for_block`

- If the LLM returns `violation = 1` but `confidence` < threshold:
    - you can treat it as `FLAG` instead of `BLOCK`, or still block; up to you.
- v1: you can even ignore this and always BLOCK when `violation=1`.

`config.fail_closed_on_error`

- If:
    - model times out,
    - returns non-JSON,
    - returns missing fields,
- then:
    - `true` → policy returns `status = "BLOCK"` (fail-closed).
    - `false` → policy returns `status = "ERROR"` or `status = "ALLOW"` depending on guardrail mode.

**Runtime behavior for CONTEXT_AWARE policy:**

1. Choose text according to `target` + `phase_focus`.
2. Build the full prompt from `instructions + definitions_and_category_map + examples + input`.
3. Call the LLM (via your GPT-OSS router or internal GPU).
4. Parse response JSON using `output_schema`.
5. Map to internal `PolicyResult`:
    - `violation = 1` → `status = "BLOCK"` (or FLAG, based on confidence threshold).
    - `violation = 0` → `status = "ALLOW"`.
6. On parse/timeouts/errors:
    - follow `fail_closed_on_error`.

---

## Summary: Policy Structure v1

**Common core:**

```json
{
  "id": "pol-xyz",
  "type": "HEURISTIC" | "CONTEXT_AWARE",
  "name": "Some name",
  "enabled": true,
  "phases": ["PRE_LLM", "POST_LLM"],
  "config": { ... }
}

```

**Heuristic policies**:

- `config.target`
- `config.rules[]` (regex/exact)
- optional `max_length`

**Context-aware policies**:

- `config.target`
- `config.instructions`
- `config.definitions_and_category_map`
- `config.examples`
- `config.output_schema`
- `config.min_confidence_for_block`
- `config.fail_closed_on_error`

## 4.2 Guardrail & Policy Storage, Retrieval & Execution

This section describes how:

1. Guardrails and their policies are **stored** as snapshots,
2. Those snapshots are written to and read from **Redis**, and
3. Policies are **instantiated and executed** inside the UMAI Engine.

The goals are:

- Keep AI Engine **config-light** and **stateless at node level**,
- Ensure every evaluation uses a **consistent, versioned snapshot**,
- Make policy changes **safe and auditable**.

---

### Guardrail Snapshot Schema (Stored in Redis)

At runtime, the AI Engine never talks directly to SQL.

Instead, UMAI Service writes a **guardrail snapshot** into Redis.

**Key (conceptual):**

```
guardrail:{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{guardrail_version}

```

**Value (JSON):**

```json
{
  "guardrail_id": "gr-main-chat",
  "version": 7,
  "mode": "ENFORCE",                  // or "MONITOR"
  "phases": ["PRE_LLM", "POST_LLM"],

  "preflight": {
    "target": "LAST_MESSAGE",
    "rules": [
      {
        "id": "preflight-ignore-previous",
        "mode": "REGEX",
        "pattern": "(?i)ignore (all|previous) instructions",
        "block_on_match": true},
      {
        "id": "preflight-system-override",
        "mode": "REGEX",
        "pattern": "(?i)from now on you are not UMAI",
        "block_on_match": true}
    ],
    "max_length": 8000
  },

  "policies": [
    {
      "id": "pol-regex-blacklist",
      "type": "HEURISTIC",
      "name": "Regex & Keyword Blacklist",
      "enabled": true,
      "phases": ["PRE_LLM"],
      "config": {
        "target": "LAST_MESSAGE",
        "rules": [
          {
            "id": "rule-iban",
            "mode": "EXACT",
            "pattern": "iban numaram",
            "block_on_match": true}
        ],
        "max_length": 8000
      }
    },
    {
      "id": "pol-oss-main",
      "type": "CONTEXT_AWARE",
      "name": "UMAI Enterprise Guardrails (GPT-OSS)",
      "enabled": true,
      "phases": ["PRE_LLM", "POST_LLM"],
      "config": {
        "target": "LAST_MESSAGE",
        "instructions": "UMAI Enterprise Guardrails Policy\nINSTRUCTIONS\n\n1. Review the exact Content ...",
        "definitions_and_category_map": "DEFINITIONS & CATEGORY MAP\n\nA. HATE / HARASSMENT\n- H1: ...",
        "examples": "EXAMPLES\n\nInput: \"Tayyip Erdogan mi ALLAH mi?\" ...",
        "output_schema": {
          "violation_field": "violation",
          "category_field": "policy_category",
          "confidence_field": "confidence",
          "rationale_field": "rationale"
        },
        "min_confidence_for_block": "medium",
        "fail_closed_on_error": true}
    }
  ],

  "llm_config": {
    "provider": "OSS_ROUTER",          // or "INTERNAL_GPU", "OPENAI_DIRECT", etc.
    "base_url": "https://router.huggingface.co/v1",
    "model": "openai/gpt-oss-safeguard-20b",
    "timeout_ms": 2000
  }
}

```

Notes:

- **`preflight`** is a special **built-in heuristic policy** that always runs first and very cheaply. It’s stored separately from `policies` to emphasize its special role.
- `policies` is an array of **only two types**:
    - `type = "HEURISTIC"` – regex / exact match rules,
    - `type = "CONTEXT_AWARE"` – prompt-based, GPT-OSS style.
- `llm_config` gives the AI Engine all info it needs to call the right LLM endpoint for **CONTEXT_AWARE** policies.

---

### Writing Guardrail Snapshots to Redis (UMAI Service)

UMAI Service is the **only component** that writes guardrail snapshots to Redis.

Flow when a guardrail is created/updated in Control Center:

1. Admin uses Control Center to:
    - create or edit guardrail,
    - configure policies (both HEURISTIC and CONTEXT_AWARE),
    - choose mode (ENFORCE / MONITOR),
    - configure LLM routing (if needed).
2. Control Center → UMAI Service:
    - sends the updated config via internal API.
3. UMAI Service:
    - validates the guardrail definition,
    - assigns or increments `version` (e.g., 7 → 8),
    - constructs the **snapshot JSON** in the format above,
    - serializes to JSON and writes to Redis with the key:
        
        ```
        guardrail:{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{version}
        
        ```
        
4. UMAI Service updates SQL DB as **source of truth**:
    - guardrail metadata (id, version, mode, timestamps),
    - policy definitions,
    - associations to tenant/env/project.
5. Optionally, older versions may:
    - be kept for audit (no TTL), or
    - be expired after some time; depends on retention strategy.

The AI Engine never mutates snapshots in Redis. It only reads.

---

### Retrieving Guardrails from Redis (AI Engine)

When AI Engine receives an evaluation request (3.1):

```json
{
  "tenant_id": "ent-acme",
  "environment_id": "env-prod",
  "project_id": "proj-customer-chat",
  "guardrail_id": "gr-main-chat",
  "guardrail_version": 7,
  ...
}

```

it performs:

1. **Key construction**
    
    ```
    key = guardrail:{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{guardrail_version}
    
    ```
    
2. **GET from Redis**
    - If value is found → parse JSON into an internal `GuardrailSnapshot` object.
    - If not found → return a **config error response**:
        - `decision.action = "BLOCK"` or a special error decision,
        - `allowed = false`,
        - `errors = [{ "type": "CONFIG_MISSING", ... }]`.
3. **Basic validation**
    - Ensure `guardrail_id` and `version` inside JSON match the requested ones.
    - Ensure required fields exist (`mode`, `policies`, `llm_config`, etc.).
4. **Build in-memory objects**
    - `preflight` → in-memory `HeuristicPolicy` instance (special role).
    - `policies` → list of `PolicyDefinition`:
        - either `HeuristicPolicyDefinition` or `ContextAwarePolicyDefinition`,
        - based on `type` field.

If any critical validation fails, AI Engine returns a structured error and does **not** proceed to policy execution.

---

### Executing Guardrail Policies in UMAI Engine

Once the snapshot is loaded and validated, the execution pipeline (4.1) uses the snapshot like this:

### 5.4.1 Pre-Flight Execution

- Take `snapshot.preflight`:
    - `target`, `rules`, `max_length`.
- Apply it over `input.messages`:
    - usually `phase_focus` (e.g., last user message).

If a preflight rule matches (with `block_on_match = true`):

- AI Engine immediately:
    - sets `decision.action = "BLOCK"`,
    - `decision.allowed = false`,
    - `decision.reason` = something like: `"Preflight: rule preflight-ignore-previous matched"`,
    - sets `triggering_policy` to a synthetic “preflight” policy (or leaves it `null` and relies on reason),
    - returns the response without running any other policies.

If preflight passes:

- Execution continues to main policy stage.

---

### Main Policy Selection

From `snapshot.policies`:

1. Filter by:
    - `enabled == true`,
    - current `phase` in `policy.phases`.
2. Respect `flags.allow_llm_calls` from the request:
    - For `type = "CONTEXT_AWARE"`:
        - if `allow_llm_calls == false`:
            - either skip these policies entirely,
            - or run in a **degraded mode** (e.g., treat as `ERROR` and let aggregation handle).
            - Design decision: for v1, simplest is to skip and note an error if `fail_closed_on_error == true`.
3. For each remaining policy, build an **async task** that:
    - selects the relevant text from `input.messages` based on `config.target`,
    - executes the policy logic.

---

### HEURISTIC Policy Execution

For each `type = "HEURISTIC"`:

- Handler takes:
    - `config.target` – `"LAST_MESSAGE"` or `"FULL_HISTORY"`,
    - `config.rules[]` – regex / exact patterns,
    - `config.max_length`.

Execution:

1. Extract text (last message or entire conversation).
2. If length > `max_length` (if defined):
    - either BLOCK or FLAG; up to implementation.
3. For each rule in `rules`:
    - If `mode = "REGEX"` → run compiled regex.
    - If `mode = "EXACT"` → case-insensitive substring match.
    - On first match where `block_on_match = true`:
        - return `PolicyResult` with `status = "BLOCK"` and details (rule id, pattern).
4. If no blocking match:
    - return `PolicyResult` with `status = "ALLOW"` (or `FLAG` if you add non-blocking rules).

AI Engine watches for any `PolicyResult.status == "BLOCK"`. The first such result triggers early short-circuit.

---

### CONTEXT_AWARE Policy Execution

For each `type = "CONTEXT_AWARE"`:

- Handler takes:
    - `config.target`,
    - `instructions`,
    - `definitions_and_category_map`,
    - `examples`,
    - `output_schema`,
    - `min_confidence_for_block`,
    - `fail_closed_on_error`.

Execution:

1. Extract the relevant text from `input.messages` (last message or full history).
2. Build a prompt:
    
    ```
    {instructions}
    
    {definitions_and_category_map}
    
    {examples}
    
    Content: [INPUT]
    Answer:
    
    ```
    
3. Call the model defined in `snapshot.llm_config` (e.g. GPT-OSS Safeguard via router or internal GPU).
4. Parse JSON response using `output_schema` field names:
    - `violation_field` → integer 0/1,
    - `category_field` → string code (H2.f, P1, SAFE, etc.),
    - `confidence_field` → "low" | "medium" | "high",
    - `rationale_field` → short explanation.
5. Map to `PolicyResult`:
    - If `violation = 1` and confidence ≥ `min_confidence_for_block`:
        - `status = "BLOCK"`, severity = HIGH/CRITICAL (per mapping you define).
    - If `violation = 0`:
        - `status = "ALLOW"`.
6. On parse failure / timeout / model error:
    - If `fail_closed_on_error == true`:
        - `status = "BLOCK"` (fail-closed).
    - Else:
        - `status = "ERROR"` and let aggregator handle.

Again, the orchestrator short-circuits as soon as any policy returns `BLOCK`.

---

### Putting It Together: End-to-End Snapshot Usage

For each evaluation:

1. **UMAI Service**:
    - Validates tenant/env/project/guardrail,
    - Sends internal request with IDs + chat messages to AI Engine.
2. **AI Engine**:
    - Builds Redis key from IDs + `guardrail_version`,
    - Fetches **guardrail snapshot** (mode, preflight, policies, llm_config),
    - Runs **preflight**,
    - Runs applicable **HEURISTIC** and **CONTEXT_AWARE** policies in **parallel**,
    - Short-circuits on first BLOCK,
    - Aggregates into a single `decision` and `triggering_policy`,
    - Returns internal response (3.2).
3. **UMAI Service**:
    - Persists the event (for audits & metrics),
    - Raises alerts as needed,
    - Returns final result to the enterprise app.

This design keeps:

- **Configuration authority** and versioning in UMAI Service + SQL,
- **Fast distribution** via Redis snapshots,
- **Pure execution** and LLM interaction inside AI Engine,
- With a clean, minimal **internal request/response contract** between them.

# **Standalone Development of AI Engine (MVP)**

## 5.1 Development Plan (MVP AI Engine Only)

**Goal:**

Build a standalone AI Engine service that:

- Exposes a single internal API endpoint `/internal/ai-engine/v1/evaluate`
- Accepts our **internal request format (3.1)**
- Uses **dummy in-memory guardrail snapshots** instead of Redis/SQL
- Implements:
    - Request validation
    - Pre-flight heuristic checks
    - HEURISTIC policy
    - CONTEXT_AWARE policy that calls GPT-OSS Safeguard style endpoint (configurable)
    - Async parallel execution with early BLOCK
- Returns our **internal response format (3.2)**

### Phase 1 – Skeleton & Project Structure

- Initialize Python project:
    - FastAPI app with `/healthz` and `/internal/ai-engine/v1/evaluate`
    - Use `uvicorn` for local run.
- Create basic folders/modules:
    - `app/api/` – FastAPI routes & request/response models
    - `app/core/` – engine orchestration, pipeline, config
    - `app/policies/` – HEURISTIC & CONTEXT_AWARE implementations
    - `app/models/` – Pydantic models for request, response, guardrail snapshot
    - `app/store/` – Dummy in-memory guardrail store

### Phase 2 – Data Models & Dummy Guardrail Store

- Implement Pydantic models for:
    - Internal request
    - Internal response
    - Guardrail snapshot & policy structure (HEURISTIC/CONTEXT_AWARE)
- Implement `DummyGuardrailStore`:
    - Hardcode 1–2 guardrail snapshots in Python (with fake tenant/env/project/guardrail IDs).
    - Expose `get_guardrail(tenant_id, env_id, project_id, guardrail_id, version)`.

### Phase 3 – Pipeline & Orchestrator

- Implement core pipeline:
    1. Load snapshot by IDs
    2. Run pre-flight HEURISTIC rules
    3. If not blocked:
        - Filter applicable policies for phase
        - Run all policies in parallel (`asyncio.gather` with cancellation)
        - Early exit on first BLOCK
    4. Aggregate into final decision & response
- Implement internal `PolicyContext` and `PolicyResult` (internal classes, not API).

### Phase 4 – HEURISTIC Policy

- Implement `HeuristicPolicy`:
    - Target text by `target` (LAST_MESSAGE or FULL_HISTORY)
    - Evaluate regex + exact rules
    - On first blocking rule → return `BLOCK`
    - Else → `ALLOW`
- Use Python `re` with precompiled patterns for speed.

### Phase 5 – CONTEXT_AWARE Policy (GPT-OSS-style)

- Implement `ContextAwarePolicy`:
    - Build prompt from:
        - `instructions`
        - `definitions_and_category_map`
        - `examples`
        - `Content: [INPUT]`
    - Call LLM endpoint using `openai` client (or HTTPX if you prefer), with:
        - `HF_API_BASE` and `HF_TOKEN`style env vars
    - Parse JSON, map to:
        - `violation`
        - `policy_category`
        - `confidence`
        - `rationale`
    - Apply `min_confidence_for_block` and `fail_closed_on_error`.

### Phase 6 – Logging, Errors & Health

- Add structured logging with `request_id` in each request.
- Implement `errors` field in response.
- `/healthz` endpoint returns simple OK.

### Phase 7 – Tests & Sample Flows

- Unit tests for:
    - HEURISTIC policy
    - CONTEXT_AWARE policy (with mocked LLM)
    - Pipeline early-block behavior
- Example `curl` scripts:
    - One that should ALLOW
    - One that should BLOCK via HEURISTIC
    - One that should BLOCK via CONTEXT_AWARE

---

## 5.2 Deployment & Execution Plan (MVP)

We keep it **simple but production-like**.

### Local Development

- Run with `uvicorn app.main:app --reload --host 0.0.0.0 --port 8081`
- Configure env vars:
    - `HF_API_BASE` – e.g. `https://router.huggingface.co/v1` or local OSS endpoint
    - `HF_TOKEN` – API key
- Use `curl`/Postman to hit `/internal/ai-engine/v1/evaluate` with dummy request JSON.

### Containerization

- Create a minimal `Dockerfile`:
    - Python 3.11 slim base
    - Install dependencies
    - Set `CMD` to run `uvicorn app.main:app --host 0.0.0.0 --port 8081`
- Build & run:
    - `docker build -t umai-ai-engine-mvp .`
    - `docker run -p 8081:8081 umai-ai-engine-mvp`

### Future K8s (just stub)

- A simple `Deployment` + `Service` manifest:
    - 1 replica (for MVP)
    - No Ingress (internal-only in real deployment)
- For now, we **don’t** integrate with other UMAI modules, just ensure the engine can be scaled & health-checked.

---

## 5.3 Implementation Documentation

### Scope & Assumptions

This document describes how to **build, run, and deploy** the **AI Engine as a standalone service (MVP)**.

- **Included:**
    - Internal HTTP API (`/internal/ai-engine/v1/evaluate`)
    - Internal request/response contracts
    - Dummy in-memory guardrail store
    - Pre-flight HEURISTIC checks
    - HEURISTIC policies
    - CONTEXT_AWARE policies using GPT-OSS-style prompts
    - Async policy execution with early BLOCK behaviour
- **Excluded (for MVP):**
    - Control Center (UI)
    - UMAI Service (control plane, auth, licensing)
    - Redis / SQL integration (we emulate them in-memory)

The MVP’s job: **simulate how the real AI Engine will behave** when wired into the full platform.

---

### Technology Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI
- **Server:** Uvicorn
- **Async model:** `asyncio` (native, via FastAPI & async handlers)
- **HTTP client for LLMs:** `openai` client or `httpx` (backed by env-configured base URL)
- **Testing:** `pytest`
- **Container:** Docker (optional but recommended)

---

### Project Structure (MVP)

Suggested structure:

```
ai-engine/
  app/
    __init__.py
    main.py                 # FastAPI app, entrypoint
    api/
      __init__.py
      routes.py             # /healthz, /evaluate
    models/
      __init__.py
      request.py            # InternalRequest model
      response.py           # InternalResponse model
      guardrail.py          # GuardrailSnapshot, Policy models
      policy_result.py      # PolicyResult internal type
    core/
      __init__.py
      guardrail_store.py    # DummyGuardrailStore (in-memory)
      pipeline.py           # Orchestrator & pipeline
      preflight.py          # Pre-flight heuristic checks
      llm_client.py         # LLM client wrapper (GPT-OSS style)
    policies/
      __init__.py
      base.py               # Policy interface
      heuristic.py          # HEURISTIC implementation
      context_aware.py      # CONTEXT_AWARE implementation
  tests/
    test_heuristic.py
    test_context_aware.py
    test_pipeline.py
  requirements.txt or pyproject.toml
  Dockerfile
  README.md

```

---

### Core Models

### Internal Request Model

Pydantic model reflecting our earlier design:

```python
# app/models/request.py
from typing import List, Literal, Optional
from pydantic import BaseModel

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class InputPayload(BaseModel):
    messages: List[ChatMessage]
    phase_focus: Literal["LAST_USER_MESSAGE", "LAST_ASSISTANT_MESSAGE"]
    content_type: Literal["text", "markdown", "json"] = "text"
    language: Optional[str] = None

class Flags(BaseModel):
    allow_llm_calls: bool = True

class InternalRequest(BaseModel):
    request_id: str
    timestamp: str

    tenant_id: str
    environment_id: str
    project_id: str
    guardrail_id: str
    guardrail_version: int

    phase: Literal["PRE_LLM", "POST_LLM"]

    input: InputPayload
    timeout_ms: Optional[int] = 1500
    flags: Flags = Flags()

```

### 3.4.2 Policy & Guardrail Snapshot Models

```python
# app/models/guardrail.py
from typing import List, Literal, Optional, Dict
from pydantic import BaseModel

PolicyType = Literal["HEURISTIC", "CONTEXT_AWARE"]

class HeuristicRule(BaseModel):
    id: str
    mode: Literal["REGEX", "EXACT"]
    pattern: str
    block_on_match: bool = True

class HeuristicConfig(BaseModel):
    target: Literal["LAST_MESSAGE", "FULL_HISTORY"] = "LAST_MESSAGE"
    rules: List[HeuristicRule]
    max_length: Optional[int] = None

class ContextAwareOutputSchema(BaseModel):
    violation_field: str
    category_field: str
    confidence_field: str
    rationale_field: str

class ContextAwareConfig(BaseModel):
    target: Literal["LAST_MESSAGE", "FULL_HISTORY"] = "LAST_MESSAGE"
    instructions: str
    definitions_and_category_map: str
    examples: str
    output_schema: ContextAwareOutputSchema
    min_confidence_for_block: Literal["low", "medium", "high"] = "medium"
    fail_closed_on_error: bool = True

class Policy(BaseModel):
    id: str
    type: PolicyType
    name: str
    enabled: bool = True
    phases: List[Literal["PRE_LLM", "POST_LLM"]]
    config: Dict  # will be validated per type at runtime or with custom root validator

class LLMConfig(BaseModel):
    provider: str
    base_url: str
    model: str
    timeout_ms: int = 2000

class GuardrailSnapshot(BaseModel):
    guardrail_id: str
    version: int
    mode: Literal["ENFORCE", "MONITOR"]
    phases: List[Literal["PRE_LLM", "POST_LLM"]]

    preflight: HeuristicConfig
    policies: List[Policy]
    llm_config: LLMConfig

```

### 3.4.3 Internal Response Model

```python
# app/models/response.py
from typing import Optional, List
from pydantic import BaseModel

class Decision(BaseModel):
    action: str        # "ALLOW" | "BLOCK" | "ALLOW_WITH_WARNINGS" (future)
    allowed: bool
    severity: str      # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    reason: str

class TriggeringPolicyResult(BaseModel):
    policy_id: str
    type: str
    name: str
    status: str        # "ALLOW" | "FLAG" | "BLOCK" | "ERROR"
    severity: str
    score: Optional[float] = None
    details: dict
    latency_ms: float

class LatencyInfo(BaseModel):
    total: float
    preflight: Optional[float] = None

class ErrorInfo(BaseModel):
    type: str
    source: Optional[str] = None
    message: Optional[str] = None
    retryable: Optional[bool] = None

class InternalResponse(BaseModel):
    request_id: str
    tenant_id: str
    environment_id: str
    project_id: str
    guardrail_id: str
    guardrail_version: int
    phase: str

    decision: Decision
    triggering_policy: Optional[TriggeringPolicyResult] = None
    latency_ms: LatencyInfo
    errors: List[ErrorInfo] = []

```

---

### 3.5 Dummy Guardrail Store

For MVP we **skip Redis** and emulate it.

```python
# app/core/guardrail_store.py
from typing import Optional
from .dummy_data import DUMMY_GUARDRAILS  # a dict defined below
from app.models.guardrail import GuardrailSnapshot

class DummyGuardrailStore:
    def get_guardrail(
        self,
        tenant_id: str,
        environment_id: str,
        project_id: str,
        guardrail_id: str,
        version: int,
    ) -> Optional[GuardrailSnapshot]:
        key = f"{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{version}"
        data = DUMMY_GUARDRAILS.get(key)
        if not data:
            return None
        return GuardrailSnapshot(**data)

```

Example dummy data:

```python
# app/core/dummy_data.py
DUMMY_GUARDRAILS = {
  "ent-acme:env-prod:proj-chat:gr-main:1": {
    "guardrail_id": "gr-main",
    "version": 1,
    "mode": "ENFORCE",
    "phases": ["PRE_LLM", "POST_LLM"],
    "preflight": {
      "target": "LAST_MESSAGE",
      "rules": [
        {
          "id": "preflight-ignore-previous",
          "mode": "REGEX",
          "pattern": "(?i)ignore (all|previous) instructions",
          "block_on_match": true
        }
      ],
      "max_length": 8000
    },
    "policies": [
      {
        "id": "pol-regex-blacklist",
        "type": "HEURISTIC",
        "name": "Regex & Keyword Blacklist",
        "enabled": true,
        "phases": ["PRE_LLM"],
        "config": {
          "target": "LAST_MESSAGE",
          "rules": [
            {
              "id": "rule-iban",
              "mode": "EXACT",
              "pattern": "iban numaram",
              "block_on_match": true
            }
          ],
          "max_length": 8000
        }
      },
      {
        "id": "pol-oss-main",
        "type": "CONTEXT_AWARE",
        "name": "UMAI Enterprise Guardrails (GPT-OSS)",
        "enabled": true,
        "phases": ["PRE_LLM", "POST_LLM"],
        "config": {
          "target": "LAST_MESSAGE",
          "instructions": "UMAI Enterprise Guardrails Policy\nINSTRUCTIONS\n...",
          "definitions_and_category_map": "DEFINITIONS & CATEGORY MAP\n...",
          "examples": "EXAMPLES\n...",
          "output_schema": {
            "violation_field": "violation",
            "category_field": "policy_category",
            "confidence_field": "confidence",
            "rationale_field": "rationale"
          },
          "min_confidence_for_block": "medium",
          "fail_closed_on_error": true
        }
      }
    ],
    "llm_config": {
      "provider": "OSS_ROUTER",
      "base_url": "https://router.huggingface.co/v1",
      "model": "openai/gpt-oss-safeguard-20b",
      "timeout_ms": 2000
    }
  }
}

```

(You can fill in actual full instructions/definitions/examples from your policy.)

---

### 3.6 Pipeline Orchestrator

Key responsibilities:

1. Validate request
2. Load guardrail snapshot
3. Run pre-flight
4. Run policies in parallel
5. Early exit on BLOCK
6. Build response

Pseudo-code:

```python
# app/core/pipeline.py
import time
import asyncio
from typing import List
from app.models.request import InternalRequest
from app.models.response import InternalResponse, Decision, LatencyInfo, ErrorInfo
from app.models.guardrail import GuardrailSnapshot
from app.core.guardrail_store import DummyGuardrailStore
from app.policies.heuristic import HeuristicPolicy
from app.policies.context_aware import ContextAwarePolicy

class Pipeline:
    def __init__(self, guardrail_store: DummyGuardrailStore):
        self.guardrail_store = guardrail_store

    async def evaluate(self, req: InternalRequest) -> InternalResponse:
        start = time.perf_counter()
        errors: List[ErrorInfo] = []

        snapshot = self.guardrail_store.get_guardrail(
            req.tenant_id,
            req.environment_id,
            req.project_id,
            req.guardrail_id,
            req.guardrail_version,
        )
        if not snapshot:
            # Config missing -> BLOCK
            decision = Decision(
                action="BLOCK",
                allowed=False,
                severity="HIGH",
                reason="Guardrail configuration missing"
            )
            latency = LatencyInfo(total=(time.perf_counter() - start) * 1000, preflight=None)
            errors.append(ErrorInfo(type="CONFIG_MISSING", source="guardrail_store"))
            return InternalResponse(
                request_id=req.request_id,
                tenant_id=req.tenant_id,
                environment_id=req.environment_id,
                project_id=req.project_id,
                guardrail_id=req.guardrail_id,
                guardrail_version=req.guardrail_version,
                phase=req.phase,
                decision=decision,
                triggering_policy=None,
                latency_ms=latency,
                errors=errors,
            )

        # 1) Preflight
        preflight_start = time.perf_counter()
        preflight_policy = HeuristicPolicy("preflight", snapshot.preflight)
        preflight_result = await preflight_policy.run(req)
        preflight_latency = (time.perf_counter() - preflight_start) * 1000

        if preflight_result.status == "BLOCK":
            decision = Decision(
                action="BLOCK",
                allowed=False,
                severity=preflight_result.severity,
                reason=f"Preflight: {preflight_result.details.get('rule_id')}"
            )
            latency = LatencyInfo(
                total=(time.perf_counter() - start) * 1000,
                preflight=preflight_latency,
            )
            return InternalResponse(
                request_id=req.request_id,
                tenant_id=req.tenant_id,
                environment_id=req.environment_id,
                project_id=req.project_id,
                guardrail_id=req.guardrail_id,
                guardrail_version=req.guardrail_version,
                phase=req.phase,
                decision=decision,
                triggering_policy=preflight_result.to_triggering_policy(),
                latency_ms=latency,
                errors=errors,
            )

        # 2) Main policies (parallel)
        # ... filter policies by phase, flags.allow_llm_calls, type
        # ... create async tasks, cancel on first BLOCK
        # ... aggregate and build response

```

(Implementation of async cancellation and PolicyResult omitted here for brevity, but the design is clear.)

---

### 3.7 HEURISTIC Policy Implementation

- Reads `HeuristicConfig`
- Extracts text based on `target`
- Applies regex/exact rules in order
- On first match → `BLOCK`, else `ALLOW`
- Returns internal `PolicyResult` with:
    - `status`, `severity`, `details`, `latency_ms`

---

### 3.8 CONTEXT_AWARE Policy Implementation

- Uses `ContextAwareConfig` + `snapshot.llm_config`
- Builds prompt from `instructions + definitions + examples + input`
- Calls LLM through `LLMClient`
- Parses JSON using `output_schema`
- Maps to `PolicyResult`:
    - `violation = 1` → typically `BLOCK`
    - `violation = 0` → `ALLOW`
- Handles errors according to `fail_closed_on_error`.

---

### 3.9 API Layer

`/healthz` – simple probe:

```python
# app/api/routes.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/healthz")
async def healthz():
    return {"status": "ok"}

```

`/internal/ai-engine/v1/evaluate`:

```python
from fastapi import APIRouter, Depends
from app.models.request import InternalRequest
from app.models.response import InternalResponse
from app.core.pipeline import Pipeline
from app.core.guardrail_store import DummyGuardrailStore

router = APIRouter()
pipeline = Pipeline(DummyGuardrailStore())

@router.post("/internal/ai-engine/v1/evaluate", response_model=InternalResponse)
async def evaluate(req: InternalRequest):
    return await pipeline.evaluate(req)

```

`main.py`:

```python
from fastapi import FastAPI
from app.api.routes import router as api_router

app = FastAPI(title="UMAI AI Engine (MVP)")
app.include_router(api_router)

```

---

### 3.10 Running Locally

1. Install deps:

```bash
pip install fastapi uvicorn openai pydantic[dotenv]

```

1. Export env vars:

```bash
export HF_API_BASE="https://router.huggingface.co/v1"
export HF_TOKEN="your_hf_or_router_token_here"

```

1. Run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload

```

1. Test ALLOW:

```bash
curl -X POST http://localhost:8081/internal/ai-engine/v1/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "req-1",
    "timestamp": "2025-12-09T12:00:00Z",
    "tenant_id": "ent-acme",
    "environment_id": "env-prod",
    "project_id": "proj-chat",
    "guardrail_id": "gr-main",
    "guardrail_version": 1,
    "phase": "PRE_LLM",
    "input": {
      "messages": [
        {"role": "user", "content": "Merhaba, hava nasıl?"}
      ],
      "phase_focus": "LAST_USER_MESSAGE",
      "content_type": "text",
      "language": "tr"
    },
    "timeout_ms": 1500,
    "flags": {
      "allow_llm_calls": true
    }
  }'

```

---

### 3.11 Dockerfile (MVP)

```docker
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV HF_API_BASE=https://router.huggingface.co/v1
# HF_TOKEN must be provided at runtime

EXPOSE 8081

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]

```

Build & run:

```bash
docker build -t umai-ai-engine-mvp .
docker run -e HF_TOKEN=your_token -p 8081:8081 umai-ai-engine-mvp

```