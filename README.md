# UMAI Enterprise Engine

Internal FastAPI service that enforces guardrail policies for UMAI traffic. The engine is designed to run behind
UMAI Service and is not exposed to end users. It evaluates requests against configured guardrail snapshots and
returns structured decisions for allow/block/flag flows.

## Key Features
- Preflight heuristic checks (regex/exact) before any expensive work.
- Heuristic policies for fast keyword or pattern matching.
- Context-aware policies via an OpenAI-compatible LLM endpoint (GPT-OSS style).
- Async policy execution with early block and task cancellation.
- Deterministic internal request/response contract for the control plane.

## Architecture Overview
1. Receive internal request from UMAI Service.
2. Load guardrail snapshot by tenant/environment/project/guardrail/version.
3. Run preflight heuristic rules.
4. Run applicable policies in parallel for the requested phase.
5. Early block on the first BLOCK result; otherwise aggregate results.
6. Return a structured decision payload.

## Project Layout
```
app/
  api/                # FastAPI routes
  core/               # pipeline, guardrail store, LLM client
  models/             # request/response/guardrail schemas
  policies/           # heuristic and context-aware policies
tests/                # (planned) unit tests
```

## Requirements
- Python 3.11+
- Dependencies in `requirements.txt`

Install:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

### Environment Variables
- `REDIS_URL` — Redis connection string for guardrail snapshot store (falls back to in-memory dummy if unset)
- `SNAPSHOT_SIGNING_KEY` — shared HMAC-SHA256 secret for snapshot signature verification (must match UMAI Service)
- `LICENSE_KEY` — license key provided by UMAI
- `GROQ_API_KEY` (required for Groq inference)
- `LLM_API_KEY` (optional fallback for custom endpoints)

### Guardrail Snapshot
By default the engine uses an in-memory guardrail snapshot defined in `app/core/dummy_data.py`. Update this file to change:
- policy rules
- policy prompt content (instructions, definitions, examples)
- LLM base URL, model, and timeout

If `REDIS_URL` is set, the engine will load snapshots from Redis using the key format:
`guardrail:{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{version}`.

## Run Locally
```bash
set GROQ_API_KEY=your_groq_api_key
uvicorn app.main:app --reload --host 0.0.0.0 --port 9000
```

Health check:
```
GET http://localhost:9000/healthz
```

Evaluate:
```
POST http://localhost:9000/internal/ai-engine/v1/evaluate
```

Example request:
```json
{
  "request_id": "req-1",
  "timestamp": "2025-01-01T00:00:00Z",
  "tenant_id": "ent-acme",
  "environment_id": "env-prod",
  "project_id": "proj-chat",
  "guardrail_id": "gr-main",
  "guardrail_version": 1,
  "phase": "PRE_LLM",
  "input": {
    "messages": [
      {"role": "user", "content": "iban numaram nedir?"}
    ],
    "phase_focus": "LAST_USER_MESSAGE",
    "content_type": "text",
    "language": "tr"
  },
  "timeout_ms": 1500,
  "flags": {"allow_llm_calls": true}
}
```

Example response (block):
```json
{
  "request_id": "req-1",
  "tenant_id": "ent-acme",
  "environment_id": "env-prod",
  "project_id": "proj-chat",
  "guardrail_id": "gr-main",
  "guardrail_version": 1,
  "phase": "PRE_LLM",
  "decision": {
    "action": "BLOCK",
    "allowed": false,
    "severity": "HIGH",
    "reason": "HEURISTIC: rule rule-iban matched"
  },
  "triggering_policy": {
    "policy_id": "pol-regex-blacklist",
    "type": "HEURISTIC",
    "name": "Regex & Keyword Blacklist",
    "status": "BLOCK",
    "severity": "HIGH",
    "score": null,
    "details": {
      "matched_rule_id": "rule-iban",
      "matched_pattern": "iban numaram",
      "mode": "EXACT",
      "block_on_match": true
    },
    "latency_ms": 1.2
  },
  "latency_ms": {
    "total": 5.2,
    "preflight": 0.4
  },
  "errors": []
}
```

## Policy Behavior

### Heuristic Policy
- `REGEX` rules use Python regex.
- `EXACT` rules use case-insensitive substring matching.
- `max_length` can block oversized inputs for safety.

### Context-Aware Policy
- Builds a prompt from instructions, definitions, and examples.
- Calls the configured OpenAI-compatible endpoint.
- Expects JSON output using the configured output schema fields.
- Applies `min_confidence_for_block` and `fail_closed_on_error`.

### LLM Calls
If `flags.allow_llm_calls` is `false`, context-aware policies are skipped and return errors in the response. In
`ENFORCE` mode, these errors can block; in `MONITOR` mode they are surfaced as warnings.

## Production Notes
- Run this service behind UMAI Service (internal-only network access).
- Use network policies or firewall rules to prevent public access.
- Store tokens in a secret manager and inject as environment variables.
- Prefer internal LLM endpoints for data residency requirements.
- Scale horizontally and use `/healthz` for liveness/readiness probes.

## Troubleshooting
- `Missing Groq API key`: ensure `GROQ_API_KEY` is set before starting the server.
- `No JSON object found in LLM response`: model did not follow the JSON-only output format; verify policy prompt
  content in `app/core/dummy_data.py`.
- `LLM calls disabled`: set `flags.allow_llm_calls` to `true` if you want context-aware policies to run.

## Progress
See `progress-0.md` for the initial implementation milestones and next steps.
