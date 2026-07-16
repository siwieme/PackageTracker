"""Entry point: CLI wrapper around the router and adapters.

Usage:
    python -m main <tracking_number> [<tracking_number> ...] [--postal-code CODE]

Detects the courier for each tracking number, fetches its status, and prints
the normalized :class:`~core.models.PackageStatus` as JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List, Optional

from adapters.base import CourierError
from core.models import PackageStatus
from core.router import CourierRouter


async def track(
    tracking_number: str,
    postal_code: Optional[str] = None,
) -> PackageStatus:
    """Detect the courier and fetch the normalized status for one parcel.

    Args:
        tracking_number: The tracking number to look up.
        postal_code: Optional destination postal code forwarded to the adapter.

    Returns:
        The normalized :class:`~core.models.PackageStatus`.

    Raises:
        ValueError: If no courier matches the tracking number.
        CourierError: If the courier lookup fails.
    """
    router = CourierRouter()
    async with router.get_adapter(tracking_number) as adapter:
        return await adapter.fetch_tracking(tracking_number, postal_code=postal_code)


async def _run(tracking_numbers: List[str], postal_code: Optional[str]) -> int:
    exit_code = 0
    for number in tracking_numbers:
        try:
            status = await track(number, postal_code=postal_code)
            print(status.model_dump_json(indent=2))
        except (ValueError, CourierError) as exc:
            print(f"[error] {number}: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Track parcels across bpost, PostNL, and DHL."
    )
    parser.add_argument("tracking_numbers", nargs="+", metavar="NUMBER")
    parser.add_argument(
        "-p",
        "--postal-code",
        metavar="CODE",
        default=None,
        help="Destination postal code (required by some couriers for certain parcel types).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.tracking_numbers, args.postal_code)))


if __name__ == "__main__":
    main()
