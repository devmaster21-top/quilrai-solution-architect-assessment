"""Official SDK stdio server; stdout belongs exclusively to the SDK transport."""

import asyncio
import json
import logging
import sys
from uuid import uuid4

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from pydantic import ValidationError

from quilr_gateway.schemas import CustomerInput, RefundInput

logger = logging.getLogger(__name__)
server = Server("quilr-customer-tools")
SCHEMAS = {"get_customer_record": CustomerInput, "trigger_refund": RefundInput}
CUSTOMERS = {
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Demo Customer", "currency": "USD"}
}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=name,
            description=description,
            inputSchema=SCHEMAS[name].model_json_schema(),
        )
        for name, description in [
            ("get_customer_record", "Read a synthetic customer record by CUST-XXXXX identifier."),
            ("trigger_refund", "Simulate a positive USD refund; no payment service is contacted."),
        ]
    ]


async def call_tool(name: str, arguments: dict) -> list[types.TextContent] | types.CallToolResult:
    schema = SCHEMAS.get(name)
    if schema is None:
        raise McpError(types.ErrorData(code=-32602, message="Unknown tool"))
    try:
        values = schema.model_validate(arguments)
    except ValidationError as exc:
        # Never echo input values (customer information can be sensitive).
        fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        raise McpError(
            types.ErrorData(code=-32602, message="Invalid tool arguments", data={"fields": fields})
        ) from None
    customer = CUSTOMERS.get(values.customer_id)
    if customer is None:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Customer not found")], isError=True
        )
    if isinstance(values, RefundInput):
        result = {
            "refund_id": f"REF-{uuid4().hex}",
            "customer_id": values.customer_id,
            "amount": values.amount,
            "currency": "USD",
            "status": "simulated",
        }
    else:
        result = customer
    return [types.TextContent(type="text", text=json.dumps(result, allow_nan=False))]


async def dispatch_call(request: types.CallToolRequest) -> types.ServerResult:
    # SDK 1.x's call_tool decorator catches McpError and turns it into isError.
    # Register at the official low-level dispatch boundary to preserve -32602.
    try:
        result = await call_tool(request.params.name, request.params.arguments or {})
        if not isinstance(result, types.CallToolResult):
            result = types.CallToolResult(content=result)
        return types.ServerResult(result)
    except McpError:
        raise
    except Exception:
        logger.error("Tool execution failed")
        raise McpError(types.ErrorData(code=-32603, message="Internal error")) from None


server.request_handlers[types.CallToolRequest] = dispatch_call


async def run() -> None:
    logger.info("Starting customer MCP server on stdio")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
