"""示例业务：只保留搜索网站特有的页面操作，不重复任何公共能力。"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from core.common_utils import result
from core.playwright_base import BrowserContextPool, PlaywrightBase


async def run(
    params: dict[str, Any],
    browser_pool: BrowserContextPool,
    *,
    task_id: str,
) -> dict[str, Any]:
    """执行一次 Bing 搜索并提取结果标题。

    业务参数：query（必填）、limit（可选，默认 5）、profile（可选，默认 demo）。
    浏览器创建、等待、重试、截图、异常 JSON 均由 core 统一处理。
    """

    query = str(params.get("query", "")).strip()
    if not query:
        return result(code=400, msg="参数 query 不能为空")
    limit = max(1, min(int(params.get("limit", 5)), 20))
    automation = PlaywrightBase(
        browser_pool,
        task_id=task_id,
        profile=str(params.get("profile", "demo")),
    )

    async def search(page: Page) -> dict[str, Any]:
        """这里是本业务唯一需要维护的网站专属操作步骤。"""

        await automation.goto("https://www.bing.com/")
        searchbox = automation.by_role("searchbox")

        async def submit_search() -> None:
            await searchbox.fill(query)
            await searchbox.press("Enter")

        await automation.fixed_operation(
            submit_search,
            intent="在搜索框输入关键词并搜索",
            current_locator="get_by_role('searchbox')",
        )
        await page.wait_for_load_state("domcontentloaded")

        # 结果页没有稳定统一的 role/name，故在业务层使用站点专属 CSS。
        result_titles = page.locator("li.b_algo h2")

        async def wait_for_results() -> None:
            await result_titles.first.wait_for(state="visible")

        await automation.fixed_operation(
            wait_for_results,
            intent="找到搜索结果标题列表",
            current_locator="locator('li.b_algo h2').first",
        )
        titles = await result_titles.all_inner_texts()
        return {
            "query": query,
            "count": min(len(titles), limit),
            "titles": [title.strip() for title in titles[:limit]],
            "url": page.url,
            "operation_mode": "fixed_playwright",
        }

    return await automation.execute(search)
