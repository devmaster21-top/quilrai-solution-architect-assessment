"""Real process tests: inspect every stdout line on the JSON-RPC wire."""

import json
import queue
import subprocess
import sys
import tempfile
import threading

import pytest


def exchange(requests):
    init = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "assessment-test", "version": "1.0"},
        },
    }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    messages = []
    with tempfile.TemporaryFile(mode="w+t") as logs:
        process = subprocess.Popen(
            [sys.executable, "-m", "quilr_gateway.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=logs,
            text=True,
        )
        lines = queue.Queue()

        def read_stdout():
            for line in process.stdout:
                lines.put(line)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        try:

            def send(message):
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()

            send(init)
            messages.append(json.loads(lines.get(timeout=15)))
            send(initialized)
            # EOF is a disconnect: keep stdin open until each response arrives.
            for item in requests:
                send(item)
                messages.append(json.loads(lines.get(timeout=15)))
            process.stdin.close()
            assert process.wait(timeout=15) == 0
            reader.join(timeout=2)
            while not lines.empty():
                messages.append(json.loads(lines.get_nowait()))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            logs.seek(0)
            stderr = logs.read()
    assert all(item["jsonrpc"] == "2.0" for item in messages)
    assert "Starting customer MCP server" in stderr
    return {item["id"]: item for item in messages if "id" in item}


def request(name, arguments, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_real_stdio_list_customer_and_refund():
    messages = exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            request("get_customer_record", {"customer_id": "CUST-00001"}, 2),
            request(
                "trigger_refund",
                {
                    "customer_id": "CUST-00001",
                    "amount": 12.5,
                    "reason": "Duplicate charge confirmed",
                },
                3,
            ),
        ]
    )
    assert len(messages[1]["result"]["tools"]) == 2
    assert "CUST-00001" in messages[2]["result"]["content"][0]["text"]
    assert '"status": "simulated"' in messages[3]["result"]["content"][0]["text"]


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("get_customer_record", {"customer_id": "cust-00001"}),
        ("get_customer_record", {"customer_id": "CUST-1234"}),
        ("get_customer_record", {"customer_id": 12345}),
        ("get_customer_record", {"customer_id": "CUST-00001", "extra": True}),
        (
            "trigger_refund",
            {"customer_id": "CUST-00001", "amount": "2.5", "reason": "long enough reason"},
        ),
        (
            "trigger_refund",
            {"customer_id": "CUST-00001", "amount": 0, "reason": "long enough reason"},
        ),
        (
            "trigger_refund",
            {"customer_id": "CUST-00001", "amount": -1, "reason": "long enough reason"},
        ),
        (
            "trigger_refund",
            {"customer_id": "CUST-00001", "amount": True, "reason": "long enough reason"},
        ),
        ("trigger_refund", {"customer_id": "CUST-00001", "amount": 1.5, "reason": "short"}),
        ("trigger_refund", {"customer_id": "CUST-00001", "amount": 1.5, "reason": "          "}),
        ("get_customer_record", {}),
        ("unknown_tool", {}),
    ],
)
def test_invalid_inputs_use_jsonrpc_invalid_params(name, arguments):
    messages = exchange([request(name, arguments)])
    assert messages[1]["error"]["code"] == -32602
    assert "result" not in messages[1]


def test_unknown_customer_is_a_tool_domain_error():
    message = exchange([request("get_customer_record", {"customer_id": "CUST-99999"})])[1]
    assert message["result"]["isError"] is True
