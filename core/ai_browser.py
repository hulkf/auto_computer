"""AI-enhanced browser operations used by PlaywrightBase.

The default behavior is deterministic and local. When an OpenAI-compatible
endpoint is configured, the same API can ask a model to rank element candidates
or extract structured data from page text.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import httpx
from playwright.async_api import Locator, Page

from .common_utils import get_logger


INTERACTIVE_SELECTOR = ",".join(
    [
        "a",
        "button",
        "input",
        "textarea",
        "select",
        "summary",
        "label",
        "[role]",
        "[contenteditable='true']",
    ]
)


def _tokens(text: str) -> list[str]:
    """Split English words and Chinese characters for simple local matching."""

    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def _candidate_text(candidate: dict[str, Any]) -> str:
    fields = [
        candidate.get("text"),
        candidate.get("aria_label"),
        candidate.get("placeholder"),
        candidate.get("name"),
        candidate.get("role"),
        candidate.get("tag"),
        candidate.get("type"),
        candidate.get("href"),
    ]
    return " ".join(str(field) for field in fields if field).lower()


def selector_suggestions(candidate: dict[str, Any]) -> list[str]:
    """Build stable Playwright locator suggestions for permanent source repair."""

    suggestions: list[str] = []
    role = str(candidate.get("role", "")).strip()
    aria_label = str(candidate.get("aria_label", "")).strip()
    text = str(candidate.get("text", "")).strip()
    placeholder = str(candidate.get("placeholder", "")).strip()
    test_id = str(candidate.get("test_id", "")).strip()
    element_id = str(candidate.get("id", "")).strip()
    name = str(candidate.get("name", "")).strip()

    if role and aria_label:
        suggestions.append(f"page.get_by_role({role!r}, name={aria_label!r})")
    elif role and text:
        suggestions.append(f"page.get_by_role({role!r}, name={text[:120]!r})")
    if aria_label:
        suggestions.append(f"page.get_by_label({aria_label!r})")
    if placeholder:
        suggestions.append(f"page.get_by_placeholder({placeholder!r})")
    if test_id:
        suggestions.append(f"page.get_by_test_id({test_id!r})")
    if text:
        suggestions.append(f"page.get_by_text({text[:120]!r}, exact=True)")
    if element_id:
        suggestions.append(f"page.locator({('#' + element_id)!r})")
    if name:
        suggestions.append(f"page.locator({('[name=' + repr(name) + ']')!r})")
    return suggestions


def rank_candidates(instruction: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank visible page candidates without calling a model."""

    lowered = instruction.lower()
    instruction_tokens = _tokens(instruction)
    wants_input = any(word in lowered for word in ("input", "fill", "type", "search", "输入", "填写", "搜索"))
    wants_click = any(word in lowered for word in ("click", "press", "open", "点击", "打开", "选择"))
    ranked: list[dict[str, Any]] = []

    for candidate in candidates:
        haystack = _candidate_text(candidate)
        score = 0.0
        for token in instruction_tokens:
            if token in haystack:
                score += 2.0 if len(token) > 1 else 0.5
        tag = str(candidate.get("tag", "")).lower()
        role = str(candidate.get("role", "")).lower()
        input_type = str(candidate.get("type", "")).lower()
        if wants_input and (tag in {"input", "textarea"} or role in {"textbox", "searchbox"}):
            score += 6.0
        if wants_click and (tag in {"a", "button", "summary", "label"} or role in {"button", "link", "menuitem"}):
            score += 5.0
        if input_type in {"hidden", "submit"}:
            score -= 2.0
        enriched = dict(candidate)
        enriched["score"] = round(score, 3)
        enriched["reason"] = "local heuristic rank"
        enriched["suggested_locators"] = selector_suggestions(candidate)
        ranked.append(enriched)

    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def infer_action(instruction: str, candidate: dict[str, Any], value: str | None = None) -> str:
    """Infer a safe browser action from intent and target metadata."""

    lowered = instruction.lower()
    tag = str(candidate.get("tag", "")).lower()
    role = str(candidate.get("role", "")).lower()
    if "搜索" in lowered or "search" in lowered:
        if tag in {"input", "textarea"} or role in {"textbox", "searchbox"}:
            return "fill_and_enter"
    if value is not None:
        return "fill"
    if any(word in lowered for word in ("input", "fill", "type", "输入", "填写")):
        return "fill"
    if any(word in lowered for word in ("select", "选择")) and tag == "select":
        return "select"
    return "click"


