"""Tests for the bpost adapter, mocking all outbound HTTP with respx.

Payloads mirror the real bpost API structure observed in production:
- ``key`` is a multilingual dict with ``{LANG: {description: "..."}}``
- location is nested as ``{locationName: "..."}``
- delivery state comes from ``shipmentDeliveryStatus`` (bool)
- timestamps use ``date`` + ``time`` ("HH:MM", no seconds)
"""

from typing import Optional

import httpx
import pytest
import respx

from adapters.base import CourierError
from adapters.bpost import BpostAdapter
from core.models import StatusCode

BPOST_URL = BpostAdapter.BASE_URL


def _event(date: str, time: str, description_en: str, location_name: Optional[str] = None, *, irregularity: bool = False) -> dict:
    """Build a realistic bpost event dict."""
    ev: dict = {
        "date": date,
        "time": time,
        "key": {"EN": {"description": description_en}, "NL": {"description": description_en}},
        "irregularity": irregularity,
    }
    if location_name:
        ev["location"] = {"locationName": location_name}
    return ev


def _delivered_payload() -> dict:
    """Realistic bpost payload for a delivered parcel."""
    return {
        "items": [
            {
                "itemCode": "323212345678901234567890",
                "shipmentDeliveryStatus": True,
                "activeStep": {"knownProcessStep": "DELIVERED_IN_MAILBOX"},
                "events": [
                    _event("2026-07-13", "11:15", "Confirmation of preparation of the shipment received", "LCI"),
                    _event("2026-07-14", "00:41", "Your item has been sorted", "PSM AX BEUMER"),
                    _event("2026-07-14", "08:27", "Shipment being prepared by the postman", "LOKEREN X"),
                    _event("2026-07-14", "09:24", "Item in distribution phase", "LOKEREN X"),
                    _event("2026-07-14", "13:41", "Item delivered in mailbox", "LOKEREN MAIL"),
                ],
            }
        ]
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_maps_payload_to_model() -> None:
    """Real bpost JSON maps onto PackageStatus with correct fields."""
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=_delivered_payload()))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890", postal_code="9160")

    assert status.courier == "bpost"
    assert status.tracking_number == "323212345678901234567890"
    assert status.is_delivered is True
    assert len(status.history) == 5

    # History is sorted chronologically.
    assert status.history[0].timestamp.isoformat() == "2026-07-13T11:15:00"
    assert status.history[-1].timestamp.isoformat() == "2026-07-14T13:41:00"

    latest = status.latest_event
    assert latest.status_code == StatusCode.DELIVERED
    assert latest.description == "Item delivered in mailbox"
    assert latest.location == "LOKEREN MAIL"


@pytest.mark.asyncio
@respx.mock
async def test_location_name_extracted() -> None:
    """Facility name is extracted from the nested location.locationName field."""
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=_delivered_payload()))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890", postal_code="9160")

    sorting = next(e for e in status.history if "sorted" in e.description)
    assert sorting.location == "PSM AX BEUMER"
    assert sorting.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_description_from_multilingual_key() -> None:
    """Description is extracted from key[EN][description]."""
    payload = {
        "items": [{
            "shipmentDeliveryStatus": False,
            "events": [_event("2026-07-15", "09:00", "Your item has been sorted", "ANTWERPEN X")],
        }]
    }
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890", postal_code="9000")

    assert status.latest_event.description == "Your item has been sorted"


@pytest.mark.asyncio
@respx.mock
async def test_missing_location_defaults_to_none() -> None:
    """An event without a location field yields location=None."""
    payload = {
        "items": [{
            "shipmentDeliveryStatus": False,
            "events": [_event("2026-07-15", "09:00", "Item in distribution phase")],
        }]
    }
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890", postal_code="9000")

    assert status.latest_event.location is None
    assert status.latest_event.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_irregularity_flag_maps_to_exception() -> None:
    """An event with irregularity=True maps to EXCEPTION."""
    payload = {
        "items": [{
            "shipmentDeliveryStatus": False,
            "events": [_event("2026-07-15", "09:00", "Delivery failed", irregularity=True)],
        }]
    }
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890", postal_code="9000")

    assert status.latest_event.status_code == StatusCode.EXCEPTION


@pytest.mark.asyncio
@respx.mock
async def test_is_delivered_from_active_step_fallback() -> None:
    """is_delivered is True when activeStep.knownProcessStep contains DELIVER."""
    payload = {
        "items": [{
            "shipmentDeliveryStatus": False,
            "activeStep": {"knownProcessStep": "DELIVERED_AT_HOME"},
            "events": [_event("2026-07-16", "14:00", "Item delivered at home", "GENT 1")],
        }]
    }
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json=payload))

    async with BpostAdapter() as adapter:
        status = await adapter.fetch_tracking("323212345678901234567890", postal_code="9000")

    assert status.is_delivered is True


@pytest.mark.asyncio
@respx.mock
async def test_no_data_found_gives_helpful_error() -> None:
    """NO_DATA_FOUND error gives a message about the missing postal code."""
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json={"error": "NO_DATA_FOUND"}))

    async with BpostAdapter() as adapter:
        with pytest.raises(CourierError, match="postal code"):
            await adapter.fetch_tracking("323212345678901234567890")


@pytest.mark.asyncio
@respx.mock
async def test_other_api_error_raises_courier_error() -> None:
    """An unexpected error code raises CourierError."""
    respx.get(BPOST_URL).mock(return_value=httpx.Response(200, json={"error": "UNKNOWN_ERROR"}))

    async with BpostAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking("323212345678901234567890")


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_courier_error() -> None:
    """A non-2xx HTTP response is surfaced as CourierError."""
    respx.get(BPOST_URL).mock(return_value=httpx.Response(503))

    async with BpostAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking("323212345678901234567890")
