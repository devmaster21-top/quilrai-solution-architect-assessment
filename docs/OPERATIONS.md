# Security and deployment operations

## Trust boundaries

The public client authenticates only to the gateway. Tenant and role are derived from the credential map; a caller cannot choose a provider destination. The gateway uses separate service/provider credentials and does not forward caller credentials or arbitrary headers. Tokens, prompts, model text, upstream bodies, and database secrets are never intentionally logged. Error responses carry an opaque request ID instead of stack traces.

For a production deployment, terminate TLS at a trusted ingress, authenticate internal service hops with mTLS or workload identity, store and rotate credentials in a secret manager, and restrict egress to approved provider/MCP destinations. Apply request and connection limits at ingress, timeouts, and explicit body-size limits. The 64 KiB application body cap does not replace slow-client protection at ingress. Authorize on the gateway for every tool call; discovery is not authority to execute a tool.

The application binds loopback by default. Containers run as a nonroot user with dropped capabilities, a read-only root filesystem, an internal network, and only SQLite's data directory writable. The Compose mock network intentionally has no external provider egress. Real providers require an explicitly allowed external route and DNS. Docker is optional; its configuration is supplied separately from executed Python tests.

Opaque demo tokens are intentionally public and must only be used in the local demo. The source PDF, `.env`, SQLite files, virtual environment, caches, and Git credentials are excluded from Git. The mock admin tool and simulated refunds perform no real administrative or payment actions.

## SQLite operations

Use a durable local filesystem for the database, with writable access to the database and its parent directory for `-wal` and `-shm` files. Share the same path among workers on one host. Run migrations before rolling out schema changes. Back up using SQLite's backup API or a coordinated checkpoint/backup; copying the main file alone while writes are active may omit WAL data.

The implementation removes expired rows on admission. A quiet database can retain old rows on disk until the next request; they cannot count after expiry. WAL files and freelist space can remain allocated after deletion; schedule checkpoint/maintenance for sustained production traffic. Observe lock wait rates and disk space. A SQLite failure returns 503 and does not silently admit unmetered requests. SQLite throughput and a server-configured token map are explicit assessment-scale choices.

## Troubleshooting a zero-trust deployment

| Symptom | Check and evidence | Resolution |
| --- | --- | --- |
| 401 before tool discovery | Bearer format, token mapping, ingress stripping Authorization | Preserve the header only on the trusted ingress hop; rotate/reload the correct credential |
| `-32001` on an admin call | Server-side role mapping and exact `params.name` | Grant an appropriate identity through the identity system; do not bypass the policy |
| MCP works locally, fails behind proxy | Egress policy, destination DNS, service credential, JSON content type | Permit the exact service route; verify internal TLS trust and the gateway's service identity |
| TLS handshake failure | CA bundle, SAN hostname, certificate expiry, trust chain, mTLS client certificate | Correct identity and trust roots; keep certificate verification enabled |
| Provider switches at about three seconds | TCP/TLS setup, firewall drops, provider latency, first-delta timing | Measure each hop; permit the approved provider and retain the bounded fallback deadline |
| SSE arrives all at once | Ingress response buffering, compression buffering, SSE content type | Disable buffering on this route; preserve `Cache-Control: no-store` and `X-Accel-Buffering: no` |
| 429 persists across different API keys | Keys may map to the same tenant | Confirm tenant ownership; inspect reservation totals with tenant hashes, not raw credentials |
| SQLite unavailable or busy | Directory permissions, disk space, local mount, worker contention | Fix the writable local volume or reduce contention; do not bypass admission checks |
| stdio client cannot initialize | Wrong interpreter, import failure, stdout contamination, client handshake | Use the installed environment's absolute Python path; inspect stderr; run wire tests |
| Stream ends with error and no done | Provider timeout, malformed/truncated SSE, stream limit | Treat generation as incomplete; retry as a new user request if appropriate |

## Deliberate limits

This is not a full MCP Streamable HTTP gateway or multi-region production service. It has no dynamic identity-provider integration, semantic DLP classifier, exact provider billing reconciliation, circuit breaker, distributed quota store, metrics backend, or payment integration. These are recorded limits, not claims of implemented functionality. The four requested tasks are exercised by local automated and real-TCP checks.