def _extract_quoted_text(instruction: str) -> str | None:
    quoted = re.search(r"[\"'“”‘’](.+?)[\"'“”‘’]", instruction)
    if quoted:
        return quoted.group(1).strip()
    return None


class AIBrowserOperator:
    """Optional AI browser operation layer for a single Playwright page."""

    def __init__(self, page: Page, *, task_id: str | None = None) -> None:
        self.page = page
        self.logger = get_logger(__name__, task_id)
        self.model = os.getenv("AUTOMATION_AI_MODEL", "").strip()
        self.api_key = os.getenv("AUTOMATION_AI_API_KEY", "").strip()
        self.base_url = os.getenv("AUTOMATION_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.timeout_seconds = float(os.getenv("AUTOMATION_AI_TIMEOUT_SECONDS", "30"))

    @property
    def ai_enabled(self) -> bool:
        """Return whether an OpenAI-compatible chat endpoint is configured."""

        return bool(self.model and self.api_key)

    async def observe(
        self,
        instruction: str,
        *,
        limit: int = 10,
        use_ai: bool = True,
    ) -> list[dict[str, Any]]:
        """Find visible page targets that may satisfy the natural-language intent."""

        candidates = await self._collect_candidates(max(limit * 8, 40))
        ranked = rank_candidates(instruction, candidates)
        if use_ai and self.ai_enabled and ranked:
            try:
                ai_ranked = await self._rank_with_ai(instruction, ranked[:40])
                if ai_ranked:
                    return ai_ranked[:limit]
            except Exception:
                self.logger.exception("AI observe ranking failed; falling back to local ranking")
        return ranked[:limit]

    async def act(self, instruction: str, *, value: str | None = None) -> dict[str, Any]:
        """Execute one natural-language page action against the best observed target."""

        observations = await self.observe(instruction, limit=5)
        if not observations:
            raise RuntimeError(f"未找到可执行目标: {instruction}")
        target = observations[0]
        locator = self.page.locator(str(target["selector"])).first
        action = infer_action(instruction, target, value)
        typed_value = value if value is not None else _extract_quoted_text(instruction)

        await self._run_action(locator, action, typed_value)
        return {
            "instruction": instruction,
            "action": action,
            "target": target,
            "ai_enabled": self.ai_enabled,
        }

    async def extract(
        self,
        instruction: str,
        *,
        schema: dict[str, Any] | None = None,
        limit_chars: int = 12000,
    ) -> dict[str, Any]:
        """Extract page data with an optional schema."""

        page_text = await self._page_text(limit_chars)
        if self.ai_enabled:
            try:
                payload = await self._extract_with_ai(instruction, schema, page_text)
                if payload:
                    return payload
            except Exception:
                self.logger.exception("AI extract failed; falling back to local extraction")

        return self._extract_locally(instruction, schema, page_text)

    async def _collect_candidates(self, limit: int) -> list[dict[str, Any]]:
        run_id = uuid.uuid4().hex
        script = """
        ({ selector, limit, runId }) => {
          const nodes = Array.from(document.querySelectorAll(selector));
          const visible = [];
          for (const node of nodes) {
            const rect = node.getBoundingClientRect();
            const style = window.getComputedStyle(node);
            if (!rect || rect.width < 2 || rect.height < 2) continue;
            if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) continue;
            const index = visible.length;
            node.setAttribute("data-automation-ai-run", runId);
            node.setAttribute("data-automation-ai-index", String(index));
            visible.push({
              index,
              selector: `[data-automation-ai-run="${runId}"][data-automation-ai-index="${index}"]`,
              tag: node.tagName.toLowerCase(),
              role: node.getAttribute("role") || (
                node.tagName === "BUTTON" ? "button" :
                node.tagName === "A" && node.hasAttribute("href") ? "link" :
                node.tagName === "TEXTAREA" ? "textbox" :
                node.tagName === "SELECT" ? "combobox" :
                node.tagName === "INPUT" && node.type === "search" ? "searchbox" :
                node.tagName === "INPUT" && ["button", "submit", "reset"].includes(node.type) ? "button" :
                node.tagName === "INPUT" && node.type !== "hidden" ? "textbox" : ""
              ),
              text: (node.innerText || node.value || "").trim().slice(0, 300),
              aria_label: node.getAttribute("aria-label") || "",
              placeholder: node.getAttribute("placeholder") || "",
              id: node.id || "",
              test_id: node.getAttribute("data-testid") || node.getAttribute("data-test-id") || "",
              name: node.getAttribute("name") || "",
              type: node.getAttribute("type") || "",
              href: node.getAttribute("href") || "",
              bounds: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
              },
            });
            if (visible.length >= limit) break;
          }
          return visible;
        }
        """
        return await self.page.evaluate(script, {"selector": INTERACTIVE_SELECTOR, "limit": limit, "runId": run_id})

    async def _run_action(self, locator: Locator, action: str, value: str | None) -> None:
        if action in {"fill", "fill_and_enter"}:
            if value is None:
                raise RuntimeError("填写类动作必须提供 value，或在指令中用引号包含要输入的文本")
            await locator.fill(value)
            if action == "fill_and_enter":
                await locator.press("Enter")
            return
        if action == "select":
            if value is None:
                raise RuntimeError("选择类动作必须提供 value")
            await locator.select_option(value)
            return
        await locator.click()

    async def _page_text(self, limit_chars: int) -> str:
        text = await self.page.locator("body").inner_text(timeout=5000)
        return text[:limit_chars]

    def _extract_locally(
        self,
        instruction: str,
        schema: dict[str, Any] | None,
        page_text: str,
    ) -> dict[str, Any]:
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if not schema:
            return {
                "instruction": instruction,
                "text": page_text,
                "url": self.page.url,
                "ai_enabled": False,
            }

        data: dict[str, Any] = {}
        for field, description in schema.items():
            field_tokens = set(_tokens(f"{field} {description}"))
            matched = next(
                (line for line in lines if field_tokens.intersection(_tokens(line))),
                None,
            )
            data[field] = matched
        return {
            "instruction": instruction,
            "data": data,
            "url": self.page.url,
            "ai_enabled": False,
            "raw_text_excerpt": page_text[:1000],
        }

    async def _chat_json(self, messages: list[dict[str, str]]) -> Any:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)

    async def _rank_with_ai(
        self,
        instruction: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compact = [
            {
                "index": item["index"],
                "selector": item["selector"],
                "tag": item["tag"],
                "role": item["role"],
                "text": item["text"],
                "aria_label": item["aria_label"],
                "placeholder": item["placeholder"],
                "score": item["score"],
            }
            for item in candidates
        ]
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": "You rank browser element candidates. Return JSON: {\"items\":[{\"index\":0,\"confidence\":0.9,\"reason\":\"...\"}]}",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"instruction": instruction, "candidates": compact},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        by_index = {item["index"]: item for item in candidates}
        ranked: list[dict[str, Any]] = []
        for item in payload.get("items", []):
            source = by_index.get(item.get("index"))
            if not source:
                continue
            enriched = dict(source)
            enriched["confidence"] = item.get("confidence")
            enriched["reason"] = item.get("reason", "AI rank")
            ranked.append(enriched)
        return ranked

    async def _extract_with_ai(
        self,
        instruction: str,
        schema: dict[str, Any] | None,
        page_text: str,
    ) -> dict[str, Any]:
        payload = await self._chat_json(
            [
                {
                    "role": "system",
                    "content": "Extract structured data from page text. Return JSON only.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction": instruction,
                            "schema": schema,
                            "url": self.page.url,
                            "page_text": page_text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        return {
            "instruction": instruction,
            "data": payload,
            "url": self.page.url,
            "ai_enabled": True,
        }
