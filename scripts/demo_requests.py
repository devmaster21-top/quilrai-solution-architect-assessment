"""Interactive-free walkthrough for PowerShell, macOS, and Linux alike."""

import json

import httpx


def main():
    viewer = {"Authorization": "Bearer demo-viewer-token"}
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10, trust_env=False) as client:
        for name, token, payload in [
            ("Tool discovery", viewer, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            (
                "Denied viewer call",
                viewer,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "admin_reset_key"},
                },
            ),
            (
                "Allowed admin call",
                {"Authorization": "Bearer demo-admin-token"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "admin_reset_key"},
                },
            ),
        ]:
            print(name)
            print(json.dumps(client.post("/mcp", headers=token, json=payload).json(), indent=2))
        print("Redacted streaming response:")
        with client.stream(
            "POST",
            "/v1/completions",
            headers=viewer,
            json={
                "prompt": "Demonstrate safe streaming",
                "stream": True,
            },
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if "text" in event:
                        print(event["text"], end="", flush=True)
        print("\nPrimary 429 -> backup:")
        print(
            json.dumps(
                client.post(
                    "/v1/completions",
                    headers=viewer,
                    json={
                        "prompt": "[force-429]",
                    },
                ).json(),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
