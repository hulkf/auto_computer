"""任务编排器：排队、执行、重试、持久化、失败取证和自愈重跑。"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from pathlib import Path
from typing import Any

from core.ahk_runner import AhkRunner
from core.common_utils import TASK_LOG_DIR, append_jsonl, read_json, result, utc_now_iso, write_json
from core.playwright_base import BrowserContextPool
from gateway.business_registry import (
    get_business,
    get_business_source,
    get_desktop_executable,
    load_web_business,
)
from gateway.models import TaskRecord, TaskRequest, TaskStatus
from gateway.self_healer import SelfHealer


class TaskManager:
    """网关内唯一任务管理器，保证网页和桌面任务遵循相同生命周期。"""

    def __init__(self, browser_pool: BrowserContextPool) -> None:
        self.browser_pool = browser_pool
        self.ahk_runner = AhkRunner()
        self.self_healer = SelfHealer()
        self.records: dict[str, TaskRecord] = {}
        self.background_tasks: set[asyncio.Task[None]] = set()
        self.state_lock = asyncio.Lock()
        self.execution_limit = asyncio.Semaphore(8)
        self._load_existing_records()

    def _record_path(self, task_id: str) -> Path:
        """返回单任务状态快照路径。"""

        return TASK_LOG_DIR / f"{task_id}.json"

    def _event_path(self, task_id: str) -> Path:
        """返回单任务不可变事件日志路径。"""

        return TASK_LOG_DIR / f"{task_id}.jsonl"

    def _load_existing_records(self) -> None:
        """网关重启后恢复历史任务；中断中的任务标记为失败，避免假运行状态。"""

        for path in TASK_LOG_DIR.glob("*.json"):
            payload = read_json(path)
            if not payload:
                continue
            try:
                record = TaskRecord.model_validate(payload)
            except Exception:
                continue
            if record.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.HEALING}:
                record.status = TaskStatus.FAILED
                record.finished_at = utc_now_iso()
                record.result = result(code=503, msg="网关重启导致任务中断，请手动重试")
                write_json(path, record.model_dump(mode="json"))
            self.records[record.task_id] = record

    async def _save(self, record: TaskRecord, event: str) -> None:
        """同时更新当前快照和全量事件日志。"""

        payload = record.model_dump(mode="json")
        write_json(self._record_path(record.task_id), payload)
        append_jsonl(
            self._event_path(record.task_id),
            {"time": utc_now_iso(), "event": event, "record": payload},
        )

    async def submit(self, request: TaskRequest, *, parent_task_id: str | None = None) -> TaskRecord:
        """创建任务并立即进入后台队列，接口无需等待长任务结束。"""

        task_id = uuid.uuid4().hex
        business_source = None
        # 提交阶段即校验统一白名单和业务类型，错误直接返回调用方。
        get_business(request.business, request.kind)
        business_source = str(get_business_source(request.business).resolve())
        record = TaskRecord(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            request=request,
            parent_task_id=parent_task_id,
            created_at=utc_now_iso(),
            business_source=business_source,
        )
        async with self.state_lock:
            self.records[task_id] = record
            await self._save(record, "submitted")
        task = asyncio.create_task(self._run(task_id), name=f"automation-task-{task_id}")
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return record.model_copy(deep=True)

    async def get(self, task_id: str) -> TaskRecord | None:
        """读取任务状态副本，防止 API 序列化期间状态被并发修改。"""

        async with self.state_lock:
            record = self.records.get(task_id)
            return record.model_copy(deep=True) if record else None

    async def retry(self, task_id: str) -> TaskRecord:
        """基于原请求创建新任务；历史任务保持不可变，便于审计。"""

        original = await self.get(task_id)
        if not original:
            raise KeyError(f"任务不存在: {task_id}")
        if original.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.HEALING}:
            raise ValueError("任务仍在执行中，不能手动重试")
        return await self.submit(original.request, parent_task_id=task_id)

    async def _transition(self, task_id: str, status: TaskStatus, event: str) -> TaskRecord:
        """原子更新状态并写入审计事件。"""

        async with self.state_lock:
            record = self.records[task_id]
            record.status = status
            await self._save(record, event)
            return record

    async def _execute_once(self, record: TaskRecord) -> dict[str, Any]:
        """按任务类型分发到已注册网页业务或 AHK 公共执行器。"""

        request = record.request
        if request.kind == "web":
            run_business = load_web_business(request.business)
            return await run_business(request.params, self.browser_pool, task_id=record.task_id)
        executable, registered_cwd = get_desktop_executable(request.business)
        return await self.ahk_runner.run(
            str(executable),
            request.args,
            timeout_seconds=request.timeout_seconds,
            cwd=str(registered_cwd) if registered_cwd else None,
        )

    async def _attempt(self, record: TaskRecord) -> dict[str, Any]:
        """执行一次并保证即使业务违反契约或抛出异常也转换为标准 JSON。"""

        try:
            task_result = await self._execute_once(record)
            required_keys = {"code", "msg", "data", "screenshot"}
            if not isinstance(task_result, dict) or not required_keys.issubset(task_result):
                return result(code=500, msg="业务返回体不符合 code/msg/data/screenshot 统一协议")
            return task_result
        except Exception as exc:
            return result(
                code=500,
                msg=str(exc),
                data={"traceback": traceback.format_exc()},
            )

    @staticmethod
    def _traceback_from_result(task_result: dict[str, Any]) -> str:
        """从标准返回体提取完整堆栈；没有堆栈时至少保留错误消息。"""

        data = task_result.get("data")
        if isinstance(data, dict) and data.get("traceback"):
            return str(data["traceback"])
        return str(task_result.get("msg", "unknown error"))

    async def _run(self, task_id: str) -> None:
        """执行普通重试；失败后触发一次自愈，并在确认修复后自动重跑一次。"""

        async with self.execution_limit:
            record = await self._transition(task_id, TaskStatus.RUNNING, "started")
            record.started_at = utc_now_iso()
            await self._save(record, "execution_started")

            # max_retries 表示首次执行之外的重试次数。
            for _ in range(record.request.max_retries + 1):
                record.attempts += 1
                await self._save(record, "attempt_started")
                task_result = await self._attempt(record)
                record.result = task_result
                record.screenshot = task_result.get("screenshot")
                if task_result["code"] == 0:
                    await self._finish(record, TaskStatus.SUCCEEDED, "succeeded")
                    return
                record.error_traceback = self._traceback_from_result(task_result)
                await self._save(record, "attempt_failed")

            request = record.request
            if (
                request.enable_self_healing
                and record.business_source
            ):
                await self._transition(task_id, TaskStatus.HEALING, "healing_started")
                fixed = await self.self_healer.repair(
                    task_id=task_id,
                    business=request.business,
                    source_path=Path(record.business_source),
                    error_traceback=record.error_traceback or "unknown error",
                    screenshot=record.screenshot,
                    params=request.params,
                )
                if fixed:
                    record.healed = True
                    record.status = TaskStatus.RUNNING
                    record.attempts += 1
                    await self._save(record, "healed_rerun_started")
                    task_result = await self._attempt(record)
                    record.result = task_result
                    record.screenshot = task_result.get("screenshot")
                    if task_result["code"] == 0:
                        await self._finish(record, TaskStatus.SUCCEEDED, "healed_rerun_succeeded")
                        return
                    record.error_traceback = self._traceback_from_result(task_result)
                    await self._save(record, "healed_rerun_failed")

            await self._finish(record, TaskStatus.FAILED, "failed")

    async def _finish(self, record: TaskRecord, status: TaskStatus, event: str) -> None:
        """写入任务终态和结束时间。"""

        record.status = status
        record.finished_at = utc_now_iso()
        await self._save(record, event)

    async def shutdown(self) -> None:
        """优雅停机：取消仍在运行的任务，再关闭浏览器常驻池。"""

        for task in list(self.background_tasks):
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        # 正常停机也要落下明确终态，不能让查询端长期看到幽灵 running。
        for record in self.records.values():
            if record.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.HEALING}:
                record.status = TaskStatus.FAILED
                record.finished_at = utc_now_iso()
                record.result = result(code=503, msg="网关停机导致任务中断，请手动重试")
                await self._save(record, "shutdown_interrupted")
        await self.browser_pool.close()
