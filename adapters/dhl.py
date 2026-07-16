"""DHL courier adapter.

Normalizes the DHL unified tracking payload (``shipments`` / ``events``) into
the uniform model. DHL nests location under ``location.address`` with keys such
as ``addressLocality`` and, for facilities, ``servicePoint``/``name``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from adapters.base import BaseCourierAdapter, CourierError
from core.models import PackageStatus, TrackingEvent

# Keys checked directly on a location object.
_LOCATION_KEYS = ("name", "servicePoint", "label")
# Keys checked on the nested address object.
_ADDRESS_KEYS = ("addressLocality", "streetAddress", "postalCode", "countryCode")


class DHLAdapter(BaseCourierAdapter):
    """Adapter for DHL parcel tracking.

    Attributes:
        courier_name: Always ``"dhl"``.
        BASE_URL: The DHL unified shipment tracking endpoint.
    """

    courier_name = "dhl"
    BASE_URL = "https://api-eu.dhl.com/track/shipments"

    def __init__(self, *args: Any, api_key: Optional[str] = None, **kwargs: Any) -> None:
        """Initialize the DHL adapter.

        Args:
            api_key: Optional DHL API key sent as the ``DHL-API-Key`` header.
            *args: Forwarded to :class:`~adapters.base.BaseCourierAdapter`.
            **kwargs: Forwarded to :class:`~adapters.base.BaseCourierAdapter`.
        """
        super().__init__(*args, **kwargs)
        self.api_key = api_key

    async def fetch_tracking(
        self,
        tracking_number: str,
        postal_code: Optional[str] = None,
    ) -> PackageStatus:
        """Fetch and normalize DHL tracking data.

        Args:
            tracking_number: The DHL tracking number.
            postal_code: Optional recipient postal code, forwarded to the DHL
                API when provided.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If the request fails or the payload cannot be parsed.
        """
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["DHL-API-Key"] = self.api_key
        params: dict = {"trackingNumber": tracking_number}
        if postal_code:
            params["postalCode"] = postal_code
        try:
            response = await self.client.get(
                self.BASE_URL,
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CourierError(
                f"dhl lookup failed for {tracking_number}: {exc}"
            ) from exc

        return self._parse(tracking_number, payload)

    def _parse(self, tracking_number: str, payload: Mapping[str, Any]) -> PackageStatus:
        """Map a raw DHL payload onto :class:`PackageStatus`.

        Args:
            tracking_number: The queried tracking number.
            payload: The decoded JSON body from DHL.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If no shipments or events are present.
        """
        shipments = payload.get("shipments") or []
        if not shipments:
            raise CourierError(f"dhl returned no shipments for {tracking_number}")

        shipment = shipments[0]
        raw_events = shipment.get("events") or []
        events = [self._parse_event(ev) for ev in raw_events]
        events = [ev for ev in events if ev is not None]
        if not events:
            raise CourierError(f"dhl returned no events for {tracking_number}")

        events.sort(key=lambda e: e.timestamp)
        latest = events[-1]

        status_code = (shipment.get("status") or {}).get("statusCode", "")
        is_delivered = "DELIVER" in str(status_code).upper() or (
            latest.status_code.value == "DELIVERED"
        )

        return PackageStatus(
            tracking_number=tracking_number,
            courier=self.courier_name,
            is_delivered=is_delivered,
            latest_event=latest,
            history=events,
        )

    def _parse_event(self, ev: Mapping[str, Any]) -> Optional[TrackingEvent]:
        """Convert a single DHL event into a :class:`TrackingEvent`.

        Args:
            ev: A single raw event mapping.

        Returns:
            The parsed :class:`TrackingEvent`, or ``None`` if it lacks a
            usable timestamp.
        """
        raw_ts = ev.get("timestamp") or ev.get("date")
        if not raw_ts:
            return None
        try:
            timestamp = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            return None

        code = ev.get("statusCode") or ev.get("status") or ev.get("description") or ""
        return TrackingEvent(
            timestamp=timestamp,
            status_code=self._normalize_status(code),
            description=str(ev.get("description") or ev.get("status") or ""),
            location=self._parse_location(ev),
        )

    @classmethod
    def _parse_location(cls, ev: Mapping[str, Any]) -> Optional[str]:
        """Extract the facility name from a DHL event.

        Prefers an explicit facility/service-point name, then falls back to the
        address locality. Missing locations gracefully default to ``None``.

        Args:
            ev: A raw event mapping.

        Returns:
            The facility or locality name, or ``None``.
        """
        location = ev.get("location")
        if isinstance(location, str) and location:
            return location
        if not isinstance(location, Mapping):
            return None

        name = cls._first_present(location, _LOCATION_KEYS)
        if name:
            return str(name)

        address = location.get("address")
        if isinstance(address, Mapping):
            locality = cls._first_present(address, _ADDRESS_KEYS)
            if locality:
                return str(locality)
        return None
