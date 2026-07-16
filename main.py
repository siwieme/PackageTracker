"""Entry point: a small CLI wrapper around the router and adapters.

Usage:
    python -m main <tracking_number> [<tracking_number> ...]

Detects the courier for each tracking number, fetches its status, and prints
the normalized :class:`~core.models.PackageStatus` as JSON.
"""

from __future__ import annotations

import asyncio
import sys
from typing import List

from adapters.base import CourierError
from core.models import PackageStatus
from core.router import CourierRouter


async def track(tracking_number: str) -> PackageStatus:
    """Detect the courier and fetch the normalized status for one parcel.

    Args:
        tracking_number: The tracking number to look up.

    Returns:
        The normalized :class:`~core.models.PackageStatus`.

    Raises:
        ValueError: If no courier matches the tracking number.
        CourierError: If the courier lookup fails.
    """
    router = CourierRouter()
    async with router.get_adapter(tracking_number) as adapter:
        return await adapter.fetch_tracking(tracking_number)


async def _run(tracking_numbers: List[str]) -> int:
    """Track each number and print the result, returning a process exit code.

    Args:
        tracking_numbers: Tracking numbers to look up.

    Returns:
        ``0`` if every lookup succeeded, ``1`` otherwise.
    """
    exit_code = 0
    for number in tracking_numbers:
        try:
            status = await track(number)
            print(status.model_dump_json(indent=2))
        except (ValueError, CourierError) as exc:
            print(f"[error] {number}: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


def main() -> None:
    """CLI entry point."""
    args = sys.argv[1:]
    if not args:
        print("Usage: python -m main <tracking_number> [...]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
