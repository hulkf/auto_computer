"""AHK 编译后 EXE 的统一异步调用封装。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .common_utils import get_logger, result


class AhkRunner:
    """安全启动 AHK EXE，透传参数并统一采集退出码、标准输出和错误输出。"""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    async def run(
        self,
        executable: str,
        args: list[str] | None = None,
        *,
        timeout_seconds: float = 300,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """等待进程结束并返回标准 JSON；不使用 shell，避免参数注入。"""

        exe_path = Path(executable).expanduser().resolve()
        if not exe_path.is_file() or exe_path.suffix.lower() != ".exe":
            return result(code=400, msg=f"AHK executable not found or not .exe: {exe_path}")
        work_dir = Path(cwd).expanduser().resolve() if cwd else exe_path.parent
        command = [str(exe_path), *(str(value) for value in (args or []))]
        self.logger.info("Starting AHK executable: %s", exe_path)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            self.logger.error("AHK executable timed out: %s", exe_path)
            return result(code=504, msg=f"AHK task timed out after {timeout_seconds}s")
        except Exception as exc:
            self.logger.exception("Failed to run AHK executable")
            return result(code=500, msg=str(exc))

        payload = {
            "executable": str(exe_path),
            "args": args or [],
            "exit_code": process.returncode,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        }
        if process.returncode == 0:
            return result(data=payload)
        self.logger.error("AHK executable exited with code %s", process.returncode)
        return result(code=process.returncode or 500, msg="AHK executable failed", data=payload)
