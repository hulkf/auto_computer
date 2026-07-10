"""网页与桌面业务统一白名单；网关不会执行请求方传入的任意模块或 EXE。"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.common_utils import PROJECT_ROOT


@dataclass(frozen=True)
class BusinessDefinition:
    """固化业务定义；网页使用 module，桌面使用 executable 和 source。"""

    kind: Literal["web", "desktop"]
    module: str | None = None
    executable: str | None = None
    source: str | None = None
    cwd: str | None = None


# 新业务必须在这里显式注册。桌面业务示例见 README，禁止从 API 传入 EXE 路径。
BUSINESSES: dict[str, BusinessDefinition] = {
    "demo_search": BusinessDefinition(
        kind="web",
        module="business.demo_search.task",
        source="business/demo_search/task.py",
    ),
}


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
        rows.append(
            {
                "name": name,
                "kind": definition.kind,
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
