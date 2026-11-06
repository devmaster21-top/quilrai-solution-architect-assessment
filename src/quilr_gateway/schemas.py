"""Strict, shared schemas: no type coercion or undeclared arguments."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CustomerID = Annotated[str, StringConstraints(pattern=r"^CUST-[0-9]{5}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class CustomerInput(StrictModel):
    customer_id: CustomerID


class RefundInput(CustomerInput):
    amount: Annotated[float, Field(gt=0)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class CompletionInput(StrictModel):
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=16000)]
    max_tokens: Annotated[int, Field(ge=1, le=4096)] = 256
    stream: bool = False
