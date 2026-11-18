"""Client for the Mijn Energiedirect customer portal.

This module has no Home Assistant dependencies so it can be exercised from a
plain Python script (see ``scripts/test_login.py``).

The portal (https://mijn.energiedirect.nl) is a single page app with a
backend-for-frontend: the browser logs in through Auth0 (auth.energiedirect.nl),
the portal backend keeps the tokens and hands the browser HTTP-only session
cookies. All data calls go to ``https://mijn.energiedirect.nl/api/...`` with
those cookies, and ``/auth/refresh/`` renews the short-lived access token.

The Auth0 login page always requires a captcha, so this client does not log in
itself. The user logs in once in a browser and hands over the session cookies;
the client keeps that session alive with ``/auth/refresh/`` and exposes the
(possibly rotated) cookies so they can be stored.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

PORTAL_URL = URL("https://mijn.energiedirect.nl")
COOKIE_DOMAIN = "energiedirect.nl"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)


class EnergiedirectError(Exception):
    """Base error."""


class EnergiedirectConnectionError(EnergiedirectError):
    """Network problem or unexpected response."""


class EnergiedirectAuthError(EnergiedirectError):
    """The browser session is missing, invalid or expired."""


class EnergiedirectApiChangedError(EnergiedirectError):
    """A response no longer has the structure the integration expects."""


def parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse a pasted ``Cookie`` request header (``a=1; b=2``)."""
    raw = raw.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]
    cookies: dict[str, str] = {}
    for part in raw.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


class EnergiedirectClient:
    """Async client for the Mijn Energiedirect portal, using a browser session."""

    def __init__(self, cookies: str, session: aiohttp.ClientSession | None = None) -> None:
        self._own_session = session is None
        self._session = session or self.create_session()
        parsed = parse_cookie_string(cookies)
        if not parsed:
            raise EnergiedirectAuthError("No cookies found in the pasted text")
        self._session.cookie_jar.update_cookies(parsed, PORTAL_URL)
        self.account_id: str | None = None

    @staticmethod
    def create_session() -> aiohttp.ClientSession:
        """Session with its own cookie jar (the login lives in cookies)."""
        return aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(),
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

    async def close(self, force: bool = False) -> None:
        """Close the session if we created it (or when forced)."""
        if self._own_session or force:
            await self._session.close()

    def export_cookies(self) -> str:
        """Current portal cookies as a ``Cookie`` header string, for storage."""
        cookies: dict[str, str] = {}
        for morsel in self._session.cookie_jar:
            domain = (morsel["domain"] or "").lstrip(".")
            if not domain or domain.endswith(COOKIE_DOMAIN):
                cookies[morsel.key] = morsel.value
        return "; ".join(f"{k}={v}" for k, v in sorted(cookies.items()))

    # ------------------------------------------------------------------ auth

    async def refresh(self) -> bool:
        """Refresh the access token. Returns False when the session is gone."""
        try:
            async with self._session.get(
                PORTAL_URL / "auth" / "refresh" / "", allow_redirects=False
            ) as resp:
                _LOGGER.debug("Session refresh: HTTP %s", resp.status)
                return resp.status == 200
        except aiohttp.ClientError as err:
            raise EnergiedirectConnectionError(f"Session refresh failed: {err}") from err

    async def _load_account(self) -> None:
        try:
            async with self._session.get(
                PORTAL_URL / "auth" / "userinfo", allow_redirects=False
            ) as resp:
                if resp.status != 200:
                    raise EnergiedirectAuthError(f"userinfo returned HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise EnergiedirectConnectionError(f"userinfo failed: {err}") from err
        except ValueError as err:
            raise EnergiedirectAuthError("userinfo did not return JSON") from err
        if not data.get("authenticated"):
            raise EnergiedirectAuthError("The browser session is not logged in")
        self.account_id = (data.get("userinfo") or {}).get("accountId")
        if not self.account_id:
            raise EnergiedirectConnectionError("No accountId in userinfo")

    async def ensure_session(self) -> None:
        """Renew the access token; raise EnergiedirectAuthError if the session expired."""
        if not await self.refresh():
            raise EnergiedirectAuthError(
                "The Energiedirect session expired; log in in a browser and paste new cookies"
            )
        if not self.account_id:
            await self._load_account()

    # ------------------------------------------------------------------ data

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = PORTAL_URL.with_path(path)
        for attempt in range(2):
            try:
                async with self._session.get(
                    url, params=params, headers={"Accept": "application/json"},
                    allow_redirects=False,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        _LOGGER.debug("401 on %s, refreshing session", path)
                        await self.ensure_session()
                        continue
                    if resp.status == 401:
                        raise EnergiedirectAuthError(f"{path}: unauthorized")
                    if resp.status != 200:
                        raise EnergiedirectConnectionError(f"{path} returned HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise EnergiedirectConnectionError(f"{path} failed: {err}") from err
        raise EnergiedirectAuthError(f"{path}: unauthorized")

    async def get_supply_addresses(self) -> list[dict[str, Any]]:
        today = date.today()
        return await self._get(
            "/api/contracts-middleware/supplyaddresses/v3",
            {
                "account_id": self.account_id,
                "contract_status": "all",
                "from_date": (today - timedelta(days=90)).isoformat(),
                "to_date": today.isoformat(),
            },
        )

    async def get_prices(self, contract_group_id: str, contract_id: str) -> dict[str, Any]:
        return await self._get(
            "/api/billing/prices/v1",
            {
                "account_id": self.account_id,
                "contract_group_id": contract_group_id,
                "contract_id": contract_id,
            },
        )

    async def get_consumption(self, supply_address_id: str, contract_group_id: str) -> dict[str, Any]:
        return await self._get(
            "/api/energy-insights/consumption-data/v2",
            {"supplyAddressId": supply_address_id, "contractGroupId": contract_group_id},
        )

    async def get_budget_bill(self, supply_address_id: str, contract_group_id: str) -> dict[str, Any]:
        return await self._get(
            "/api/eci/getBudgetBillInsightsV1",
            {"premiseId": supply_address_id, "contractGroupId": contract_group_id},
        )


# --------------------------------------------------------------- public data

DYNAMIC_PRICES_URL = URL(
    "https://www.energiedirect.nl/api/public/dynamicpricing/dynamic-prices/v1"
)


async def fetch_dynamic_prices(session: aiohttp.ClientSession) -> dict[str, Any]:
    """Public dynamic (hourly) prices for yesterday, today and tomorrow. No login needed."""
    try:
        async with session.get(
            DYNAMIC_PRICES_URL,
            # The site's proxy rejects public API calls without this header (HTTP 401).
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "x-request-origin": "client",
            },
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise EnergiedirectConnectionError(f"dynamic prices returned HTTP {resp.status}")
            return await resp.json(content_type=None)
    except aiohttp.ClientError as err:
        raise EnergiedirectConnectionError(f"dynamic prices failed: {err}") from err
