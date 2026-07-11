"""Run a Baidu search and save ranked result titles as JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.async_api import Page, async_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "runtime" / "baidu_kobe_top10.json"
DEFAULT_QUERY = "\u79d1\u6bd4"


async def extract_results(page: Page) -> dict[str, Any]:
    """Read the current Baidu result page without relying on load completion."""

    return await page.evaluate(
        """() => {
            const text = document.body?.innerText || "";
            const captcha =
                location.href.includes("captcha") ||
                document.title.includes("\u5b89\u5168\u9a8c\u8bc1") ||
                text.includes("\u767e\u5ea6\u5b89\u5168\u9a8c\u8bc1");
            const items = [...document.querySelectorAll("h3 a")]
                .map((anchor) => {
                    const title = (anchor.innerText || anchor.textContent || "")
                        .trim()
                        .replace(/\\s+/g, " ");
                    const container =
                        anchor.closest(".result, .c-container, [tpl]") ||
                        anchor.closest("div");
                    let snippet = "";
                    if (container) {
                        snippet = (container.innerText || "")
                            .replace(title, "")
                            .trim()
                            .replace(/\\s+/g, " ")
                            .slice(0, 500);
                    }
                    return title ? { title, url: anchor.href || "", snippet } : null;
                })
                .filter(Boolean);
            return {
                url: location.href,
                page_title: document.title,
                captcha,
                items,
            };
        }"""
    )


async def goto_and_extract(page: Page, url: str, wait_ms: int) -> dict[str, Any]:
    """Navigate to a Baidu page and inspect DOM even if navigation keeps loading."""

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        # Baidu can keep the navigation open after useful DOM is already present.
        pass
    await page.wait_for_timeout(wait_ms)
    return await extract_results(page)


def append_unique_results(
    output: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    seen_titles: set[str],
    limit: int,
    source_page: int,
) -> None:
    for row in rows:
        title = str(row.get("title", "")).strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        output.append(
            {
                "rank": len(output) + 1,
                "title": title,
                "url": row.get("url", ""),
                "snippet": row.get("snippet", ""),
                "source_page": source_page,
            }
        )
        if len(output) >= limit:
            return


async def search_baidu(
    query: str,
    *,
    limit: int,
    output_path: Path,
    wait_ms: int,
    headless: bool,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    searched_urls: list[str] = []
    page_titles: list[str] = []
    captcha = False

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page(
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        pages_needed = max(1, (limit + 9) // 10)
        for page_index in range(pages_needed + 1):
            pn = page_index * 10
            url = "https://www.baidu.com/s?" + urlencode({"wd": query, "pn": pn})
            state = await goto_and_extract(page, url, wait_ms)
            searched_urls.append(state["url"])
            page_titles.append(state["page_title"])
            captcha = captcha or bool(state["captcha"])
            if not state["captcha"]:
                append_unique_results(
                    results,
                    state["items"],
                    seen_titles=seen_titles,
                    limit=limit,
                    source_page=page_index + 1,
                )
            if len(results) >= limit or state["captcha"]:
                break
        await browser.close()

    payload = {
        "query": query,
        "engine": "baidu",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "searched_urls": searched_urls,
        "page_titles": page_titles,
        "captcha": captcha,
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Search keyword.")
    parser.add_argument("--limit", type=int, default=10, help="Number of results to save.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON output path.")
    parser.add_argument("--wait-ms", type=int, default=8000, help="DOM settle wait after navigation.")
    parser.add_argument("--headed", action="store_true", help="Show Chromium while running.")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Exit 0 even when Baidu returns fewer results than requested.",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    payload = await search_baidu(
        args.query,
        limit=max(1, args.limit),
        output_path=args.output.resolve(),
        wait_ms=max(0, args.wait_ms),
        headless=not args.headed,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["count"] < max(1, args.limit) and not args.allow_partial:
        print(
            (
                f"Baidu returned {payload['count']} result(s), fewer than requested "
                f"{max(1, args.limit)}. Check captcha/searched_urls in the JSON output."
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(async_main())
