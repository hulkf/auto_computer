"""AI browser operation layer tests that do not require launching a browser."""

from core.ai_browser import infer_action, rank_candidates


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
