"""Pydantic schemas for normalized, token-efficient package tracking output.

All courier adapters normalize their raw responses into these models so that
downstream consumers work against a single strict data contract regardless of
which courier produced the data.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class StatusCode(str, Enum):
    """Standardized, courier-agnostic status codes.

    Attributes:
        TRANSIT: Parcel is in the courier network and moving toward delivery.
        DELIVERED: Parcel has been handed over to the recipient.
        EXCEPTION: A problem occurred (failed delivery, customs hold, return, etc.).
    """

    TRANSIT = "TRANSIT"
    DELIVERED = "DELIVERED"
    EXCEPTION = "EXCEPTION"


class TrackingEvent(BaseModel):
    """A single scan/event in a parcel's journey.

    Attributes:
        timestamp: When the event occurred, timezone-aware where the courier provides it.
        status_code: Standardized status (`TRANSIT`, `DELIVERED`, `EXCEPTION`).
        description: The original, unmodified courier description of the event.
        location: Explicit facility/depot name (e.g. "LOKEREN X"), or ``None`` when
            the courier did not provide a resolvable facility for this event.
    """

    timestamp: datetime
    status_code: StatusCode = Field(
        description="Standardized: 'TRANSIT', 'DELIVERED', 'EXCEPTION'"
    )
    description: str = Field(description="Original courier description")
    location: Optional[str] = Field(
        default=None,
        description='Explicit facility/depot name (e.g. "LOKEREN X")',
    )


class PackageStatus(BaseModel):
    """The normalized status of a tracked parcel.

    Attributes:
        tracking_number: The tracking number as queried.
        courier: Identifier of the courier that produced the data (e.g. "bpost").
        is_delivered: ``True`` when the parcel has reached a delivered state.
        latest_event: The most recent event in the parcel's history.
        history: Full chronological list of events, newest last.
    """

    tracking_number: str
    courier: str
    is_delivered: bool
    latest_event: TrackingEvent
    history: List[TrackingEvent]
