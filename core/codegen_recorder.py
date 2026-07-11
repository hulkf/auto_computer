"""Playwright Codegen recorder shared by the local gateway and future tools."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, IO
from urllib.parse import urlparse

from .common_utils import BROWSER_PROFILE_DIR, RUNTIME_DIR, get_logger, read_json, utc_now_iso, write_json


BUSINESS_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RECORDING_DIR = RUNTIME_DIR / "recordings"


@dataclass
class RecordingSession:
    """Serializable state for one visible Codegen recording process."""

    recording_id: str
    business_name: str
    start_url: str
    profile: str
    status: str
    started_at: str
    finished_at: str | None
    raw_script: str
    stdout_log: str
    stderr_log: str
    exit_code: int | None = None
    output_ready: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodegenRecorder:
    """Manage the single interactive Codegen session allowed by the gateway."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._session: RecordingSession | None = self._load_latest_session()
        self._monitor: asyncio.Task[None] | None = None
        self._intentional_stops: set[asyncio.subprocess.Process] = set()

    @staticmethod
    def _load_latest_session() -> RecordingSession | None:
        """Restore the latest saved recording metadata after a gateway restart."""

        manifests = list(RECORDING_DIR.glob("*/*/session.json"))
        if not manifests:
            return None
        payload = read_json(max(manifests, key=lambda path: path.stat().st_mtime))
        if not isinstance(payload, dict):
            return None
        try:
            session = RecordingSession(**payload)
        except TypeError:
            return None
        if session.status == "recording":
            session.status = "failed"
            session.finished_at = utc_now_iso()
            session.error = "网关重启导致录制中断"
        return session

    @staticmethod
    def validate_request(business_name: str, start_url: str, profile: str) -> None:
        """Reject unsafe names and non-web URLs before creating paths or processes."""

        if not BUSINESS_NAME_PATTERN.fullmatch(business_name):
            raise ValueError("业务名只能使用小写字母、数字和下划线，并以字母开头")
        if not PROFILE_PATTERN.fullmatch(profile):
            raise ValueError("Profile 只能使用字母、数字、下划线和短横线")
        parsed = urlparse(start_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("起始网址必须是完整的 http/https 地址")

    @staticmethod
    def _build_command(output: Path, profile_dir: Path, start_url: str) -> list[str]:
        """Build the local Python Codegen command without shell interpolation."""

        command = [
            sys.executable,
            "-m",
            "playwright",
            "codegen",
            "--target=python-async",
            f"--output={output}",
            f"--user-data-dir={profile_dir}",
        ]
        channel = os.getenv("AUTOMATION_BROWSER_CHANNEL", "chrome").strip()
        if channel:
            command.append(f"--channel={channel}")
        command.append(start_url)
        return command

    def _session_dir(self, session: RecordingSession) -> Path:
        return Path(session.raw_script).parent

    def _save_session(self, session: RecordingSession) -> None:
        write_json(self._session_dir(session) / "session.json", session.to_dict())

    async def start(self, business_name: str, start_url: str, profile: str) -> RecordingSession:
        """Launch visible Codegen and return immediately while the user records."""

        self.validate_request(business_name, start_url, profile)
        async with self._lock:
            if self._process and self._process.returncode is None:
                raise RuntimeError("已有录制正在进行，请先停止当前录制")

            recording_id = uuid.uuid4().hex
            session_dir = (RECORDING_DIR / business_name / recording_id).resolve()
            session_dir.mkdir(parents=True, exist_ok=False)
            raw_script = session_dir / "raw_codegen.py"
            stdout_path = session_dir / "codegen.stdout.log"
            stderr_path = session_dir / "codegen.stderr.log"
            profile_dir = (BROWSER_PROFILE_DIR / "recordings" / profile).resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            stdout_stream = stdout_path.open("wb")
            stderr_stream = stderr_path.open("wb")

            try:
                process = await asyncio.create_subprocess_exec(
                    *self._build_command(raw_script, profile_dir, start_url),
                    cwd=str(session_dir),
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                )
            except Exception:
                stdout_stream.close()
                stderr_stream.close()
                raise

            self._process = process
            self._session = RecordingSession(
                recording_id=recording_id,
                business_name=business_name,
                start_url=start_url,
                profile=profile,
                status="recording",
                started_at=utc_now_iso(),
                finished_at=None,
                raw_script=str(raw_script),
                stdout_log=str(stdout_path),
                stderr_log=str(stderr_path),
            )
            self._save_session(self._session)
            self._monitor = asyncio.create_task(
                self._watch(process, self._session, (stdout_stream, stderr_stream)),
                name=f"codegen-{recording_id}",
            )
            self.logger.info("Codegen recording started: %s", recording_id)
            return RecordingSession(**self._session.to_dict())

    async def _watch(
        self,
        process: asyncio.subprocess.Process,
        session: RecordingSession,
        streams: tuple[IO[bytes], IO[bytes]],
    ) -> None:
        """Persist terminal state when the user closes Codegen without using the API."""

        exit_code = await process.wait()
        async with self._lock:
            self._close_streams(streams)
            session.exit_code = exit_code
            session.finished_at = utc_now_iso()
            output = Path(session.raw_script)
            session.output_ready = output.is_file() and output.stat().st_size > 0
            normal_exit = exit_code == 0 or process in self._intentional_stops
            self._intentional_stops.discard(process)
            session.status = "completed" if normal_exit and session.output_ready else "failed"
            if not session.output_ready:
                session.error = "Codegen 未生成有效脚本，请查看错误日志后重新录制"
            elif not normal_exit:
                session.error = f"Codegen 异常退出，exit_code={exit_code}"
            self._save_session(session)

    @staticmethod
    def _close_streams(streams: tuple[IO[bytes], IO[bytes]]) -> None:
        for stream in streams:
            stream.close()

    async def status(self) -> RecordingSession | None:
        """Return a detached copy of the current or most recent session."""

        async with self._lock:
            return RecordingSession(**self._session.to_dict()) if self._session else None

    async def stop(self) -> RecordingSession:
        """Stop Codegen, wait for output flush, and return saved recording paths."""

        async with self._lock:
            if not self._process or not self._session:
                raise RuntimeError("当前没有录制会话")
            process = self._process
            session = self._session
            if process.returncode is None:
                self._intentional_stops.add(process)
                process.terminate()

        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._monitor:
            await self._monitor
        return RecordingSession(**session.to_dict())

    async def shutdown(self) -> None:
        """Close an active recorder when the gateway shuts down."""

        if self._process and self._process.returncode is None:
            await self.stop()
