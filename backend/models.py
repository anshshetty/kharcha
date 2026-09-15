"""Strict schemas shared by manual edits, ingestion, and backup validation."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator

Money = Annotated[int, Field(gt=0, le=9_007_199_254_740_991)]
ShortText = Annotated[str, Field(max_length=2000)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Kind = Literal[
    "purchase",
    "person_payment",
    "emi",
    "fee",
    "refund",
    "income",
    "card_repayment",
    "own_transfer",
    "investment",
    "wallet_funding",
    "cash_withdrawal",
    "financed_purchase",
    "financing_adjustment",
    "reimbursement",
    "lending",
]


class Allocation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    type: Literal["personal", "reimbursable", "lending"]
    amount_minor: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
    category: ShortText | None = None
    person: ShortText | None = None


class TransactionInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    direction: Literal["debit", "credit"]
    kind: Kind
    amount_minor: Money
    currency: Currency
    category: ShortText = "Uncategorized"
    counterparty: ShortText = "Unknown recipient"
    counterparty_key: ShortText | None = None
    identity_confirmed: bool = False
    account: ShortText = "Unknown account"
    reference: ShortText | None = None
    reference_namespace: ShortText = ""
    allocations: Annotated[list[Allocation], Field(max_length=1000)] = Field(default_factory=list)
    excluded: bool = False
    avoidable: bool = False
    linked_to: ShortText | None = None
    notes: Annotated[str, Field(max_length=10000)] = ""
    original_currency: Currency | None = None
    original_amount_minor: Money | None = None
    cash_source_id: ShortText | None = None

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value


# Missing patch fields have defaults but explicit null still has to satisfy
# the original annotation. Do not coerce "false", 0, or objects into booleans.
TransactionPatch = create_model(
    "TransactionPatch",
    __base__=TransactionInput,
    **{
        name: (
            Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation,
            None,
        )
        for name, field in TransactionInput.model_fields.items()
    },
)


class TransactionRecord(TransactionInput):
    # Internal parsers retain additional provenance fields. Public mutations
    # use TransactionInput/TransactionPatch, which reject unknown properties.
    model_config = ConfigDict(strict=True, extra="allow")
    warnings: list[ShortText] = Field(default_factory=list)
    date_basis: Literal["explicit", "received"] = "explicit"
    category_inference: dict[str, str] = Field(default_factory=dict)
    category_manual: bool = False
    rule_id: ShortText | None = None
    financing_source_id: ShortText | None = None
    separate_event: bool = False
    statement_key: ShortText | None = None
    statement_filename: ShortText | None = None
    statement_details: dict[str, str] = Field(default_factory=dict)


def validated(model, data, *, partial=False):
    try:
        return model.model_validate(data).model_dump(exclude_unset=partial)
    except ValidationError as error:
        # Report field names without echoing private financial data.
        fields = ", ".join(".".join(map(str, item["loc"])) or "request" for item in error.errors())
        raise ValueError("Invalid transaction field(s): " + fields) from None
