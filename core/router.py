"""Regex-based courier detection.

Given a bare tracking number, :class:`CourierRouter` guesses the courier by
matching against known number formats and hands back the appropriate adapter.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Pattern, Tuple, Type

from adapters.base import BaseCourierAdapter
from adapters.bpost import BpostAdapter
from adapters.dhl import DHLAdapter
from adapters.postnl import PostNLAdapter

# Ordered registry: the first courier whose pattern matches wins. Order matters
# because some formats (e.g. bare S10 codes) overlap; more specific patterns and
# more common couriers are listed first.
_REGISTRY: List[Tuple[str, Type[BaseCourierAdapter], List[Pattern[str]]]] = [
    (
        "bpost",
        BpostAdapter,
        [
            re.compile(r"^3232\d{20}$"),          # 24 digits, bpost prefix 3232
            re.compile(r"^\d{24}$"),               # generic 24-digit bpost barcode
            re.compile(r"^[A-Z]{2}\d{9}BE$", re.I),  # S10 international, Belgium
        ],
    ),
    (
        "postnl",
        PostNLAdapter,
        [
            re.compile(r"^3S[A-Z0-9]{2,4}\d{6,}$", re.I),  # PostNL domestic 3S...
            re.compile(r"^[A-Z]{2}\d{9}NL$", re.I),         # S10 international, NL
            re.compile(r"^KG[A-Z0-9]{9,}$", re.I),          # PostNL parcel variant
        ],
    ),
    (
        "dhl",
        DHLAdapter,
        [
            re.compile(r"^JVGL[A-Z0-9]{10,}$", re.I),  # DHL Parcel Benelux
            re.compile(r"^JJD\d{10,}$", re.I),          # DHL Express / eCommerce
            re.compile(r"^3S?DHL[A-Z0-9]+$", re.I),     # DHL-branded barcode
            re.compile(r"^\d{10}$"),                     # DHL Express 10-digit
        ],
    ),
]


class CourierRouter:
    """Detect couriers from tracking-number formats and build adapters."""

    def __init__(self) -> None:
        """Initialize the router with the built-in courier registry."""
        self._registry = _REGISTRY

    @staticmethod
    def _normalize(tracking_number: str) -> str:
        """Strip spaces/hyphens and uppercase for consistent matching.

        Args:
            tracking_number: The raw, possibly formatted tracking number.

        Returns:
            The normalized tracking number.
        """
        return re.sub(r"[\s-]", "", tracking_number or "").upper()

    def detect(self, tracking_number: str) -> Optional[str]:
        """Detect the courier for a tracking number.

        Args:
            tracking_number: The tracking number to classify.

        Returns:
            The courier name (e.g. "bpost"), or ``None`` if unrecognized.
        """
        cleaned = self._normalize(tracking_number)
        if not cleaned:
            return None
        for name, _adapter_cls, patterns in self._registry:
            if any(pattern.match(cleaned) for pattern in patterns):
                return name
        return None

    def get_adapter(self, tracking_number: str, **kwargs: object) -> BaseCourierAdapter:
        """Return an adapter instance for a tracking number.

        Args:
            tracking_number: The tracking number to route.
            **kwargs: Forwarded to the adapter constructor (e.g. a shared client).

        Returns:
            An instantiated :class:`~adapters.base.BaseCourierAdapter`.

        Raises:
            ValueError: If no courier matches the tracking number.
        """
        name = self.detect(tracking_number)
        if name is None:
            raise ValueError(
                f"Could not detect a courier for tracking number '{tracking_number}'"
            )
        adapter_cls = self._adapter_for(name)
        return adapter_cls(**kwargs)  # type: ignore[arg-type]

    def get_adapter_by_courier(
        self, courier_name: str, **kwargs: object
    ) -> BaseCourierAdapter:
        """Return an adapter for an explicitly named courier.

        Args:
            courier_name: One of the registered courier identifiers (e.g. "bpost").
            **kwargs: Forwarded to the adapter constructor.

        Raises:
            ValueError: If the courier name is not registered.
        """
        try:
            adapter_cls = self._adapter_for(courier_name.lower())
        except KeyError:
            valid = ", ".join(name for name, _, _ in self._registry)
            raise ValueError(
                f"Onbekende vervoerder '{courier_name}'. Geldige opties: {valid}"
            )
        return adapter_cls(**kwargs)  # type: ignore[arg-type]

    def _adapter_for(self, courier_name: str) -> Type[BaseCourierAdapter]:
        """Return the adapter class registered for a courier name.

        Args:
            courier_name: The courier identifier.

        Returns:
            The adapter class.

        Raises:
            KeyError: If the courier name is not registered.
        """
        mapping: Dict[str, Type[BaseCourierAdapter]] = {
            name: cls for name, cls, _ in self._registry
        }
        return mapping[courier_name]
