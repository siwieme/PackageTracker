"""Tests for the DHL adapter, mocking all outbound HTTP with respx.

DHL wraps shipments in a top-level ``shipments`` list. Each shipment has an
``events`` list, and each event nests location under a ``location`` object that
may contain a ``name``/``servicePoint`` key directly or an ``address`` dict with
``addressLocality``. Tests cover: full happy path, is_delivered from
``status.statusCode``, all location extraction paths (name, servicePoint,
address locality, string location, None), exception status, skipped events
without timestamps, empty shipments/events, HTTP errors, and the optional
``DHL-API-Key`` header.
"""

import httpx
import pytest
import respx

from adapters.base import CourierError
from adapters.dhl import DHLAdapter
from core.models import StatusCode

TRACKING_NUMBER = "JVGL0123456789AB"
URL = DHLAdapter.BASE_URL


def _shipment_payload(
    events: list,
    *,
    status_code: str = "transit",
) -> dict:
    """Build a minimal DHL payload containing one shipment."""
    return {
        "shipments": [
            {
                "id": TRACKING_NUMBER,
                "status": {"statusCode": status_code},
                "events": events,
            }
        ]
    }


@pytest.mark.asyncio
@respx.mock
async def test_maps_payload_to_model() -> None:
    """Raw DHL JSON maps onto PackageStatus with the correct field values."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-14T10:00:00Z",
                "statusCode": "transit",
                "description": "Shipment picked up",
                "location": {"name": "BERLIN HUB"},
            },
            {
                "timestamp": "2026-07-16T14:30:00Z",
                "statusCode": "delivered",
                "description": "Delivered - Signed by RECIPIENT",
                "location": {"name": "BRUSSELS 1"},
            },
        ],
        status_code="delivered",
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.courier == "dhl"
    assert status.tracking_number == TRACKING_NUMBER
    assert status.is_delivered is True
    assert len(status.history) == 2

    # History is sorted chronologically.
    assert status.history[0].timestamp.isoformat() == "2026-07-14T10:00:00+00:00"
    assert status.history[1].timestamp.isoformat() == "2026-07-16T14:30:00+00:00"

    latest = status.latest_event
    assert latest.status_code == StatusCode.DELIVERED
    assert latest.description == "Delivered - Signed by RECIPIENT"
    assert latest.location == "BRUSSELS 1"

    first = status.history[0]
    assert first.location == "BERLIN HUB"
    assert first.status_code == StatusCode.TRANSIT


@pytest.mark.asyncio
@respx.mock
async def test_is_delivered_from_status_code_field() -> None:
    """is_delivered is True when shipment.status.statusCode contains 'DELIVER'."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-16T14:00:00Z",
                "statusCode": "transit",
                "description": "In transit",
            }
        ],
        status_code="DELIVERED",
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.is_delivered is True


@pytest.mark.asyncio
@respx.mock
async def test_location_from_service_point_key() -> None:
    """``servicePoint`` is used as the facility name when ``name`` is absent."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "At service point",
                "location": {"servicePoint": "ANTWERP SP"},
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.latest_event.location == "ANTWERP SP"


@pytest.mark.asyncio
@respx.mock
async def test_location_from_address_locality() -> None:
    """Falls back to ``addressLocality`` when no facility name is present."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "In transit",
                "location": {
                    "address": {
                        "addressLocality": "Liège",
                        "countryCode": "BE",
                    }
                },
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.latest_event.location == "Liège"


@pytest.mark.asyncio
@respx.mock
async def test_location_as_string() -> None:
    """A plain string ``location`` value is used directly."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "In transit",
                "location": "GHENT DEPOT",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.latest_event.location == "GHENT DEPOT"


@pytest.mark.asyncio
@respx.mock
async def test_missing_location_defaults_to_none() -> None:
    """An event without a location field yields location=None."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "In transit",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.latest_event.location is None


@pytest.mark.asyncio
@respx.mock
async def test_exception_status_normalization() -> None:
    """Event status codes containing 'RETURN' normalize to EXCEPTION."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "return-to-sender",
                "description": "Returned to sender",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.latest_event.status_code == StatusCode.EXCEPTION
    assert status.is_delivered is False


@pytest.mark.asyncio
@respx.mock
async def test_event_without_timestamp_is_skipped() -> None:
    """Events without a timestamp are silently dropped."""
    payload = _shipment_payload(
        [
            {
                "statusCode": "transit",
                "description": "No timestamp — should be skipped",
            },
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "Valid event",
            },
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert len(status.history) == 1
    assert status.latest_event.description == "Valid event"


@pytest.mark.asyncio
@respx.mock
async def test_description_falls_back_to_status_field() -> None:
    """When ``description`` is absent, ``status`` is used as the description."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "status": "In transit via hub",
            }
        ]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        status = await adapter.fetch_tracking(TRACKING_NUMBER)

    assert status.latest_event.description == "In transit via hub"


@pytest.mark.asyncio
@respx.mock
async def test_api_key_sent_in_header() -> None:
    """The DHL-API-Key header is attached when an api_key is provided."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "In transit",
            }
        ]
    )

    route = respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter(api_key="test-key-abc") as adapter:
        await adapter.fetch_tracking(TRACKING_NUMBER)

    assert route.called
    sent_headers = route.calls[0].request.headers
    assert sent_headers.get("dhl-api-key") == "test-key-abc"


@pytest.mark.asyncio
@respx.mock
async def test_no_api_key_omits_header() -> None:
    """The DHL-API-Key header is absent when no api_key is provided."""
    payload = _shipment_payload(
        [
            {
                "timestamp": "2026-07-15T09:00:00Z",
                "statusCode": "transit",
                "description": "In transit",
            }
        ]
    )

    route = respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        await adapter.fetch_tracking(TRACKING_NUMBER)

    assert "dhl-api-key" not in route.calls[0].request.headers


@pytest.mark.asyncio
@respx.mock
async def test_empty_shipments_raises_courier_error() -> None:
    """An empty shipments array raises CourierError."""
    respx.get(URL).mock(return_value=httpx.Response(200, json={"shipments": []}))

    async with DHLAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking(TRACKING_NUMBER)


@pytest.mark.asyncio
@respx.mock
async def test_all_events_without_timestamps_raises_courier_error() -> None:
    """When every event is skipped, CourierError is raised (no valid events)."""
    payload = _shipment_payload(
        [{"statusCode": "transit", "description": "No timestamp"}]
    )
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload))

    async with DHLAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking(TRACKING_NUMBER)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_courier_error() -> None:
    """A non-2xx HTTP status raises CourierError."""
    respx.get(URL).mock(return_value=httpx.Response(401))

    async with DHLAdapter() as adapter:
        with pytest.raises(CourierError):
            await adapter.fetch_tracking(TRACKING_NUMBER)
