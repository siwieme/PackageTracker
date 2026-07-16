"""Abstract base class and shared utilities for courier adapters.

Every courier adapter converts a raw courier response into the uniform
:class:`~core.models.PackageStatus` model. Shared plumbing (HTTP client
lifecycle, defensive lookups, status normalization) lives here so concrete
adapters stay small and focused on the courier's specific payload shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Mapping, Optional

import httpx

from core.models import PackageStatus, StatusCode


class CourierError(Exception):
    """Raised when a courier response cannot be retrieved or parsed."""


class BaseCourierAdapter(ABC):
    """Abstract base for all courier adapters.

    Concrete subclasses implement :meth:`fetch_tracking` and set
    :attr:`courier_name`. An adapter can either own its own
    :class:`httpx.AsyncClient` (created lazily and closed on exit) or receive
    a shared client for connection reuse.

    Attributes:
        courier_name: Stable lowercase identifier for the courier (e.g. "bpost").
        timeout: Default request timeout in seconds.

    Example:
        >>> async with BpostAdapter() as adapter:
        ...     status = await adapter.fetch_tracking("323212345678901234567890")
    """

    courier_name: str = "unknown"
    timeout: float = 10.0

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        """Initialize the adapter.

        Args:
            client: Optional shared ``httpx.AsyncClient``. When omitted, the
                adapter creates and manages its own client.
        """
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "BaseCourierAdapter":
        """Enter the async context, creating an owned client if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit the async context, closing the client if this adapter owns it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the active client, creating an owned one on first use.

        Returns:
            The ``httpx.AsyncClient`` used for outbound requests.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self._client

    @abstractmethod
    async def fetch_tracking(
        self,
        tracking_number: str,
        postal_code: Optional[str] = None,
    ) -> PackageStatus:
        """Fetch and normalize tracking information for a parcel.

        Args:
            tracking_number: The courier tracking number to look up.
            postal_code: Optional destination postal code. Required by some
                couriers (e.g. bpost registered mail) to authenticate the
                lookup. Ignored when not needed.

        Returns:
            A normalized :class:`~core.models.PackageStatus`.

        Raises:
            CourierError: If the parcel is unknown or the response is unparseable.
        """
        raise NotImplementedError

    # -- Shared helpers -----------------------------------------------------

    @staticmethod
    def _first_present(
        mapping: Mapping[str, Any], keys: Iterable[str]
    ) -> Optional[Any]:
        """Return the first non-empty value among ``keys`` in ``mapping``.

        Couriers expose the same concept (e.g. a facility name) under different
        keys across endpoints and payload versions. This checks each candidate
        key in order and returns the first truthy value, or ``None``.

        Args:
            mapping: The dictionary to inspect.
            keys: Candidate keys, in priority order.

        Returns:
            The first non-empty value found, otherwise ``None``.
        """
        if not isinstance(mapping, Mapping):
            return None
        for key in keys:
            value = mapping.get(key)
            if value not in (None, "", {}, []):
                return value
        return None

    @staticmethod
    def _normalize_status(
        raw: Optional[str],
        *,
        delivered_tokens: Iterable[str] = ("DELIVER",),
        exception_tokens: Iterable[str] = (
            "PROBLEM",
            "EXCEPTION",
            "RETURN",
            "REFUSED",
            "FAILED",
            "UNDELIVER",
            "NOT_DELIVERED",
            "CUSTOMS",
            "ERROR",
        ),
    ) -> StatusCode:
        """Map a courier-specific status token to a standardized status code.

        Args:
            raw: The courier's status/event key (case-insensitive), or ``None``.
            delivered_tokens: Substrings that indicate a delivered state.
            exception_tokens: Substrings that indicate an exception state.

        Returns:
            The corresponding :class:`~core.models.StatusCode`. Falls back to
            ``TRANSIT`` when nothing else matches.
        """
        token = (raw or "").upper()
        if any(t in token for t in delivered_tokens):
            return StatusCode.DELIVERED
        if any(t in token for t in exception_tokens):
            return StatusCode.EXCEPTION
        return StatusCode.TRANSIT
