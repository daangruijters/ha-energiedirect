"""Repair issues for when Energiedirect changes the structure of its (undocumented) APIs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from . import parsers as p
from .const import DOMAIN, ISSUE_TRACKER_URL

_LOGGER = logging.getLogger(__name__)

# For the log message; the repair texts are translated per endpoint.
ENDPOINT_NAMES = {
    p.ENDPOINT_SUPPLY_ADDRESSES: "addresses and contracts",
    p.ENDPOINT_TARIFFS: "contracted tariffs",
    p.ENDPOINT_CONSUMPTION: "usage and costs",
    p.ENDPOINT_BUDGET_BILL: "budget bill",
    p.ENDPOINT_DYNAMIC_PRICES: "dynamic prices",
}


def issue_id(endpoint: str) -> str:
    return f"api_changed_{endpoint}"


class ApiShapeMonitor:
    """Collects shape problems during one update, then raises or clears repair issues."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._checked: set[str] = set()
        self._problems: dict[str, str] = {}
        self._active: set[str] = set()

    def check(self, endpoint: str, data: Any, checker: Callable[[Any], str | None]) -> bool:
        """Record whether ``data`` has the expected shape. Returns True when it does."""
        self._checked.add(endpoint)
        problem = checker(data)
        if problem:
            self._problems.setdefault(endpoint, problem)
        return problem is None

    def sync(self) -> None:
        """Create issues for endpoints that changed, remove them for endpoints that are fine."""
        for endpoint in self._checked:
            problem = self._problems.get(endpoint)
            if problem:
                if endpoint not in self._active:
                    _LOGGER.warning(
                        "Energiedirect changed the %s data (%s); please report this at %s",
                        ENDPOINT_NAMES[endpoint],
                        problem,
                        ISSUE_TRACKER_URL,
                    )
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id(endpoint),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=issue_id(endpoint),
                    translation_placeholders={
                        "detail": problem,
                        "issue_url": ISSUE_TRACKER_URL,
                    },
                    learn_more_url=ISSUE_TRACKER_URL,
                )
                self._active.add(endpoint)
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id(endpoint))
                self._active.discard(endpoint)
        self._checked.clear()
        self._problems.clear()
