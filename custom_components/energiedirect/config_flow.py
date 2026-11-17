"""Config flow for Energiedirect.

Two tiers:
- Dynamic prices: public data, no login. One entry at most.
- Account: the Energiedirect login page always asks for a captcha, so the user
  logs in in a browser and pastes the session cookies here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    EnergiedirectAuthError,
    EnergiedirectClient,
    EnergiedirectError,
    fetch_dynamic_prices,
)
from .const import (
    CONF_COOKIES,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_PRICES,
    PRICES_UNIQUE_ID,
)

_LOGGER = logging.getLogger(__name__)

PLACEHOLDERS = {"portal_url": "https://mijn.energiedirect.nl"}

COOKIE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COOKIES): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
        ),
    }
)


class EnergiedirectConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, cookies: str) -> tuple[str | None, str, dict[str, str]]:
        """Check the session. Returns (account id, refreshed cookies, errors)."""
        # A short-lived session of our own: closing a session from HA's helper is not allowed.
        try:
            client = EnergiedirectClient(cookies)
        except EnergiedirectAuthError:
            return None, cookies, {"base": "no_cookies"}
        try:
            await client.ensure_session()
            return client.account_id, client.export_cookies(), {}
        except EnergiedirectAuthError:
            return None, cookies, {"base": "invalid_session"}
        except EnergiedirectError:
            _LOGGER.exception("Cannot connect to Energiedirect")
            return None, cookies, {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error checking the Energiedirect session")
            return None, cookies, {"base": "unknown"}
        finally:
            await client.close()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=[ENTRY_TYPE_PRICES, ENTRY_TYPE_ACCOUNT]
        )

    async def async_step_dynamic_prices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(PRICES_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await fetch_dynamic_prices(async_get_clientsession(self.hass))
            except EnergiedirectError:
                _LOGGER.exception("Cannot fetch Energiedirect dynamic prices")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Energiedirect dynamic prices",
                    data={CONF_ENTRY_TYPE: ENTRY_TYPE_PRICES},
                )
        return self.async_show_form(
            step_id=ENTRY_TYPE_PRICES, data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account_id, cookies, errors = await self._validate(user_input[CONF_COOKIES])
            if not errors:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Energiedirect ({account_id})",
                    data={CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT, CONF_COOKIES: cookies},
                )
        return self.async_show_form(
            step_id=ENTRY_TYPE_ACCOUNT,
            data_schema=COOKIE_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._replace_cookies(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a new session for an account entry, e.g. after logging in again."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_ACCOUNT) != ENTRY_TYPE_ACCOUNT:
            return self.async_abort(reason="nothing_to_configure")
        return await self._replace_cookies("reconfigure", entry, user_input)

    async def _replace_cookies(
        self, step_id: str, entry: ConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Validate pasted cookies and store them on an existing account entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            account_id, cookies, errors = await self._validate(user_input[CONF_COOKIES])
            if not errors:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_COOKIES: cookies}
                )
        return self.async_show_form(
            step_id=step_id,
            data_schema=COOKIE_SCHEMA,
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )
