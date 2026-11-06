# Submission

**Candidate:** devmaster21-top

**Assessment:** QuilrAI Solution Architect — all four tasks

Start with the [README](README.md). The [requirement map](README.md#requirement-map) links every task to its implementation and tests.

```bash
python -m venv .venv
# Activate the environment for your shell, then:
python -m pip install -e ".[dev]"
python scripts/evaluate.py
python -m quilr_gateway.demo
```

The evaluation starts and stops its own HTTP demo; run the interactive demo afterward. No paid provider credentials are required. The MCP stdio server is launched separately using `python -m quilr_gateway.mcp_server` or `quilr-mcp`.

Review [validation evidence](docs/EVALUATION.md), [architecture decisions](docs/ARCHITECTURE.md), and [security/operations](docs/OPERATIONS.md). Measured results are in [docs/results](docs/results). GitHub Actions reruns lint, formatting, coverage-gated tests, and live HTTP checks on each push.
