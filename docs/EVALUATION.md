# Evaluation and submission evidence

Evaluated on Windows with Python 3.14.7. These results assess the supplied synthetic fixtures and explicit policies; they are not an independent assessor's score or a production certification.

## Executed checks

| Area | Executed evidence | Result |
| --- | --- | --- |
| Automated tests | Strict inputs; real MCP subprocess wire; HTTP authorization; SSE parsing; all PII split positions; cancellation; fallback; limiter persistence/concurrency | 95 passing tests |
| Coverage | `pytest --cov=quilr_gateway` over the entire package, including demo code | About 85% line coverage |
| Style and dependency consistency | Ruff lint/format and `pip check` | Passing |
| Live TCP streaming | Self-started gateway and mock service over loopback | Safe deltas arrive before completion; three PII values masked |
| Live primary throttling | Mock returns HTTP 429 | Backup selected |
| Live primary deadline | Mock waits 3500 ms; gateway first-delta deadline is 3000 ms | Backup selected, response completes in approximately 3.5 s |
| Error sanitization | Mock body includes a synthetic private diagnostic | Client gets a fixed error message; diagnostic absent |
| Memory | Lazy 10-million-character unbroken input | Candidate state stays at or below 320 characters |
| Compose structure | `docker compose config --quiet` | Valid |

Run `python scripts/evaluate.py` to repeat all Python submission gates and overwrite the measured reports. It requires ports 8000 and 9001 to be free. The report is written only after every gate succeeds.

Machine-readable evidence:

- [Overall validation](results/validation.json)
- [Live HTTP latency and fallback results](results/live-smoke.json)
- [Redactor working-memory measurements](results/redaction-benchmark.json)

Latency and allocation figures are local observations. The first safe delta measured roughly 43 ms in the initial run, versus roughly 483 ms for completion. These are not provider or network service-level guarantees. The checked-in JSON records the latest run and may differ slightly. `tracemalloc` reports the redactor loop's Python allocations, not the whole HTTP service's process memory.

## Evaluation against each task

The live streaming check uses a client-controlled gate: the upstream sends the first word, then waits until the client receives a safe delta and releases the remaining response over a separate request. A fully buffered gateway cannot pass this check. This replaces an elapsed-time assertion that was sensitive to Windows CI scheduling; latency is still recorded as an observation.

1. **MCP server:** uses the official SDK, advertises closed Pydantic schemas, validates exact ASCII customer IDs and finite positive amounts, rejects invalid arguments with `-32602`, isolates logs to stderr, and tests actual JSON-RPC stdout. The wire harness completes initialization and waits for replies before disconnecting.
2. **MCP gateway:** authenticates opaque bearer credentials, transparently lists all tools, authorizes `admin_*` at execution, preserves request IDs, and verifies that rejected calls cause zero downstream tool executions. Service credentials and caller credentials remain separate. Malformed envelopes, unsupported batches/methods, notifications, and input limits have defined behavior.
3. **Streaming guardrail:** uses independent incremental SSE framing and bounded lexical redaction. Tests exercise every possible two-chunk split and one-character deltas for the declared PII patterns. An oversized-token test demonstrates fail-closed bounded state. A real TCP test checks that the first safe text is received while generation is still running.
4. **Limiter/router:** SQLite persists across instances and uses atomic check/insert transactions. Fifty concurrent admissions through five independent limiter instances admit exactly the available capacity. Tests cover exact expiry boundaries, tenant isolation, sufficient-expiry retry timing, primary timeout/429, both providers failing, explicit stream close, and no fallback after output.

## Limits and unexecuted environments

- Docker Engine was unavailable on this workstation. Compose syntax was validated; image build, volume permissions, and container runtime behavior were not executed locally.
- Real paid provider APIs were not called. HTTP fixtures implement their documented SSE interface; tests force statuses, split framing, and timing without external credentials.
- GitHub Actions defines Windows/Linux and Python 3.11/3.14 jobs. Local success does not imply those remote jobs ran; inspect Actions after publication.
- The budget is a conservative reservation estimate, not exact model token billing. There is no post-completion reconciliation. Midstream fallback is deliberately disallowed to avoid mixed answers.
- The redactor implements an explicit ASCII-pattern policy, not semantic or universal PII detection. Ordinary words longer than the state cap may be redacted as a privacy-preserving false positive.

## Submission contents

The repository contains all four implementations, deterministic mock services, installation metadata and resolved dependencies, runnable demo and evaluation scripts, automated tests, CI, deployment configuration, architecture decisions, and operational troubleshooting. No real credentials, customer records, payment operations, database state, or original private assessment PDF are included.
