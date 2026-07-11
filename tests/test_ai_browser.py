"""AI browser operation layer tests that do not require launching a browser."""

import asyncio
from pathlib import Path

import pytest

from core.ai_browser import infer_action, rank_candidates, selector_suggestions
from core.playwright_base import BrowserDiagnosisError, PlaywrightBase


def test_rank_candidates_prefers_searchbox_for_chinese_search_intent() -> None:
    candidates = [
        {"tag": "a", "role": "link", "text": "新闻", "type": ""},
        {"tag": "input", "role": "searchbox", "placeholder": "搜索", "text": "", "type": "search"},
    ]

    ranked = rank_candidates("在搜索框输入关键词并搜索", candidates)

    assert ranked[0]["role"] == "searchbox"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_infer_action_search_with_value_submits() -> None:
    candidate = {"tag": "input", "role": "searchbox"}

    assert infer_action("在搜索框输入关键词并搜索", candidate, value="Stagehand") == "fill_and_enter"


def test_infer_action_uses_click_for_button() -> None:
    candidate = {"tag": "button", "role": "button"}

    assert infer_action("点击提交按钮", candidate) == "click"


def test_selector_suggestions_prioritize_stable_playwright_locators() -> None:
    candidate = {
        "role": "button",
        "aria_label": "提交订单",
        "text": "提交",
        "test_id": "submit-order",
        "id": "submit",
        "name": "submit",
    }

    suggestions = selector_suggestions(candidate)

    assert suggestions[0] == "page.get_by_role('button', name='提交订单')"
    assert "page.get_by_test_id('submit-order')" in suggestions


def test_fixed_operation_does_not_start_diagnosis_on_success(monkeypatch) -> None:
    automation = PlaywrightBase.__new__(PlaywrightBase)
    monkeypatch.setattr(
        automation,
        "_ai_operator",
        lambda: pytest.fail("successful fixed operation must not start diagnosis"),
    )

    async def operation() -> str:
        return "ok"

    value = asyncio.run(
        automation.fixed_operation(
            operation,
            intent="点击提交按钮",
            current_locator="get_by_role('button', name='提交')",
        )
    )

    assert value == "ok"


def test_fixed_operation_collects_candidates_after_failure(monkeypatch, tmp_path) -> None:
    automation = PlaywrightBase.__new__(PlaywrightBase)
    automation.task_id = "failed-task"

    class FakeOperator:
        ai_enabled = False

        async def observe(self, instruction: str, *, limit: int, use_ai: bool):
            assert use_ai is False
            return [{"suggested_locators": ["page.get_by_role('button', name='提交')"]}]

    monkeypatch.setattr(automation, "_ai_operator", lambda: FakeOperator())
    monkeypatch.setattr(
        "core.playwright_base.save_snapshot",
        lambda namespace, key, payload: Path(tmp_path / "diagnosis.json"),
    )

    async def failing_operation() -> None:
        raise RuntimeError("old selector timed out")

    with pytest.raises(BrowserDiagnosisError) as captured:
        asyncio.run(
            automation.fixed_operation(
                failing_operation,
                intent="点击提交按钮",
                current_locator="#old-submit",
            )
        )

    diagnosis = captured.value.diagnosis
    assert diagnosis["failed_locator"] == "#old-submit"
    assert diagnosis["ai_used"] is False
    assert diagnosis["candidates"][0]["suggested_locators"]
