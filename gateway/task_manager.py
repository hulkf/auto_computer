"""任务编排器：排队、执行、重试、持久化、失败取证和自愈重跑。

增强功能：
- 自愈修复后进入 healed_pending_review 状态等待人工确认
- 修复审计：保存修复前后 diff、原始源码、修复后源码
- 人工审批/拒绝修复接口
- 业务健康度追踪
"""

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
    restore_business_ai_metadata,
)
from gateway.models import BusinessHealth, BusinessHealthMetrics, ReviewHealRequest, TaskRecord, TaskRequest, TaskStatus
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
        task = asyncio.create_task(self._run_guarded(task_id), name=f"automation-task-{task_id}")
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

    async def review_heal(self, task_id: str, request: ReviewHealRequest) -> TaskRecord:
        """人工审批或拒绝自愈修复。"""

        async with self.state_lock:
            record = self.records.get(task_id)
            if not record:
                raise KeyError(f"任务不存在: {task_id}")
            if record.status != TaskStatus.HEALED_PENDING_REVIEW:
                raise ValueError(f"任务状态为 {record.status}，不是待审批状态")

            record.healing_reviewed = True
            record.healing_reviewed_at = utc_now_iso()
            record.healing_reviewer_note = request.note or ""

            if request.approved:
                # 审批通过：标记为成功
                record.status = TaskStatus.SUCCEEDED
                record.result = result(
                    code=0,
                    msg="自愈修复已人工审批通过",
                    data={"reviewed": True, "approved": True, "note": request.note},
                )
                await self._save(record, "heal_review_approved")
            else:
                # 审批拒绝：回滚源码并标记为失败
                if record.healing_original_source and record.business_source:
                    try:
                        source_path = Path(record.business_source)
                        source_path.write_text(record.healing_original_source, encoding="utf-8")
                        restore_business_ai_metadata(
                            record.request.business,
                            record.healing_previous_ai_metadata or {},
                        )
                    except Exception:
                        pass  # 回滚失败不影响状态标记
                record.status = TaskStatus.FAILED
                record.result = result(
                    code=500,
                    msg="自愈修复已人工拒绝，源码已回滚",
                    data={"reviewed": True, "approved": False, "note": request.note},
                )
                await self._save(record, "heal_review_rejected")

            return record.model_copy(deep=True)

    async def get_business_health(self, business: str) -> BusinessHealthMetrics:
        """计算业务健康度指标。"""

        total = 0
        success = 0
        fail = 0
        heal_count = 0
        heal_success = 0
        attempts_sum = 0
        last_run = None
        last_heal = None

        for record in self.records.values():
            if record.request.business != business:
                continue
            total += 1
            attempts_sum += record.attempts
            if record.finished_at:
                last_run = max(last_run or record.finished_at, record.finished_at)
            if record.status == TaskStatus.SUCCEEDED:
                success += 1
                if record.healed:
                    heal_success += 1
                    if record.finished_at:
                        last_heal = max(last_heal or record.finished_at, record.finished_at)
            elif record.status == TaskStatus.FAILED:
                fail += 1
            elif record.status == TaskStatus.HEALED_PENDING_REVIEW:
                # 待审批的任务视为需要关注的
                heal_count += 1

            if record.healed:
                heal_count += 1

        success_rate = (success / total * 100) if total > 0 else 0.0
        heal_rate = (heal_count / total * 100) if total > 0 else 0.0
        avg_attempts = (attempts_sum / total) if total > 0 else 0.0

        # 选择器质量评分（简化版：基于自愈次数和成功率）
        selector_score = max(0, 100 - heal_rate * 10 - (100 - success_rate) * 0.5)

        # 健康度分级
        if success_rate >= 99 and heal_rate == 0:
            health = BusinessHealth.HEALTHY
        elif success_rate >= 95 and heal_rate <= 5:
            health = BusinessHealth.STABLE
        elif success_rate >= 80 and heal_rate <= 20:
            health = BusinessHealth.FRAGILE
        else:
            health = BusinessHealth.UNSTABLE

        return BusinessHealthMetrics(
            business=business,
            health=health,
            total_runs=total,
            success_runs=success,
            fail_runs=fail,
            heal_count=heal_count,
            heal_success_count=heal_success,
            success_rate=round(success_rate, 2),
            heal_rate=round(heal_rate, 2),
            last_run_at=last_run,
            last_heal_at=last_heal,
            avg_attempts=round(avg_attempts, 2),
            selector_quality_score=round(selector_score, 2),
        )

    async def list_all_tasks(
        self,
        *,
        status: str | None = None,
        business: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRecord]:
        """列出任务，支持筛选和分页。"""

        records = list(self.records.values())
        if status:
            records = [r for r in records if r.status == status]
        if business:
            records = [r for r in records if r.request.business == business]
        if kind:
            records = [r for r in records if r.request.kind == kind]

        records.sort(key=lambda r: r.created_at, reverse=True)
        return [r.model_copy(deep=True) for r in records[offset : offset + limit]]

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
        """执行普通重试；失败后触发一次自愈，修复后进入 healed_pending_review 等待人工确认。"""

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
                artifact_path = None
                if request.kind == "desktop":
                    artifact_path, _ = get_desktop_executable(request.business)
                fixed, audit_info = await self.self_healer.repair(
                    task_id=task_id,
                    business=request.business,
                    source_path=Path(record.business_source),
                    error_traceback=record.error_traceback or "unknown error",
                    screenshot=record.screenshot,
                    request_payload=request.model_dump(mode="json"),
                    task_result=record.result or result(code=500, msg="unknown error"),
                    artifact_path=artifact_path,
                )
                if fixed and audit_info:
                    # 保存修复审计信息
                    record.healed = True
                    record.healing_diff = audit_info.get("healing_diff")
                    record.healing_original_source = audit_info.get("healing_original_source")
                    record.healing_fixed_source = audit_info.get("healing_fixed_source")
                    record.healing_previous_ai_metadata = audit_info.get("healing_previous_ai_metadata")
                    await self._save(record, "healing_completed")

                    # 修复后重跑一次
                    record.status = TaskStatus.RUNNING
                    record.attempts += 1
                    await self._save(record, "healed_rerun_started")
                    task_result = await self._attempt(record)
                    record.result = task_result
                    record.screenshot = task_result.get("screenshot")

                    if task_result["code"] == 0:
                        # 修复后成功：进入待审批状态
                        await self._finish(record, TaskStatus.HEALED_PENDING_REVIEW, "healed_rerun_succeeded_pending_review")
                        return
                    else:
                        # 修复后仍失败
                        record.error_traceback = self._traceback_from_result(task_result)
                        await self._save(record, "healed_rerun_failed")

            await self._finish(record, TaskStatus.FAILED, "failed")

    async def _run_guarded(self, task_id: str) -> None:
        """后台任务最外层保险：任何未预见异常都必须写入 failed 终态。"""

        try:
            await self._run(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.state_lock:
                record = self.records[task_id]
                record.status = TaskStatus.FAILED
                record.finished_at = utc_now_iso()
                record.error_traceback = traceback.format_exc()
                record.result = result(code=500, msg=str(exc), data={"traceback": record.error_traceback})
                await self._save(record, "unhandled_execution_failure")

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
