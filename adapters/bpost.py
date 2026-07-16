"""Bpost courier adapter.

Parses the bpost tracking payload into the uniform data model, with particular
care taken to extract the explicit facility/depot name (e.g. "LOKEREN X"),
which bpost exposes inconsistently under keys such as ``name``,
``locationName`` or ``activityLocation`` depending on the event and endpoint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from adapters.base import BaseCourierAdapter, CourierError
from core.models import PackageStatus, TrackingEvent

# Candidate keys under which bpost exposes a human-readable facility name.
_LOCATION_KEYS = ("name", "locationName", "activityLocation", "municipality", "label")

# Preferred language order when bpost returns multilingual description objects.
_LANG_ORDER = ("en", "nl", "fr", "de")


class BpostAdapter(BaseCourierAdapter):
    """Adapter for bpost parcel tracking.

    Attributes:
        courier_name: Always ``"bpost"``.
        BASE_URL: The bpost tracking items endpoint.
    """

    courier_name = "bpost"
    BASE_URL = "https://track.bpost.cloud/track/items"

    async def fetch_tracking(self, tracking_number: str) -> PackageStatus:
        """Fetch and normalize bpost tracking data.

        Args:
            tracking_number: The bpost item identifier.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If the item is unknown, has no events, or the
                response cannot be parsed.
        """
        try:
            response = await self.client.get(
                self.BASE_URL,
                params={"itemIdentifier": tracking_number, "lang": "en"},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # network, HTTP status, or JSON decode failure
            raise CourierError(
                f"bpost lookup failed for {tracking_number}: {exc}"
            ) from exc

        return self._parse(tracking_number, payload)

    # -- Parsing ------------------------------------------------------------

    def _parse(self, tracking_number: str, payload: Mapping[str, Any]) -> PackageStatus:
        """Map a raw bpost payload onto :class:`PackageStatus`.

        Args:
            tracking_number: The queried item identifier.
            payload: The decoded JSON body from bpost.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If no known item or no events are present.
        """
        items = payload.get("items") or []
        if not items:
            raise CourierError(f"bpost returned no items for {tracking_number}")

        item = items[0]
        if item.get("known") is False:
            raise CourierError(f"bpost does not know item {tracking_number}")

        raw_events = item.get("events") or []
        events = [self._parse_event(ev) for ev in raw_events]
        events = [ev for ev in events if ev is not None]
        if not events:
            raise CourierError(f"bpost returned no events for {tracking_number}")

        events.sort(key=lambda e: e.timestamp)
        latest = events[-1]

        # Prefer the courier's own top-level state, fall back to the latest event.
        state = (item.get("state") or "").upper()
        is_delivered = "DELIVER" in state or latest.status_code.value == "DELIVERED"

        return PackageStatus(
            tracking_number=tracking_number,
            courier=self.courier_name,
            is_delivered=is_delivered,
            latest_event=latest,
            history=events,
        )

    def _parse_event(self, ev: Mapping[str, Any]) -> Optional[TrackingEvent]:
        """Convert a single bpost event into a :class:`TrackingEvent`.

        Robust to missing fields: an event without a resolvable timestamp is
        skipped (returns ``None``); a missing location gracefully defaults to
        ``None`` rather than halting parsing.

        Args:
            ev: A single raw event mapping from the bpost payload.

        Returns:
            The parsed :class:`TrackingEvent`, or ``None`` if it lacks a usable
            timestamp.
        """
        timestamp = self._parse_timestamp(ev)
        if timestamp is None:
            return None

        key = ev.get("key") or ev.get("code") or ""
        return TrackingEvent(
            timestamp=timestamp,
            status_code=self._normalize_status(key),
            description=self._parse_description(ev),
            location=self._parse_location(ev),
        )

    @staticmethod
    def _parse_timestamp(ev: Mapping[str, Any]) -> Optional[datetime]:
        """Extract a datetime from a bpost event.

        Accepts either a combined ISO ``datetime`` field, or separate
        ``date`` and ``time`` fields.

        Args:
            ev: A raw event mapping.

        Returns:
            A parsed :class:`datetime`, or ``None`` if none could be built.
        """
        combined = ev.get("datetime") or ev.get("timestamp")
        if combined:
            try:
                return datetime.fromisoformat(str(combined).replace("Z", "+00:00"))
            except ValueError:
                return None

        date = ev.get("date")
        if not date:
            return None
        time = ev.get("time") or "00:00:00"
        try:
            return datetime.fromisoformat(f"{date}T{time}")
        except ValueError:
            return None

    @staticmethod
    def _parse_description(ev: Mapping[str, Any]) -> str:
        """Extract a human-readable description, preferring English.

        Args:
            ev: A raw event mapping.

        Returns:
            The original courier description string (may be empty).
        """
        desc = ev.get("description")
        if isinstance(desc, Mapping):
            for lang in _LANG_ORDER:
                if desc.get(lang):
                    return str(desc[lang])
            # Fall back to any available translation.
            for value in desc.values():
                if value:
                    return str(value)
            return ""
        return str(desc) if desc else ""

    @classmethod
    def _parse_location(cls, ev: Mapping[str, Any]) -> Optional[str]:
        """Extract the explicit facility/depot name from a bpost event.

        Checks a nested ``location`` object first (the common case), then the
        event root, across the several keys bpost is known to use. Missing
        locations gracefully default to ``None``.

        Args:
            ev: A raw event mapping.

        Returns:
            The facility name (e.g. "LOKEREN X"), or ``None``.
        """
        location = ev.get("location")
        if isinstance(location, str) and location:
            return location
        if isinstance(location, Mapping):
            name = cls._first_present(location, _LOCATION_KEYS)
            if name:
                return str(name)
        # Some payloads flatten the location onto the event root.
        name = cls._first_present(ev, _LOCATION_KEYS)
        return str(name) if name else None
