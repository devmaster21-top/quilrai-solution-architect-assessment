# QuilrAI Solution Architect Assessment

Runnable solutions to all four tasks: an official-SDK MCP stdio server, an authenticated MCP HTTP proxy, a streaming PII guardrail, and a persistent token-budget router with provider fallback.

The default demo is completely local. It needs no provider account, paid API key, or external database. Customer records, refunds, credentials, and model replies in the demo are synthetic.

## Quick start

Requires Python 3.11+ and two free local ports: 8000 and 9001.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux instead:
# source .venv/bin/activate

python -m pip install -e ".[dev]"
python -m quilr_gateway.demo
```

The gateway listens at `http://127.0.0.1:8000`; isolated mock upstreams listen on port 9001. Stop with Ctrl+C. The SQLite database is created at `data/gateway.sqlite3` and survives restarts.

For the resolved dependency set used in evaluation, install `requirements.lock.txt` before installing the project (`python -m pip install -r requirements.lock.txt`, then `python -m pip install --no-deps -e .`). Windows-only dependencies have platform markers.

Alternatively, `docker compose up --build` runs the same HTTP demo with a persistent volume. Docker is optional; see [validation evidence](docs/EVALUATION.md) for what was executed locally.

### Run the assessment checks

In another activated terminal:

```bash
pytest -q --cov=quilr_gateway --cov-report=term-missing --cov-fail-under=80
ruff check .
ruff format --check .
python scripts/smoke_test.py
python scripts/benchmark_redaction.py
```

Or run `python scripts/evaluate.py` with ports 8000 and 9001 free to execute all gates and save an overall validation report.

The smoke script starts its own demo, so stop any existing demo first. It exercises real TCP, authentication, incremental SSE, redaction, 429 fallback, the 3000 ms timeout, and sanitized errors. Results are saved under [docs/results](docs/results). The benchmark generates input lazily and discards output to measure redactor working memory.

## Requirement map

| Task | Implementation | Main verification |
| --- | --- | --- |
| 1. Validated MCP server over stdio | [mcp_server.py](src/quilr_gateway/mcp_server.py), [schemas.py](src/quilr_gateway/schemas.py) | Real subprocess wire tests, strict schema and nonfinite-number cases |
| 2. Bearer auth and tool authorization | [app.py](src/quilr_gateway/app.py), [config.py](src/quilr_gateway/config.py) | Viewer denial returns `-32001`, zero downstream calls; transparent tool list |
| 3. Streaming PII redaction | [redaction.py](src/quilr_gateway/redaction.py), [providers.py](src/quilr_gateway/providers.py) | Every two-chunk boundary, single-character deltas, oversized-token test, live TTFT |
| 4. Token-aware sliding window and fallback | [limiter.py](src/quilr_gateway/limiter.py), [providers.py](src/quilr_gateway/providers.py) | 50 concurrent reservations, persisted state, expiry edges, tenant isolation, timeout and 429 fallback |

## Task 1: MCP stdio server

```bash
python -m quilr_gateway.mcp_server
```

Configure an MCP client with this command; interactive human text is not a valid MCP request. Example client configuration, after installing the project:

```json
{
  "mcpServers": {
    "quilr-customer-tools": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "quilr_gateway.mcp_server"]
    }
  }
}
```

On Windows use the absolute path to `.venv\\Scripts\\python.exe`, with JSON-escaped backslashes. The official `mcp` SDK handles initialization, framing, capability discovery, transport, and protocol responses.

| Tool | Arguments | Example |
| --- | --- | --- |
| `get_customer_record` | `customer_id`: exactly `CUST-` and five ASCII digits | `{"customer_id":"CUST-00001"}` |
| `trigger_refund` | Same ID, finite positive numeric `amount`, trimmed `reason` of 10–2000 characters | `{"customer_id":"CUST-00001","amount":12.5,"reason":"Duplicate payment received"}` |

Pydantic strict validation rejects coercion, booleans as money, missing/extra fields, invalid IDs, NaN, and infinity. JSON numeric integers are valid positive amounts, alongside decimal numbers. Invalid arguments and unknown tools return JSON-RPC `-32602`; unexpected execution failures return sanitized `-32603`. A valid but nonexistent customer returns an MCP domain error (`isError: true`). Refunds return a unique simulated receipt; no funds move.

The pinned SDK's standard tool decorator converts exceptions to tool results. A low-level SDK request handler deliberately preserves protocol errors, as the assessment requires. Logs use stderr exclusively. [Wire tests](tests/test_mcp_stdio.py) parse every stdout line as JSON-RPC after completing the actual initialization handshake.

## Tasks 2–4: HTTP gateway

Use [scripts/demo_requests.py](scripts/demo_requests.py) for a platform-independent walkthrough:

```bash
python scripts/demo_requests.py
```

Or make requests directly. In PowerShell use `curl.exe` for these curl examples.

