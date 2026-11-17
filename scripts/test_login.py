"""Try the Energiedirect client outside Home Assistant.

Usage:
    python scripts/test_login.py             # uses COOKIE=... from .env
    python scripts/test_login.py --debug     # also logs each request
    python scripts/test_login.py --dump dump # also saves the raw JSON responses

The Energiedirect login page requires a captcha, so this uses the session
cookies of a browser login: put the browser's Cookie request header in .env as
COOKIE=... . The refreshed cookies are written back to .env after each run.
Cookies are never printed. Raw dumps contain personal data: don't commit them.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "energiedirect"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"ed_{name}", PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load("api")
parsers = _load("parsers")


def _dump(folder: Path | None, name: str, data) -> None:
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=value lines, optional quotes, # comments."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


ENV_FILE = PKG.parent.parent / ".env"
COOKIE_KEYS = ("COOKIE", "ENERGIEDIRECT_COOKIES")


def _cookies() -> str:
    """Session cookies from .env (COOKIE=...) or the ENERGIEDIRECT_COOKIES env var."""
    env_file = _read_env_file(ENV_FILE)
    for key in COOKIE_KEYS:
        if env_file.get(key):
            return env_file[key]
    if os.environ.get("ENERGIEDIRECT_COOKIES"):
        return os.environ["ENERGIEDIRECT_COOKIES"]
    # Alternative: the individual cookie values, as copied from DevTools > Application > Cookies.
    parts = {
        "access_token": env_file.get("ACCESS_TOKEN"),
        "id_token": env_file.get("ID_TOKEN"),
        "refresh_token": env_file.get("REFRESH_TOKEN"),
        "user_id": env_file.get("USER_ID"),
    }
    if all(parts.values()):
        return "; ".join(f"{name}-0={value}; {name}-length=1" for name, value in parts.items())
    sys.exit(
        "No session cookies found. Add a line COOKIE=<cookie header> to .env "
        "(see the README for how to copy it from your browser)."
    )


def _save_cookies(cookies: str) -> None:
    """Write refreshed cookies back to .env so the next run continues the session."""
    if not ENV_FILE.is_file() or not cookies:
        return
    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in COOKIE_KEYS:
            lines[i] = f"{key}={cookies}"
            ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    lines.append(f"COOKIE={cookies}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main(args: argparse.Namespace) -> int:
    dump = Path(args.dump) if args.dump else None

    client = api.EnergiedirectClient(_cookies())
    try:
        print("\n== Session")
        try:
            await client.ensure_session()
        except api.EnergiedirectError as err:
            print(f"FAILED: {type(err).__name__}: {err}")
            return 1
        print(f"OK, session valid (account {client.account_id[:3]}...)")

        addresses = await client.get_supply_addresses()
        _dump(dump, "supplyaddresses", addresses)
        active = [a for a in addresses if a.get("contract_status") == "active"] or addresses
        print(f"\n== Supply addresses: {len(addresses)} ({len(active)} active)")

        for address in active:
            address_id = address["supply_address_id"]
            print(f"\n-- Address {address.get('street')} {address.get('house_number')}")
            connections = address.get("connections") or []
            group_id = None
            for conn in connections:
                contract = conn.get("contract") or {}
                group_id = group_id or contract.get("contract_group_id")
                raw = await client.get_prices(contract["contract_group_id"], contract["id"])
                _dump(dump, f"prices_{conn['energy_type']}", raw)
                for t in parsers.parse_tariffs(raw):
                    print(f"\n   Tariffs {t.energy_type} ({t.proposition}, {t.contract_type}), per {t.unit}:")
                    for key, value in t.values.items():
                        print(f"     {key:28} {value}")

            if not group_id:
                continue
            raw = await client.get_consumption(address_id, group_id)
            _dump(dump, "consumption", raw)
            for energy_type, cons in parsers.parse_consumption(raw).items():
                print(f"\n   Usage {energy_type}: {len(cons.hourly)} hours, {len(cons.daily)} days")
                if cons.hourly:
                    print(f"     hourly range   {cons.hourly[0].start} .. {cons.hourly[-1].start}")
                if cons.daily:
                    last = cons.daily[-1]
                    print(
                        f"     last day       {last.start.date()}: used {last.consumption}, "
                        f"returned {last.returned}, cost EUR {last.cost}"
                    )
                mtd = parsers.month_to_date(cons.daily, date.today())
                if mtd:
                    print(f"     month to date  used {mtd.consumption}, cost EUR {mtd.cost}")

            try:
                raw = await client.get_budget_bill(address_id, group_id)
                _dump(dump, "budgetbill", raw)
                print("\n   Budget bill:")
                for key, value in parsers.parse_budget_bill(raw).items():
                    print(f"     {key:28} {value}")
            except api.EnergiedirectError as err:
                print(f"\n   Budget bill not available: {err}")

        print("\n== Session refresh:", "OK" if await client.refresh() else "FAILED")
        return 0
    finally:
        _save_cookies(client.export_cookies())
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--debug", action="store_true", help="log every login step")
    parser.add_argument("--dump", metavar="DIR", help="save raw JSON responses to DIR")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.debug:
        logging.getLogger("asyncio").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main(args)))
