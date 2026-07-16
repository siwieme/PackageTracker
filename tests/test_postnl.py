"""Tests for the PostNL adapter, mocking all outbound HTTP with respx.

PostNL returns a ``colli`` dict keyed by barcode, each collo containing an
``observations`` list. Tests cover: dict colli keyed by barcode, dict colli
without the exact barcode (falls back to first value), list colli, the
``isDelivered`` flag, all three location-key variants, missing location → None,
exception status normalization, missing timestamp skipped, empty colli, and HTTP
error.
"""

import httpx
import pytest
import respx

from adapters.base import CourierError
from adapters.postnl import PostNLAdapter
from core.models import StatusCode

BARCODE = "3SABC123456789"
URL = f"{PostNLAdapter.BASE_URL}/{BARCODE}"


def _colli_payload(observations: list, *, is_delivered: bool = False) -> dict:
    """Build a standard PostNL payload keyed by barcode."""
    return {
        "colli": {
            BARCODE: {
                "isDelivered": is_delivered,
                "observations": observations,
            }
        }
    }


@pytest.mark.asyncio
@respx.mock
async def test_maps_payload_to_model() -> None:
    """Raw PostNL JSON maps onto PackageStatus with the correct field values."""
    payload = _colli_payload(
        [
            {
                "observationDate": "2026-07-14T08:00:00",
                "code": "ANNOUNCED",
                "description": "Shipment announced",
                "locationName": "AMSTERDAM DC",
            },
            {
                "observationDate": "2026-07-16T14:00:00",
                "code": "DELIVERED",
                "description": "Delivered to recipient",
                "locationName": "ROTTERDAM 1",
            },
        ],
        is_delivered=True,
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.courier == "postnl"
    assert status.tracking_number == BARCODE
    assert status.is_delivered is True
    assert len(status.history) == 2

    # History is sorted chronologically, oldest first.
    assert status.history[0].timestamp.isoformat() == "2026-07-14T08:00:00"
    assert status.history[1].timestamp.isoformat() == "2026-07-16T14:00:00"

    latest = status.latest_event
    assert latest.status_code == StatusCode.DELIVERED
    assert latest.description == "Delivered to recipient"
    assert latest.location == "ROTTERDAM 1"

    first = status.history[0]
    assert first.status_code == StatusCode.TRANSIT
    assert first.location == "AMSTERDAM DC"


@pytest.mark.asyncio
@respx.mock
async def test_is_delivered_from_latest_event() -> None:
    """is_delivered falls back to the latest event's status when isDelivered is absent."""
    payload = {
        "colli": {
            BARCODE: {
                "observations": [
                    {
                        "observationDate": "2026-07-16T14:00:00",
                        "code": "DELIVERED",
                        "description": "Delivered",
                    }
                ]
            }
        }
    }
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.is_delivered is True


@pytest.mark.asyncio
@respx.mock
async def test_colli_falls_back_to_first_value_when_barcode_absent() -> None:
    """When the queried barcode is not a key in colli, the first collo is used."""
    payload = {
        "colli": {
            "OTHER_BARCODE": {
                "isDelivered": False,
                "observations": [
                    {
                        "observationDate": "2026-07-15T10:00:00",
                        "code": "TRANSIT",
                        "description": "In transit",
                    }
                ],
            }
        }
    }
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_colli_as_list() -> None:
    """A colli array (instead of dict) is handled gracefully."""
    payload = {
        "colli": [
            {
                "isDelivered": False,
                "observations": [
                    {
                        "observationDate": "2026-07-15T10:00:00",
                        "code": "TRANSIT",
                        "description": "In transit",
                    }
                ],
            }
        ]
    }
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.courier == "postnl"
    assert status.latest_event.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_location_from_location_description_key() -> None:
    """``locationDescription`` is picked up as the facility name."""
    payload = _colli_payload(
        [
            {
                "observationDate": "2026-07-15T09:00:00",
                "code": "TRANSIT",
                "description": "At depot",
                "locationDescription": "DEN HAAG DEPOT",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.location == "DEN HAAG DEPOT"


@pytest.mark.asyncio
@respx.mock
async def test_location_from_depot_key() -> None:
    """``depot`` is picked up as the facility name when higher-priority keys absent."""
    payload = _colli_payload(
        [
            {
                "observationDate": "2026-07-15T09:00:00",
                "code": "TRANSIT",
                "description": "Sorted",
                "depot": "EINDHOVEN X",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.location == "EINDHOVEN X"


@pytest.mark.asyncio
@respx.mock
async def test_missing_location_defaults_to_none() -> None:
    """An observation without any location key yields location=None."""
    payload = _colli_payload(
        [
            {
                "observationDate": "2026-07-15T09:00:00",
                "code": "TRANSIT",
                "description": "In transit",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.location is None


@pytest.mark.asyncio
@respx.mock
async def test_exception_status_code() -> None:
    """Event codes containing 'RETURN' normalize to EXCEPTION."""
    payload = _colli_payload(
        [
            {
                "observationDate": "2026-07-15T09:00:00",
                "code": "RETURN_TO_SENDER",
                "description": "Returned to sender",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.status_code == StatusCode.EXCEPTION
    assert status.is_delivered is False


@pytest.mark.asyncio
@respx.mock
async def test_event_without_timestamp_is_skipped() -> None:
    """Observations without a timestamp are silently dropped."""
    payload = _colli_payload(
        [
            {
                "code": "TRANSIT",
                "description": "No timestamp event",
            },
            {
                "observationDate": "2026-07-15T09:00:00",
                "code": "TRANSIT",
                "description": "Valid event",
            },
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert len(status.history) == 1
    assert status.latest_event.description == "Valid event"


@pytest.mark.asyncio
@respx.mock
async def test_uses_events_key_as_fallback() -> None:
    """``events`` is accepted when ``observations`` is absent."""
    payload = {
        "colli": {
            BARCODE: {
                "isDelivered": False,
                "events": [
                    {
                        "observationDate": "2026-07-15T09:00:00",
                        "code": "TRANSIT",
                        "description": "In transit",
                    }
                ],
            }
        }
    }
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.description == "In transit"


@pytest.mark.asyncio
@respx.mock
async def test_timestamp_from_timestamp_key() -> None:
    """``timestamp`` is accepted when ``observationDate`` is absent."""
    payload = _colli_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "code": "TRANSIT",
                "description": "In transit",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with PostNLAdapter() as adapter:
        status = await adapter.fetch_tracking(BARCODE)

    assert status.latest_event.timestamp.isoformat() == "2026-07-15T09:00:00+00:00"


@pytest.mark.asyncio
@respx.mock
async def test_empty_colli_raises_courier_error() -> None:
    """A payload with no colli raises CourierError."""
    respx.get(URL).mock(return_value=httpx.Response(200, json={"colli": {}}))

    async with PostNLAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking(BARCODE)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_courier_error() -> None:
    """A non-2xx HTTP status raises CourierError."""
    respx.get(URL).mock(return_value=httpx.Response(404))

    async with PostNLAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking(BARCODE)
