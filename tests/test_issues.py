"""Repair issues for changed API responses. Need pytest-homeassistant-custom-component."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402

from custom_components.energiedirect import parsers as p  # noqa: E402
from custom_components.energiedirect.const import DOMAIN  # noqa: E402
from custom_components.energiedirect.issues import (  # noqa: E402
    ENDPOINT_NAMES,
    ApiShapeMonitor,
    issue_id,
)

TRANSLATIONS = Path(__file__).resolve().parent.parent / "custom_components/energiedirect/translations"


async def test_issue_created_and_cleared(hass: HomeAssistant) -> None:
    monitor = ApiShapeMonitor(hass)
    registry = ir.async_get(hass)

    assert not monitor.check(p.ENDPOINT_BUDGET_BILL, {"plan": {}}, p.check_budget_bill)
    monitor.sync()
    issue = registry.async_get_issue(DOMAIN, issue_id(p.ENDPOINT_BUDGET_BILL))
    assert issue is not None
    assert issue.translation_key == "api_changed_budget_bill"
    assert issue.translation_placeholders["detail"] == "no budgetBillPlan, expectedYearCosts"

    # An update that did not reach this endpoint leaves the issue alone.
    monitor.sync()
    assert registry.async_get_issue(DOMAIN, issue_id(p.ENDPOINT_BUDGET_BILL)) is not None

    good = {"budgetBillPlan": {}, "expectedYearCosts": {}}
    assert monitor.check(p.ENDPOINT_BUDGET_BILL, good, p.check_budget_bill)
    monitor.sync()
    assert registry.async_get_issue(DOMAIN, issue_id(p.ENDPOINT_BUDGET_BILL)) is None


def test_every_endpoint_has_a_translated_issue():
    for path in TRANSLATIONS.glob("*.json"):
        issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
        assert set(issues) == {issue_id(e) for e in ENDPOINT_NAMES}, path.name
        for text in issues.values():
            assert "{detail}" in text["description"] and "{issue_url}" in text["description"]
