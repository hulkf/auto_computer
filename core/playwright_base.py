"""Playwright 公共封装：持久上下文池、等待/重试、截图与统一异常返回。"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from playwright.async_api import BrowserContext, Locator, Page, Playwright, async_playwright

from .ai_browser import AIBrowserOperator
from .common_utils import (
    BROWSER_PROFILE_DIR,
    SCREENSHOT_DIR,
    env_bool,
    get_logger,
    result,
    safe_name,
    save_snapshot,
)

T = TypeVar("T")


class BrowserDiagnosisError(RuntimeError):
    """Fixed locator failure carrying read-only browser diagnosis evidence."""

    def __init__(self, message: str, diagnosis: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnosis = diagnosis


class BrowserContextPool:
    """管理可复用的持久 Chrome 上下文，以保留登录态并降低热启动成本。"""

    def __init__(
        self,
        *,
        headless: bool | None = None,
        channel: str | None = None,
        max_contexts: int | None = None,
    ) -> None:
        self.headless = env_bool("AUTOMATION_HEADLESS", False) if headless is None else headless
        self.channel = channel or os.getenv("AUTOMATION_BROWSER_CHANNEL", "chrome")
        self.max_contexts = max_contexts or int(os.getenv("AUTOMATION_MAX_BROWSER_CONTEXTS", "4"))
        self._playwright: Playwright | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._context_locks: dict[str, asyncio.Lock] = {}
        self._last_used: dict[str, float] = {}
        self._pool_lock = asyncio.Lock()
        self.logger = get_logger(__name__)

    async def start(self) -> None:
        """启动 Playwright 驱动；不会提前打开无业务需要的浏览器。"""

        if self._playwright is None:
            self._playwright = await async_playwright().start()
            self.logger.info("Playwright driver started")

    async def get_context(self, profile: str = "default") -> BrowserContext:
        """获取或创建指定用户目录的持久上下文。不同 profile 隔离登录态。"""

        profile_name = safe_name(profile)
        await self.start()
        async with self._pool_lock:
            existing = self._contexts.get(profile_name)
            if existing:
                self._last_used[profile_name] = time.monotonic()
                return existing
            if len(self._contexts) >= self.max_contexts:
                # 关闭最久未使用且当前没有任务持有的上下文；用户数据仍在磁盘保留。
                idle_profiles = [
                    name
                    for name in self._contexts
                    if not self._context_locks.setdefault(name, asyncio.Lock()).locked()
                ]
                if not idle_profiles:
                    raise RuntimeError(f"浏览器上下文池繁忙，最大数量为 {self.max_contexts}")
                oldest = min(idle_profiles, key=lambda name: self._last_used.get(name, 0.0))
                old_context = self._contexts.pop(oldest)
                self._last_used.pop(oldest, None)
                await old_context.close()
                self.logger.info("Evicted idle browser context: %s", oldest)
            assert self._playwright is not None
            profile_dir = (BROWSER_PROFILE_DIR / profile_name).resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=self.channel or None,
                headless=self.headless,
                accept_downloads=True,
                viewport={"width": 1440, "height": 900},
            )
            timeout_ms = int(os.getenv("AUTOMATION_DEFAULT_TIMEOUT_MS", "30000"))
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)
            self._contexts[profile_name] = context
            self._context_locks.setdefault(profile_name, asyncio.Lock())
            self._last_used[profile_name] = time.monotonic()
            self.logger.info("Persistent browser context opened: %s", profile_name)
            return context

    def profile_lock(self, profile: str) -> asyncio.Lock:
        """同一 profile 串行执行，防止并发任务互相操作同一个登录会话。"""

        return self._context_locks.setdefault(safe_name(profile), asyncio.Lock())

    async def close(self) -> None:
        """关闭全部上下文及 Playwright 驱动，供网关优雅停机调用。"""

        for context in list(self._contexts.values()):
            try:
                await context.close()
            except Exception:
                self.logger.exception("Failed to close a browser context")
        self._contexts.clear()
        self._last_used.clear()
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self.logger.info("Browser context pool stopped")


class PlaywrightBase:
    """业务脚本使用的唯一浏览器入口，集中处理页面生命周期和失败证据。"""

    def __init__(self, pool: BrowserContextPool, *, task_id: str, profile: str = "default") -> None:
        self.pool = pool
        self.task_id = task_id
        self.profile = safe_name(profile)
        self.page: Page | None = None
        self.logger = get_logger(__name__, task_id)

    async def open_page(self) -> Page:
        """在持久上下文中新建干净页面。"""

        context = await self.pool.get_context(self.profile)
        self.page = await context.new_page()
        return self.page

    async def close_page(self) -> None:
        """任务结束后关闭页面，但保留上下文和登录态供后续热启动。"""

        if self.page and not self.page.is_closed():
            await self.page.close()
        self.page = None

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        """导航并执行统一页面等待。"""

        if not self.page:
            raise RuntimeError("页面尚未创建，请先调用 open_page()")
        await self.page.goto(url, wait_until=wait_until)
        await self.page.wait_for_load_state("domcontentloaded")

    def by_role(self, role: str, *, name: str | None = None, exact: bool = False) -> Locator:
        """首选的可访问性角色选择器。"""

        if not self.page:
            raise RuntimeError("页面尚未创建")
        return self.page.get_by_role(role, name=name, exact=exact)

    def by_text(self, text: str, *, exact: bool = False) -> Locator:
        """次选的可见文本选择器。"""

        if not self.page:
            raise RuntimeError("页面尚未创建")
        return self.page.get_by_text(text, exact=exact)

    def _ai_operator(self) -> AIBrowserOperator:
        """获取当前页面的 AI 增强操作器；仍复用本任务页面生命周期。"""

        if not self.page:
            raise RuntimeError("页面尚未创建")
        return AIBrowserOperator(self.page, task_id=self.task_id)

    async def observe(
        self,
        instruction: str,
        *,
        limit: int = 10,
        use_ai: bool = True,
    ) -> list[dict[str, Any]]:
        """按自然语言意图观察页面可操作目标，类似 Stagehand observe。"""

        return await self._ai_operator().observe(instruction, limit=limit, use_ai=use_ai)

    async def act(self, instruction: str, *, value: str | None = None) -> dict[str, Any]:
        """按自然语言意图执行一次页面动作，类似 Stagehand act。"""

        return await self._ai_operator().act(instruction, value=value)

    async def extract(
        self,
        instruction: str,
        *,
        schema: dict[str, Any] | None = None,
        limit_chars: int = 12000,
    ) -> dict[str, Any]:
        """按自然语言意图提取页面数据，类似 Stagehand extract。"""

        return await self._ai_operator().extract(
            instruction,
            schema=schema,
            limit_chars=limit_chars,
        )

    # 显式别名便于业务脚本表达“这里用了 AI 增强能力”。
    ai_observe = observe
    ai_act = act
    ai_extract = extract

    async def fixed_operation(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        intent: str,
        current_locator: str,
        diagnostic_limit: int = 8,
    ) -> T:
        """Run a fixed locator and start read-only diagnosis only after failure."""

        try:
            return await operation()
        except Exception as exc:
            diagnosis: dict[str, Any] = {
                "intent": intent,
                "failed_locator": current_locator,
                "original_error": str(exc),
                "candidates": [],
            }
            try:
                operator = self._ai_operator()
                diagnosis["ai_available"] = operator.ai_enabled
                diagnosis["ai_used"] = False
                diagnosis["candidates"] = await operator.observe(
                    intent,
                    limit=diagnostic_limit,
                    use_ai=False,
                )
            except Exception as diagnostic_exc:
                diagnosis["diagnostic_error"] = str(diagnostic_exc)
            snapshot = save_snapshot("browser_diagnostics", self.task_id, diagnosis)
            diagnosis["snapshot"] = str(snapshot)
            raise BrowserDiagnosisError(
                f"Fixed locator failed; browser diagnosis saved: {exc}",
                diagnosis,
            ) from exc

    async def retry(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        attempts: int = 3,
        delay_seconds: float = 1.0,
        description: str = "page operation",
    ) -> T:
        """对易受页面抖动影响的单个元素操作执行统一指数退避重试。"""

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except Exception as exc:  # Playwright 异常类型较多，统一在边界处捕获。
                last_error = exc
                self.logger.warning("%s failed (%s/%s): %s", description, attempt, attempts, exc)
                if attempt < attempts:
                    await asyncio.sleep(delay_seconds * attempt)
        assert last_error is not None
        raise last_error

    async def screenshot(self, label: str = "error") -> str | None:
        """截取当前页面并返回绝对路径；截图自身失败时不覆盖原始异常。"""

        if not self.page or self.page.is_closed():
            return None
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"{safe_name(self.task_id)}_{safe_name(label)}_{timestamp}.png"
        try:
            await self.page.screenshot(path=str(path), full_page=True)
            return str(path.resolve())
        except Exception:
            self.logger.exception("Failed to capture screenshot")
            return None

    async def execute(self, operation: Callable[[Page], Awaitable[Any]]) -> dict[str, Any]:
        """执行业务页面流程，统一加锁、异常捕获、截图和 JSON 返回。"""

        async with self.pool.profile_lock(self.profile):
            try:
                page = await self.open_page()
                data = await operation(page)
                # 允许业务返回标准结构，但禁止再套一层 data。
                if isinstance(data, dict) and set(("code", "msg", "data", "screenshot")).issubset(data):
                    return data
                return result(data=data)
            except Exception as exc:
                screenshot = await self.screenshot("exception")
                self.logger.exception("Browser task failed")
                error_data: dict[str, Any] = {"traceback": traceback.format_exc()}
                if isinstance(exc, BrowserDiagnosisError):
                    error_data["browser_diagnosis"] = exc.diagnosis
                return result(
                    code=500,
                    msg=str(exc),
                    data=error_data,
                    screenshot=screenshot,
                )
            finally:
                await self.close_page()
