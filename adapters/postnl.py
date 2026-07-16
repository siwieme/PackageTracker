"""PostNL courier adapter.

Normalizes PostNL's ``colli``/``observations`` payload into the uniform model,
extracting the facility name from the various location keys PostNL uses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from adapters.base import BaseCourierAdapter, CourierError
from core.models import PackageStatus, TrackingEvent

_LOCATION_KEYS = (
    "locationName",
    "locationDescription",
    "location",
    "depot",
    "name",
)


class PostNLAdapter(BaseCourierAdapter):
    """Adapter for PostNL parcel tracking.

    Attributes:
        courier_name: Always ``"postnl"``.
        BASE_URL: The PostNL track-and-trace API endpoint.
    """

    courier_name = "postnl"
    BASE_URL = "https://jouw.postnl.be/track-and-trace/api/trackAndTrace"

    async def fetch_tracking(self, tracking_number: str) -> PackageStatus:
        """Fetch and normalize PostNL tracking data.

        Args:
            tracking_number: The PostNL barcode.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If the request fails or the payload cannot be parsed.
        """
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/{tracking_number}",
                params={"language": "en"},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CourierError(
                f"postnl lookup failed for {tracking_number}: {exc}"
            ) from exc

        return self._parse(tracking_number, payload)

    def _parse(self, tracking_number: str, payload: Mapping[str, Any]) -> PackageStatus:
        """Map a raw PostNL payload onto :class:`PackageStatus`.

        Args:
            tracking_number: The queried barcode.
            payload: The decoded JSON body from PostNL.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If no colli or observations are present.
        """
        colli = payload.get("colli") or {}
        if isinstance(colli, Mapping) and colli:
            # Match the queried barcode when present, else take the first collo.
            parcel = colli.get(tracking_number) or next(iter(colli.values()))
        elif isinstance(colli, list) and colli:
            parcel = colli[0]
        else:
            raise CourierError(f"postnl returned no colli for {tracking_number}")

        raw_events = parcel.get("observations") or parcel.get("events") or []
        events = [self._parse_event(ev) for ev in raw_events]
        events = [ev for ev in events if ev is not None]
        if not events:
            raise CourierError(f"postnl returned no observations for {tracking_number}")

        events.sort(key=lambda e: e.timestamp)
        latest = events[-1]

        is_delivered = bool(parcel.get("isDelivered")) or (
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
        """Convert a single PostNL observation into a :class:`TrackingEvent`.

        Args:
            ev: A single raw observation mapping.

        Returns:
            The parsed :class:`TrackingEvent`, or ``None`` if it lacks a
            usable timestamp.
        """
        raw_ts = ev.get("observationDate") or ev.get("timestamp") or ev.get("date")
        if not raw_ts:
            return None
        try:
            timestamp = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            return None

        code = ev.get("code") or ev.get("statusCode") or ev.get("description") or ""
        location = self._first_present(ev, _LOCATION_KEYS)

        return TrackingEvent(
            timestamp=timestamp,
            status_code=self._normalize_status(code),
            description=str(ev.get("description") or ev.get("status") or ""),
            location=str(location) if location else None,
        )
