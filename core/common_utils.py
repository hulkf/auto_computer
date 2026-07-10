"""全中台通用工具：路径、日志、JSON、任务快照和标准返回体。"""

from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 所有路径都从项目根目录推导，避免业务脚本依赖当前工作目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
SCREENSHOT_DIR = LOG_DIR / "screenshots"
TASK_LOG_DIR = LOG_DIR / "tasks"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
SNAPSHOT_DIR = RUNTIME_DIR / "snapshots"
BROWSER_PROFILE_DIR = PROJECT_ROOT / "browser_profiles"
_SHARED_HANDLERS: tuple[logging.Handler, logging.Handler] | None = None


def ensure_directories() -> None:
    """创建中台运行所需的公共目录；函数可重复调用。"""

    for path in (
        LOG_DIR,
        SCREENSHOT_DIR,
        TASK_LOG_DIR,
        RUNTIME_DIR,
        SNAPSHOT_DIR,
        BROWSER_PROFILE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    """返回带时区、便于 JSON 存储的 UTC 时间。"""

    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量，兼容常见真假写法。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def safe_name(value: str) -> str:
    """把外部名称转换为安全文件名，防止目录穿越和非法字符。"""

    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned.strip("_") or "unnamed"


def result(
    code: int = 0,
    msg: str = "success",
    data: Any = None,
    screenshot: str | None = None,
) -> dict[str, Any]:
    """生成全中台唯一标准 JSON 返回结构。code=0 表示成功。"""

    return {"code": code, "msg": msg, "data": data, "screenshot": screenshot}


class JsonFormatter(logging.Formatter):
    """将日志格式化为单行 JSON，便于后续检索和集中采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": utc_now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        task_id = getattr(record, "task_id", None)
        if task_id:
            payload["task_id"] = task_id
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str, task_id: str | None = None) -> logging.LoggerAdapter:
    """获取分级日志记录器，并可自动为每条日志附加 task_id。"""

    ensure_directories()
    logger = logging.getLogger(name)
    logger.setLevel(os.getenv("AUTOMATION_LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    global _SHARED_HANDLERS
    if _SHARED_HANDLERS is None:
        formatter = JsonFormatter()
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        rotating_file = TimedRotatingFileHandler(
            LOG_DIR / "automation.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
            utc=True,
        )
        rotating_file.setFormatter(formatter)
        _SHARED_HANDLERS = (stream, rotating_file)
    if not logger.handlers:
        for handler in _SHARED_HANDLERS:
            logger.addHandler(handler)
    return logging.LoggerAdapter(logger, {"task_id": task_id} if task_id else {})


def read_json(path: Path, default: Any = None) -> Any:
    """读取 UTF-8 JSON；文件不存在时返回调用方提供的默认值。"""

    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    """原子写入 JSON，避免进程中断留下半个状态文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        temporary_path = Path(file.name)
    temporary_path.replace(path)


def append_jsonl(path: Path, payload: Any) -> None:
    """追加一条 JSONL 事件，作为任务生命周期的不可变审计日志。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def save_snapshot(namespace: str, key: str, payload: Any) -> Path:
    """保存业务或任务快照，并返回可记录到日志中的绝对路径。"""

    path = SNAPSHOT_DIR / safe_name(namespace) / f"{safe_name(key)}.json"
    write_json(path, payload)
    return path.resolve()


ensure_directories()
