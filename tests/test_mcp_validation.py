import math

import mcp.types as types
import pytest
from mcp.shared.exceptions import McpError

from quilr_gateway.mcp_server import call_tool, dispatch_call, list_tools


async def test_advertised_schema_is_closed_and_required():
    tools = await list_tools()
    for tool in tools:
        assert tool.inputSchema["additionalProperties"] is False
        assert "customer_id" in tool.inputSchema["required"]
    assert tools[1].inputSchema["properties"]["amount"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf, True, "12.50", 0, -1])
async def test_nonfinite_or_nonpositive_refunds_rejected(amount):
    with pytest.raises(McpError) as error:
        await call_tool(
            "trigger_refund",
            {
                "customer_id": "CUST-00001",
                "amount": amount,
                "reason": "Valid refund reason",
            },
        )
    assert error.value.error.code == -32602


@pytest.mark.parametrize("customer_id", ["CUST-00001\n", "CUST-１２３４５", "CUST-123456", None])
async def test_exact_customer_id_format(customer_id):
    with pytest.raises(McpError):
        await call_tool("get_customer_record", {"customer_id": customer_id})


async def test_direct_success_and_domain_error():
    result = await call_tool("get_customer_record", {"customer_id": "CUST-00001"})
    assert "Demo Customer" in result[0].text
    missing = await call_tool("get_customer_record", {"customer_id": "CUST-99999"})
    assert missing.isError
    result = await dispatch_call(
        types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="trigger_refund",
                arguments={
                    "customer_id": "CUST-00001",
                    "amount": 1.5,
                    "reason": "Duplicate payment",
                },
            ),
        )
    )
    assert not result.root.isError


async def test_unknown_tool_and_missing_arguments():
    for name, arguments in [("missing", {}), ("trigger_refund", {})]:
        with pytest.raises(McpError) as error:
            await dispatch_call(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=name, arguments=arguments),
                )
            )
        assert error.value.error.code == -32602
