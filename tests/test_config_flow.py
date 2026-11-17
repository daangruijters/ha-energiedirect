"""Config flow tests. Need pytest-homeassistant-custom-component; skipped otherwise."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.energiedirect.const import (  # noqa: E402
    CONF_COOKIES,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_PRICES,
)

FLOW = "custom_components.energiedirect.config_flow"


@pytest.fixture(autouse=True)
def _custom(recorder_mock, enable_custom_integrations):
    """The integration depends on recorder (for the statistics import)."""
    yield


@pytest.fixture(autouse=True)
def _no_setup():
    with patch("custom_components.energiedirect.async_setup_entry", return_value=True):
        yield


def _validate(account_id="123", errors=None):
    return patch(
        f"{FLOW}.EnergiedirectConfigFlow._validate",
        AsyncMock(return_value=(account_id, "a=1", errors or {})),
    )


async def test_menu_and_dynamic_prices(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [ENTRY_TYPE_PRICES, ENTRY_TYPE_ACCOUNT]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": ENTRY_TYPE_PRICES}
    )
    assert result["type"] is FlowResultType.FORM
    with patch(f"{FLOW}.fetch_dynamic_prices", AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_PRICES}

    # Only one dynamic prices entry.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": ENTRY_TYPE_PRICES}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_account(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": ENTRY_TYPE_ACCOUNT}
    )
    with _validate(errors={"base": "invalid_session"}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: "x=1"}
        )
    assert result["errors"] == {"base": "invalid_session"}
    with _validate():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: "x=1"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "123"
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT, CONF_COOKIES: "a=1"}


async def test_reconfigure_account(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="123", data={CONF_COOKIES: "old=1"})
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with _validate(account_id="999"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: "x=1"}
        )
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_COOKIES] == "old=1"

    result = await entry.start_reconfigure_flow(hass)
    with _validate():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: "x=1"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_COOKIES] == "a=1"


async def test_reconfigure_prices_aborts(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ENTRY_TYPE_PRICES, data={CONF_ENTRY_TYPE: ENTRY_TYPE_PRICES}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_configure"


async def test_reauth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="123", data={CONF_COOKIES: "old=1"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with _validate():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: "x=1"}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_COOKIES] == "a=1"
