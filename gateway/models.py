"""网关请求模型与任务状态模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TaskStatus(StrEnum):
    """任务生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    HEALING = "healing"
    HEALED_PENDING_REVIEW = "healed_pending_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BusinessHealth(StrEnum):
    """固化业务健康状态。"""

    HEALTHY = "healthy"
    STABLE = "stable"
    FRAGILE = "fragile"
    UNSTABLE = "unstable"


class FinalizeStatus(StrEnum):
    """一键固化流水线状态。"""

    PENDING = "pending"
    OPTIMIZING = "optimizing"
    TESTING = "testing"
    REGISTERING = "registering"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskRequest(BaseModel):
    """网页和桌面任务共用的统一提交模型。"""

    kind: Literal["web", "desktop"]
    business: str
    params: dict[str, Any] = Field(default_factory=dict)
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    max_retries: int = Field(default=1, ge=0, le=5)
    enable_self_healing: bool = True

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "TaskRequest":
        """保证不同任务类型只依赖其必需字段。"""

        if not self.business.strip():
            raise ValueError("任务必须提供已注册的 business")
        return self


class BatchTaskRequest(BaseModel):
    """批量提交请求；每个子任务仍独立排队、重试和查询。"""

    tasks: list[TaskRequest] = Field(min_length=1, max_length=100)


class RecordingStartRequest(BaseModel):
    """Interactive Playwright Codegen recording parameters."""

    business_name: str = Field(min_length=2, max_length=64)
    start_url: str = Field(min_length=8, max_length=2048)
    profile: str = Field(default="default", min_length=1, max_length=64)


class BusinessDescriptionUpdate(BaseModel):
    """User-editable project purpose for a registered business."""

    description: str = Field(min_length=2, max_length=300)


class TaskRecord(BaseModel):
    """可持久化的完整任务记录。"""

    task_id: str
    status: TaskStatus
    request: TaskRequest
    attempts: int = 0
    healed: bool = False
    parent_task_id: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error_traceback: str | None = None
    business_source: str | None = None
    screenshot: str | None = None
    # 修复审计字段
    healing_diff: str | None = None
    healing_original_source: str | None = None
    healing_fixed_source: str | None = None
    healing_previous_ai_metadata: dict[str, Any] | None = None
    healing_reviewed: bool = False
    healing_reviewed_at: str | None = None
    healing_reviewer_note: str | None = None


class FinalizeRecordingRequest(BaseModel):
    """一键固化录制素材请求。"""

    recording_id: str = Field(min_length=1)
    business_name: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=2, max_length=300)
    start_url: str = Field(min_length=8, max_length=2048)
    auto_test: bool = True
    test_params: dict[str, Any] = Field(default_factory=dict)


class FinalizeRecord(BaseModel):
    """一键固化流水线记录。"""

    finalize_id: str
    recording_id: str
    business_name: str
    description: str = ""
    status: FinalizeStatus
    created_at: str
    updated_at: str
    codex_output: dict[str, Any] | None = None
    test_task_id: str | None = None
    test_result: dict[str, Any] | None = None
    registered: bool = False
    error: str | None = None
    source_path: str | None = None


class ReviewHealRequest(BaseModel):
    """人工确认自愈修复请求。"""

    approved: bool
    note: str | None = None


class BusinessHealthMetrics(BaseModel):
    """业务健康度指标。"""

    business: str
    health: BusinessHealth
    total_runs: int = 0
    success_runs: int = 0
    fail_runs: int = 0
    heal_count: int = 0
    heal_success_count: int = 0
    success_rate: float = 0.0
    heal_rate: float = 0.0
    last_run_at: str | None = None
    last_heal_at: str | None = None
    avg_attempts: float = 0.0
    selector_quality_score: float = 0.0
