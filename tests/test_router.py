"""Tests for regex-based courier detection."""

import pytest

from adapters.bpost import BpostAdapter
from adapters.dhl import DHLAdapter
from adapters.postnl import PostNLAdapter
from core.router import CourierRouter


@pytest.fixture
def router() -> CourierRouter:
    """Provide a fresh :class:`CourierRouter`."""
    return CourierRouter()


@pytest.mark.parametrize(
    "number, expected",
    [
        ("323212345678901234567890", "bpost"),   # 24 digits, 3232 prefix
        ("123456789012345678901234", "bpost"),   # generic 24-digit
        ("CD123456789BE", "bpost"),              # S10 Belgium
        ("3SABC123456789", "postnl"),            # PostNL domestic 3S
        ("RR123456789NL", "postnl"),             # S10 Netherlands
        ("JVGL0123456789", "dhl"),               # DHL Parcel Benelux
        ("JJD0123456789", "dhl"),                # DHL Express/eCommerce
        ("1234567890", "dhl"),                   # DHL Express 10-digit
    ],
)
def test_detect_known_couriers(router: CourierRouter, number: str, expected: str) -> None:
    """Known number formats resolve to the expected courier."""
    assert router.detect(number) == expected


def test_detect_normalizes_spacing(router: CourierRouter) -> None:
    """Spaces and hyphens are ignored during detection."""
    assert router.detect("CD 1234-5678 9BE") == "bpost"


@pytest.mark.parametrize("number", ["", "   ", "hello", "12345", None])
def test_detect_unknown_returns_none(router: CourierRouter, number) -> None:
    """Unrecognized or empty input returns ``None``."""
    assert router.detect(number) is None


@pytest.mark.parametrize(
    "number, adapter_cls",
    [
        ("323212345678901234567890", BpostAdapter),
        ("3SABC123456789", PostNLAdapter),
        ("JVGL0123456789", DHLAdapter),
    ],
)
def test_get_adapter_returns_matching_instance(
    router: CourierRouter, number: str, adapter_cls
) -> None:
    """The router builds an adapter of the correct type."""
    adapter = router.get_adapter(number)
    assert isinstance(adapter, adapter_cls)


def test_get_adapter_unknown_raises(router: CourierRouter) -> None:
    """An unroutable number raises ``ValueError``."""
    with pytest.raises(ValueError):
        router.get_adapter("not-a-real-number")
