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
    SUCCEEDED = "succeeded"
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
