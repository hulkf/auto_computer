"""网页与桌面业务统一白名单；网关不会执行请求方传入的任意模块或 EXE。"""

from __future__ import annotations

import importlib
import inspect
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.common_utils import PROJECT_ROOT, read_json, utc_now_iso, write_json


@dataclass(frozen=True)
class BusinessDefinition:
    """固化业务定义；网页使用 module，桌面使用 executable 和 source。"""

    kind: Literal["web", "desktop"]
    description: str
    module: str | None = None
    executable: str | None = None
    source: str | None = None
    cwd: str | None = None


# 新业务必须在这里显式注册。桌面业务示例见 README，禁止从 API 传入 EXE 路径。
BUSINESSES: dict[str, BusinessDefinition] = {
    "demo_search": BusinessDefinition(
        kind="web",
        description="演示如何通过统一中台执行 Bing 搜索并提取结果标题。",
        module="business.demo_search.task",
        source="business/demo_search/task.py",
    ),
}
_METADATA_LOCKS: dict[str, threading.Lock] = {}


def get_business(name: str, expected_kind: str | None = None) -> BusinessDefinition:
    """读取白名单定义，并校验调用方声明的任务类型。"""

    definition = BUSINESSES.get(name)
    if not definition:
        raise KeyError(f"未注册业务: {name}")
    if expected_kind and definition.kind != expected_kind:
        raise ValueError(f"业务 {name} 的注册类型为 {definition.kind}，不是 {expected_kind}")
    return definition


def _project_path(relative_path: str, *, must_exist: bool = True) -> Path:
    """解析并限制注册路径位于项目根目录内，阻断错误配置导致的越界访问。"""

    path = (PROJECT_ROOT / relative_path).resolve()
    if path != PROJECT_ROOT and PROJECT_ROOT not in path.parents:
        raise RuntimeError(f"注册路径超出项目目录: {path}")
    if must_exist and not path.is_file():
        raise RuntimeError(f"注册文件不存在: {path}")
    return path


def get_business_source(name: str) -> Path:
    """获取交给 Codex 修复的固化业务源码绝对路径。"""

    definition = get_business(name)
    if not definition.source:
        raise RuntimeError(f"业务未配置源码路径: {name}")
    path = _project_path(definition.source)
    business_root = (PROJECT_ROOT / "business").resolve()
    if business_root not in path.parents:
        raise RuntimeError(f"业务源码必须位于 business 目录: {path}")
    return path


def write_business_metadata_for_source(
    source_path: Path,
    *,
    description: str | None = None,
    ai_summary: str | None = None,
    updated_by: str,
) -> dict[str, Any]:
    """Persist editable purpose and the latest AI change summary beside a business."""

    source_path = source_path.resolve()
    business_root = (PROJECT_ROOT / "business").resolve()
    if business_root not in source_path.parents:
        raise RuntimeError("业务元数据必须位于 business 目录")
    metadata_path = source_path.parent / "metadata.json"
    lock = _METADATA_LOCKS.setdefault(str(metadata_path), threading.Lock())
    with lock:
        metadata = read_json(metadata_path, default={})
        if not isinstance(metadata, dict):
            metadata = {}
        now = utc_now_iso()
        if description is not None:
            cleaned = description.strip()
            if not cleaned:
                raise ValueError("项目作用不能为空")
            metadata["description"] = cleaned
            metadata["description_updated_at"] = now
            metadata["description_updated_by"] = updated_by
        if ai_summary is not None:
            metadata["ai_summary"] = ai_summary.strip() or "AI已完成业务源码修改，未提供详细摘要。"
            metadata["ai_summary_updated_at"] = now
            metadata["ai_summary_updated_by"] = updated_by
        write_json(metadata_path, metadata)
    return metadata


def get_business_metadata(name: str) -> dict[str, Any]:
    """Read current metadata for a registered business."""

    metadata = read_json(get_business_source(name).parent / "metadata.json", default={})
    return metadata if isinstance(metadata, dict) else {}


def restore_business_ai_metadata(name: str, previous: dict[str, Any]) -> None:
    """Restore only AI summary fields after a rejected self-heal."""

    source_path = get_business_source(name)
    metadata_path = source_path.parent / "metadata.json"
    lock = _METADATA_LOCKS.setdefault(str(metadata_path), threading.Lock())
    keys = ("ai_summary", "ai_summary_updated_at", "ai_summary_updated_by")
    with lock:
        metadata = read_json(metadata_path, default={})
        metadata = metadata if isinstance(metadata, dict) else {}
        for key in keys:
            if key in previous:
                metadata[key] = previous[key]
            else:
                metadata.pop(key, None)
        write_json(metadata_path, metadata)


def register_business_in_memory(
    name: str,
    *,
    description: str,
    module: str,
    source: str,
) -> None:
    """Make a newly finalized web business available without restarting the gateway."""

    BUSINESSES[name] = BusinessDefinition(
        kind="web",
        description=description,
        module=module,
        source=source,
    )


def update_business_metadata(
    name: str,
    *,
    description: str | None = None,
    ai_summary: str | None = None,
    updated_by: str,
) -> dict[str, Any]:
    """Update metadata only for a registered, whitelisted business."""

    return write_business_metadata_for_source(
        get_business_source(name),
        description=description,
        ai_summary=ai_summary,
        updated_by=updated_by,
    )


def get_desktop_executable(name: str) -> tuple[Path, Path | None]:
    """获取桌面业务固化 EXE 和工作目录。"""

    definition = get_business(name, "desktop")
    if not definition.executable:
        raise RuntimeError(f"桌面业务未配置 executable: {name}")
    executable = _project_path(definition.executable)
    cwd = _project_path(definition.cwd, must_exist=False) if definition.cwd else None
    return executable, cwd


def list_businesses() -> list[dict[str, Any]]:
    """列出已注册业务，同时展示可审计的源码和运行入口。"""

    rows = []
    for name, definition in sorted(BUSINESSES.items()):
        metadata = read_json(get_business_source(name).parent / "metadata.json", default={})
        metadata = metadata if isinstance(metadata, dict) else {}
        rows.append(
            {
                "name": name,
                "kind": definition.kind,
                "description": metadata.get("description") or definition.description,
                "ai_summary": metadata.get("ai_summary"),
                "description_updated_at": metadata.get("description_updated_at"),
                "description_updated_by": metadata.get("description_updated_by"),
                "ai_summary_updated_at": metadata.get("ai_summary_updated_at"),
                "ai_summary_updated_by": metadata.get("ai_summary_updated_by"),
                "module": definition.module,
                "executable": definition.executable,
                "source": str(get_business_source(name)),
            }
        )
    return rows


def load_web_business(name: str):
    """加载网页业务 run 协程；自愈修改源码后 reload 可立即生效。"""

    definition = get_business(name, "web")
    if not definition.module:
        raise RuntimeError(f"网页业务未配置 module: {name}")
    if definition.module in sys.modules:
        module = importlib.reload(sys.modules[definition.module])
    else:
        module = importlib.import_module(definition.module)
    run = getattr(module, "run", None)
    if not run or not inspect.iscoroutinefunction(run):
        raise TypeError(f"业务模块必须实现 async run(...): {definition.module}")
    return run
