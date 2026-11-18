"""Check Energiedirect's live APIs as far as possible without logging in.

Run daily by .github/workflows/live.yml. Needs only aiohttp (and tzdata on Windows):

    python scripts/check_live.py

Checks:
- dynamic prices: the public endpoint still has the structure the integration parses, and
  has prices for today for electricity, gas and feed-in;
- the login layer: /auth/userinfo and /auth/refresh/ still exist and say "not logged in";
- the data API gateway still answers with its JSON 401 for requests without a session.

The account data endpoints themselves can't be checked without a session: the portal answers
401 for every /api path when you are not logged in, including paths that don't exist.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "energiedirect"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"ed_{name}", PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load("api")
parsers = _load("parsers")

TZ = ZoneInfo("Europe/Amsterdam")
HEADERS = {"User-Agent": api.USER_AGENT, "Accept": "application/json"}


async def check_dynamic_prices(session: aiohttp.ClientSession) -> str:
    raw = await api.fetch_dynamic_prices(session)
    if problem := parsers.check_dynamic_prices(raw):
        raise AssertionError(f"structure changed: {problem}")
    prices = parsers.parse_dynamic_prices(raw, TZ)
    today = datetime.now(TZ).date()
    counts = {}
    for kind in (parsers.DYNAMIC_ELECTRICITY, parsers.DYNAMIC_GAS, parsers.DYNAMIC_FEED_IN):
        slots = parsers.slots_on(prices.get(kind, []), today)
        if len(slots) < 24:
            raise AssertionError(f"only {len(slots)} {kind} prices for {today}")
        counts[kind] = len(slots)
    current = parsers.slot_at(prices[parsers.DYNAMIC_ELECTRICITY], datetime.now(TZ))
    if current is None:
        raise AssertionError("no electricity price for the current time")
    return f"{counts} slots today, electricity now €{current.price:.5f}/kWh"


async def check_userinfo(session: aiohttp.ClientSession) -> str:
    async with session.get(api.PORTAL_URL / "auth" / "userinfo", headers=HEADERS) as resp:
        body = await resp.json(content_type=None)
        if resp.status not in (401, 403) or body.get("authenticated") is not False:
            raise AssertionError(f"HTTP {resp.status}: {body}")
        return f"HTTP {resp.status}, authenticated=false"


async def check_refresh(session: aiohttp.ClientSession) -> str:
    async with session.get(
        api.PORTAL_URL / "auth" / "refresh" / "", headers=HEADERS, allow_redirects=False
    ) as resp:
        if resp.status not in (401, 403):
            raise AssertionError(f"expected 401/403 without a session, got HTTP {resp.status}")
        return f"HTTP {resp.status}"


async def check_api_gateway(session: aiohttp.ClientSession) -> str:
    url = api.PORTAL_URL / "api" / "billing" / "prices" / "v1"
    async with session.get(url, headers=HEADERS, allow_redirects=False) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 401 or body.get("statusCode") != 401:
            raise AssertionError(f"HTTP {resp.status}: {str(body)[:200]}")
        return "HTTP 401 Unauthorized (JSON)"


CHECKS: list[tuple[str, Callable[[aiohttp.ClientSession], Awaitable[str]]]] = [
    ("Dynamic prices", check_dynamic_prices),
    ("Login: /auth/userinfo", check_userinfo),
    ("Login: /auth/refresh/", check_refresh),
    ("Data API gateway", check_api_gateway),
]


async def main() -> int:
    rows = []
    failed = False
    async with aiohttp.ClientSession(timeout=api.TIMEOUT) as session:
        for name, check in CHECKS:
            try:
                rows.append((name, "✅", await check(session)))
            except Exception as err:  # noqa: BLE001 - report every failure
                failed = True
                rows.append((name, "❌", f"{type(err).__name__}: {err}"))

    lines = ["| Check | Result | Details |", "| --- | --- | --- |"]
    lines += [f"| {name} | {mark} | {detail} |" for name, mark, detail in rows]
    report = "\n".join(lines)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Energiedirect live check\n\n" + report + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
