"""Pydantic tool-input schemas for the driver chat LLM agent.

These are separate from ``driver_chat_eta.models`` on purpose: the models
in that file describe the REST API's request/response shapes, while these
describe exactly the arguments Gemini is allowed to fill in for a tool
call. Field descriptions here are part of the tool's JSON schema and are
read by the model itself, so they're written as instructions to the LLM,
not just documentation for humans.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReportExceptionInput(BaseModel):
    """Structured extraction of a driver's delay/ETA-change message."""

    delay_minutes: int = Field(
        default=0,
        ge=0,
        le=1440,
        description=(
            "Minutes later than the originally planned ETA the driver now expects to "
            "arrive. Use 0 if the driver did not give a relative delay (e.g. they gave "
            "an exact new time instead, or reported something with no ETA impact yet)."
        ),
    )
    declared_eta_iso: str | None = Field(
        default=None,
        description=(
            "Exact new ETA as an ISO-8601 datetime string (e.g. '2026-08-10T14:30:00'), "
            "only if the driver gave a specific clock time instead of, or in addition to, "
            "a relative delay. Leave null if they only gave a relative delay."
        ),
    )
    must_leave_by_iso: str | None = Field(
        default=None,
        description=(
            "Latest ISO-8601 datetime the driver must be finished unloading/leaving by, "
            "only if they mentioned a hard deadline (e.g. 'I must leave before 9pm for my "
            "next load'). Leave null otherwise."
        ),
    )
    note: str = Field(
        description="A short, one-sentence summary of what the driver reported, for the audit trail."
    )


class ListFeasibleSlotsInput(BaseModel):
    """No arguments needed -- re-checks slots for the driver's current shipment/ETA."""


class AutoBookSlotInput(BaseModel):
    """No arguments needed -- the agent picks the earliest compatible slot itself."""


class SlotIdInput(BaseModel):
    slot_id: str = Field(
        description=(
            "The exact slot_id of a dock slot option that was already returned by a "
            "previous tool call in this conversation. Never invent or guess a slot_id."
        )
    )


class UpdateCheckinInput(BaseModel):
    stage: Literal["arrived_gate", "waiting_yard", "docked", "completed"] = Field(
        description=(
            "The physical stage the driver just reached: 'arrived_gate' = at the facility "
            "gate, 'waiting_yard' = parked in the yard queue, 'docked' = backed into the "
            "dock door, 'completed' = unloaded and leaving. Only call this when the driver "
            "explicitly says they've just reached that stage."
        )
    )


class EscalateInput(BaseModel):
    reason: str = Field(description="Why a human dispatch coordinator needs to take over this conversation.")