```bash
# Discovery is forwarded without role-based list filtering.
curl http://127.0.0.1:8000/mcp \
  -H 'Authorization: Bearer demo-viewer-token' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Viewer receives -32001; switching to demo-admin-token permits the mock call.
curl http://127.0.0.1:8000/mcp \
  -H 'Authorization: Bearer demo-viewer-token' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"admin_reset_key","arguments":{}}}'

# Stream redacted text with SSE events: delta, done, or error.
curl -N http://127.0.0.1:8000/v1/completions \
  -H 'Authorization: Bearer demo-viewer-token' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Show sample PII","max_tokens":256,"stream":true}'

# Force primary throttling. Response reports provider: backup.
curl http://127.0.0.1:8000/v1/completions \
  -H 'Authorization: Bearer demo-viewer-token' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"[force-429]"}'
```

Use `[force-timeout]` to simulate a primary taking over 3000 ms; `[force-error]` returns a sanitized gateway error. These markers only affect the mock service. The gateway forwards prompts unchanged to configured providers.

Expected safe reply:

```text
Demo reply: email [REDACTED], SSN [REDACTED], card [REDACTED]. All sensitive values must be hidden.
```

### Auth and JSON-RPC behavior

Bearer tokens are opaque credentials resolved against a server-owned token-to-tenant/role map. Roles are never trusted from a caller-supplied header or an unsigned JWT. All HTTP generation/MCP calls require authentication; `/health` is public. Viewer and admin credentials for the same tenant share its token budget.

`tools/list` passes through unchanged. `tools/call` names beginning with `admin_` require the admin role. Unauthorized calls return `{"jsonrpc":"2.0","id":2,"error":{"code":-32001,"message":"Unauthorized Tool Call"}}` without contacting the upstream. Missing/invalid bearer credentials receive HTTP 401. JSON-RPC IDs are preserved; notifications return HTTP 202 with no response body. Batch requests are explicitly unsupported and rejected. The proxy implements HTTP/JSON-RPC to the downstream mock, not a general MCP Streamable HTTP session/SSE bridge. Task 1's SDK server is a separate stdio process.

### Streaming policy

The provider adapter consumes OpenAI-compatible chat-completion SSE, incrementally parses UTF-8/network framing, and caps event size. The public `/v1/completions` endpoint has its own documented SSE format; it is not a drop-in OpenAI response schema.

Redaction supports common ASCII emails, `NNN-NN-NNNN` SSNs, and 13–19 digit card-like numbers with spaces/hyphens. Cards are masked without a Luhn check to favor privacy. Candidate words and numeric runs are held until their boundary, so PII split across any chunk boundary never needs to be emitted speculatively. The pending candidate is capped at 320 characters; longer tokens are replaced once and their remainder suppressed. Normal prose is released at word boundaries. The SSE path never collects the full response.

This trades a word of latency for preventing partial-PII leaks. Quoted/internationalized email addresses, semantic PII, and obfuscated values are outside this explicit pattern policy. Oversized innocent tokens can also be masked. See [architecture decisions](docs/ARCHITECTURE.md).

### Token budget and resilience policy

The limiter reserves `UTF-8 prompt bytes + 32 envelope tokens + max_tokens` before upstream work, against a default 50,000-token, 60-second rolling window per authenticated tenant. This is a conservative tokenizer-independent admission estimate, not exact model billing. Output is reserved in advance; the response names it `reserved_tokens`. Reservations remain charged until expiry, including failed/cancelled requests, and are not refunded from untrusted or missing usage reports. Fallback consumes the same single reservation. For exact billing across providers, replace the estimator with model-specific tokenizers and add trusted usage reconciliation.

SQLite `BEGIN IMMEDIATE` makes budget check and insertion atomic across gateway workers sharing the same local database file. WAL mode, bounded lock waits, an expiry index, and admission-time cleanup keep state durable and bounded to the active window. Raw API keys are not stored in SQLite. Rejections return HTTP 429 with `Retry-After` calculated from enough expiring reservations to fit the request.

The router switches to backup on primary HTTP 429 or a 3000 ms deadline while awaiting the first text delta, including connection/header time. Both providers have per-delta deadlines and a 30-second total request cap. It does not retry primary, switch on nonretryable errors, or switch after text generation starts. A midstream failure emits a sanitized SSE `error`, discards any held candidate, and closes without `done`; restarting on another model after output would splice answers. Client disconnect/cancellation closes active upstream streams. Nonstream requests return a standardized HTTP 502 payload on provider failure.

## Use real upstreams

Copy [.env.example](.env.example) to `.env`, configure fresh long random bearer tokens and both provider URLs/model names/keys, then run:

```bash
uvicorn quilr_gateway.app:create_app --factory --env-file .env --host 127.0.0.1 --port 8000
```

The provider URLs are full OpenAI-compatible `/v1/chat/completions` endpoints. Client input cannot select URLs, models, roles, tenants, or provider credentials. Caller bearer tokens never pass to downstream services; `MCP_TOKEN` is a separate service credential. Provider redirects and proxy environment inheritance are disabled.

The included deployment is an assessment implementation. See [security and zero-trust operations](docs/OPERATIONS.md) for TLS, identity, egress, SQLite scaling, and deployment troubleshooting. [Evaluation](docs/EVALUATION.md) records executed checks and limits. The original assessment PDF is excluded from the public repository.
