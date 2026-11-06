"""Reproducible real-TCP evaluation; starts/stops its own local demo process."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main():
    report = {"transport": "real HTTP over loopback TCP", "checks": {}}
    viewer = {"Authorization": "Bearer demo-viewer-token"}
    admin = {"Authorization": "Bearer demo-admin-token"}
    with tempfile.TemporaryFile(mode="w+t") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "quilr_gateway.demo"],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(
                base_url="http://127.0.0.1:8000", timeout=10, trust_env=False
            ) as client:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError("Demo startup failed: " + log.read())
                    time.sleep(0.1)
                report["checks"]["health"] = "passed"
                message = {
                    "jsonrpc": "2.0",
                    "id": "smoke",
                    "method": "tools/call",
                    "params": {"name": "admin_reset_key", "arguments": {}},
                }
                denied = client.post("/mcp", headers=viewer, json=message)
                assert denied.json()["error"]["code"] == -32001
                assert "result" in client.post("/mcp", headers=admin, json=message).json()
                report["checks"]["mcp_auth"] = "passed"
                started, first_delta, text = time.monotonic(), None, ""
                with client.stream(
                    "POST",
                    "/v1/completions",
                    headers=viewer,
                    json={
                        "prompt": "[stream-probe] Demonstrate PII redaction",
                        "stream": True,
                    },
                ) as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            data = json.loads(line[6:])
                            if "text" in data:
                                if first_delta is None:
                                    first_delta = time.monotonic()
                                    # The mock cannot complete until this client
                                    # has received a safe delta and releases it.
                                    released = client.post("http://127.0.0.1:9001/probe/release")
                                    assert released.status_code == 204
                                text += data["text"]
                            assert "error" not in data
                finished = time.monotonic()
                assert first_delta is not None
                assert text.count("[REDACTED]") == 3
                assert all(
                    secret not in text
                    for secret in (
                        "alex@example.com",
                        "123-45-6789",
                        "4111 1111 1111 1111",
                    )
                )
                report["checks"]["client_released_upstream_after_first_delta"] = "passed"
                report["checks"]["incremental_stream_and_pii"] = "passed"
                report["first_safe_delta_ms"] = round((first_delta - started) * 1000, 2)
                report["stream_total_ms"] = round((finished - started) * 1000, 2)
                report["safe_response"] = text
                for marker in ("[force-429]", "[force-timeout]"):
                    started = time.monotonic()
                    response = client.post(
                        "/v1/completions", headers=viewer, json={"prompt": marker}
                    )
                    assert response.status_code == 200
                    assert response.json()["provider"] == "backup"
                    report["checks"][marker] = "passed"
                    report[marker + "_ms"] = round((time.monotonic() - started) * 1000, 2)
                response = client.post(
                    "/v1/completions",
                    headers=viewer,
                    json={
                        "prompt": "[force-error]",
                    },
                )
                assert response.status_code == 502 and "PRIVATE" not in response.text
                report["checks"]["error_sanitization"] = "passed"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    destination = Path("docs/results/live-smoke.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
