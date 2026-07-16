"""Tests for the bpost adapter, mocking all outbound HTTP with respx."""

import httpx
import pytest
import respx

from adapters.base import CourierError
from adapters.bpost import BpostAdapter
from core.models import StatusCode

BPOST_URL = BpostAdapter.BASE_URL


def _delivered_payload() -> dict:
    """Return a representative bpost payload for a delivered parcel.

    Exercises multilingual descriptions, a nested location object, and the
    explicit facility name "LOKEREN X".
    """
    return {
        "items": [
            {
                "itemCode": "323212345678901234567890",
                "known": True,
                "state": "DELIVERED",
                "events": [
                    {
                        "date": "2026-07-14",
                        "time": "08:03:00",
                        "key": "ANNOUNCED",
                        "description": {"en": "Shipment announced", "nl": "Aangekondigd"},
                    },
                    {
                        "date": "2026-07-15",
                        "time": "09:12:00",
                        "key": "ARRIVED_AT_SORTING_CENTER",
                        "description": {"en": "Arrived at sorting center"},
                        "location": {"name": "LOKEREN X", "municipality": "LOKEREN"},
                    },
                    {
                        "date": "2026-07-16",
                        "time": "14:32:00",
                        "key": "DELIVERED_AT_HOME",
                        "description": {"en": "Delivered"},
                        "location": {"name": "BRUSSEL 1"},
                    },
                ],
            }
        ]
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_maps_payload_to_model() -> None:
    """Raw bpost JSON maps onto PackageStatus with the facility name captured."""
    route = respx.get(BPOST_URL).mock(
        return_value=httpx.Response(200, json=_delivered_payload())
    )

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890")

    assert route.called
    assert status.courier == "bpost"
    assert status.tracking_number == "323212345678901234567890"
    assert status.is_delivered is True

    # History is chronological and complete.
    assert len(status.history) == 3
    assert [e.timestamp.isoformat() for e in status.history] == [
        "2026-07-14T08:03:00",
        "2026-07-15T09:12:00",
        "2026-07-16T14:32:00",
    ]

    # Latest event is the delivery, with standardized status and original text.
    latest = status.latest_event
    assert latest.status_code == StatusCode.DELIVERED
    assert latest.description == "Delivered"
    assert latest.location == "BRUSSEL 1"

    # The specific facility name is captured on the sorting-center event.
    sorting = status.history[1]
    assert sorting.location == "LOKEREN X"
    assert sorting.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_missing_location_defaults_to_none() -> None:
    """An event without any location field yields ``location=None``."""
    payload = {
        "items": [
            {
                "known": True,
                "state": "IN_TRANSIT",
                "events": [
                    {
                        "date": "2026-07-15",
                        "time": "09:12:00",
                        "key": "IN_TRANSIT",
                        "description": {"en": "In transit"},
                    }
                ],
            }
        ]
    }
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890")

    assert status.is_delivered is False
    assert status.latest_event.location is None
    assert status.latest_event.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_location_from_alternate_key() -> None:
    """A facility exposed under ``activityLocation`` is still captured."""
    payload = {
        "items": [
            {
                "known": True,
                "state": "IN_TRANSIT",
                "events": [
                    {
                        "date": "2026-07-15",
                        "time": "09:12:00",
                        "key": "PROCESSED",
                        "description": "Processed",
                        "activityLocation": "ANTWERPEN X",
                    }
                ],
            }
        ]
    }
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890")

    assert status.latest_event.location == "ANTWERPEN X"


@pytest.mark.asyncio
@respx.mock
async def test_unknown_item_raises_courier_error() -> None:
    """An unknown item raises :class:`CourierError`."""
    payload = {"items": [{"known": False, "events": []}]}
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking("000000000000000000000000")


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_courier_error() -> None:
    """A non-2xx HTTP response is surfaced as :class:`CourierError`."""
    respx.get(BPOST_URL).mock(return_value=httpx.Response(503))

    async with BpostAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking("323212345678901234567890")
