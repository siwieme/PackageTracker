"""Bpost courier adapter.

Parses the real bpost tracking payload into the uniform data model.

Key observations from the live API:
- A postal code is required for most parcel types; without it the API returns
  ``{"error": "NO_DATA_FOUND"}`` rather than an HTTP error.
- Events contain no explicit status-code field. Descriptions are nested in a
  multilingual ``key`` dict: ``event["key"]["EN"]["description"]``.
- Delivery state lives on the item as ``shipmentDeliveryStatus`` (bool).
- Facility names are under ``event["location"]["locationName"]``.
- Timestamps use ``date`` ("YYYY-MM-DD") + ``time`` ("HH:MM") — no seconds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from adapters.base import BaseCourierAdapter, CourierError
from core.models import PackageStatus, StatusCode, TrackingEvent

# Priority order for picking a language from a multilingual key dict.
_LANG_ORDER = ("en", "EN", "nl", "NL", "fr", "FR", "de", "DE")

# Keys checked in the nested location object.
_LOCATION_KEYS = ("locationName", "name", "activityLocation", "municipality", "label")


class BpostAdapter(BaseCourierAdapter):
    """Adapter for bpost parcel tracking.

    Attributes:
        courier_name: Always ``"bpost"``.
        BASE_URL: The bpost tracking items endpoint.
    """

    courier_name = "bpost"
    BASE_URL = "https://track.bpost.cloud/track/items"

    async def fetch_tracking(
        self,
        tracking_number: str,
        postal_code: Optional[str] = None,
    ) -> PackageStatus:
        """Fetch and normalize bpost tracking data.

        Args:
            tracking_number: The bpost item identifier.
            postal_code: Destination postal code. Required by bpost for most
                parcel types. Without it the API returns NO_DATA_FOUND.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If the item is unknown, postal code is missing, or
                the response cannot be parsed.
        """
        params: dict = {"itemIdentifier": tracking_number, "lang": "en"}
        if postal_code:
            params["postalCode"] = postal_code
        try:
            response = await self.client.get(
                self.BASE_URL,
                params=params,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
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
            CourierError: If the payload signals an error or has no events.
        """
        if "error" in payload:
            code = payload["error"]
            if code == "NO_DATA_FOUND":
                raise CourierError(
                    f"bpost: parcel not found for '{tracking_number}'. "
                    "For most parcels a postal code is required — "
                    "add --postal-code <postcode>."
                )
            raise CourierError(f"bpost error: {code}")

        items = payload.get("items") or []
        if not items:
            raise CourierError(f"bpost returned no items for {tracking_number}")

        item = items[0]

        raw_events = item.get("events") or []
        events = [self._parse_event(ev) for ev in raw_events]
        events = [ev for ev in events if ev is not None]
        if not events:
            raise CourierError(f"bpost returned no events for {tracking_number}")

        events.sort(key=lambda e: e.timestamp)
        latest = events[-1]

        # shipmentDeliveryStatus is the reliable delivered flag in real payloads.
        is_delivered = bool(item.get("shipmentDeliveryStatus"))
        if not is_delivered:
            active = (item.get("activeStep") or {}).get("knownProcessStep", "")
            is_delivered = "DELIVER" in str(active).upper()

        return PackageStatus(
            tracking_number=tracking_number,
            courier=self.courier_name,
            is_delivered=is_delivered,
            latest_event=latest,
            history=events,
        )

    def _parse_event(self, ev: Mapping[str, Any]) -> Optional[TrackingEvent]:
        """Convert a single bpost event into a :class:`TrackingEvent`.

        Events without a resolvable timestamp are skipped (returns ``None``).
        Missing locations default to ``None``.

        Args:
            ev: A single raw event mapping from the bpost payload.

        Returns:
            The parsed :class:`TrackingEvent`, or ``None`` if it lacks a
            usable timestamp.
        """
        timestamp = self._parse_timestamp(ev)
        if timestamp is None:
            return None

        description = self._parse_description(ev)

        # The ``irregularity`` flag is the only explicit exception signal.
        if ev.get("irregularity"):
            status_code = StatusCode.EXCEPTION
        else:
            status_code = self._normalize_status(description)

        return TrackingEvent(
            timestamp=timestamp,
            status_code=status_code,
            description=description,
            location=self._parse_location(ev),
        )

    @staticmethod
    def _parse_timestamp(ev: Mapping[str, Any]) -> Optional[datetime]:
        """Extract a datetime from a bpost event.

        The real API provides ``date`` ("YYYY-MM-DD") and ``time`` ("HH:MM")
        as separate fields. A combined ISO ``datetime``/``timestamp`` field is
        also accepted for forward compatibility.

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
                pass

        date = ev.get("date")
        if not date:
            return None
        time = ev.get("time") or "00:00:00"
        # Real API returns "HH:MM" without seconds; fromisoformat on Python <3.11
        # requires seconds, so we pad explicitly.
        if len(time) == 5:
            time = time + ":00"
        try:
            return datetime.fromisoformat(f"{date}T{time}")
        except ValueError:
            return None

    @staticmethod
    def _parse_description(ev: Mapping[str, Any]) -> str:
        """Extract a human-readable description from a bpost event.

        The real bpost API stores descriptions inside the multilingual ``key``
        dict as ``key[LANG]["description"]``. A plain string ``key`` or a
        top-level ``description`` field are accepted as fallbacks.

        Args:
            ev: A raw event mapping.

        Returns:
            The English (or best available) description string.
        """
        key = ev.get("key")
        if isinstance(key, Mapping):
            # Multilingual dict: try each language in preference order.
            for lang in _LANG_ORDER:
                lang_data = key.get(lang)
                if isinstance(lang_data, Mapping):
                    desc = lang_data.get("description")
                    if desc:
                        return str(desc)
            # Last resort: any language that has a description.
            for lang_data in key.values():
                if isinstance(lang_data, Mapping):
                    desc = lang_data.get("description")
                    if desc:
                        return str(desc)
        if isinstance(key, str) and key:
            return key
        return str(ev.get("description") or "")

    @classmethod
    def _parse_location(cls, ev: Mapping[str, Any]) -> Optional[str]:
        """Extract the facility name from a bpost event.

        Checks the nested ``location`` object first, then the event root.

        Args:
            ev: A raw event mapping.

        Returns:
            The facility name (e.g. "LOKEREN MAIL"), or ``None``.
        """
        location = ev.get("location")
        if isinstance(location, str) and location:
            return location
        if isinstance(location, Mapping):
            name = cls._first_present(location, _LOCATION_KEYS)
            if name:
                return str(name)
        name = cls._first_present(ev, _LOCATION_KEYS)
        return str(name) if name else None
