"""Playwright Codegen recorder shared by the local gateway and future tools."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, IO
from urllib.parse import quote, urlparse

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
    replay_status: str = "untested"
    replayed_at: str | None = None
    replay_result: dict[str, Any] | None = None

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
        """Build Codegen command with a stable bootstrap page before target navigation."""

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
        # Direct Codegen navigation can exit with net::ERR_ABORTED when a site redirects
        # or the user acts before the initial load finishes. A tiny page loads first and
        # redirects only after Inspector/recording startup has settled.
        bootstrap_html = (
            "<!doctype html><meta charset='utf-8'><title>启动录制</title>"
            "<p>Codegen 已启动，正在打开目标网页……</p>"
            "<script>setTimeout(() => window.location.replace("
            f"{json.dumps(start_url)}), 800);</script>"
        )
        command.append(f"data:text/html;charset=utf-8,{quote(bootstrap_html)}")
        return command

    def _session_dir(self, session: RecordingSession) -> Path:
        return Path(session.raw_script).parent

    @staticmethod
    def _prepare_replay_script(raw_script: Path, start_url: str | None = None) -> bool:
        """Patch small Codegen output gaps before running a replay test.

        Playwright Codegen occasionally writes the first target navigation as
        ``await page.goto(...)`` but omits the initial ``page = await
        context.new_page()`` line. That makes a valid recording fail instantly
        during replay with ``NameError: name 'page' is not defined``. The raw
        script is only a temporary recording artifact, so it is safe to repair
        this deterministic bootstrap gap before replay and later hardening.

        Returns True when the file was changed.
        """

        source = raw_script.read_text(encoding="utf-8")
        original_source = source
        if start_url and "data:text/html" in source:
            # The recorder starts Codegen on a tiny bootstrap data URL to avoid
            # first-load flakiness. Sometimes Codegen records that bootstrap
            # navigation and meaningless html/body clicks. Replays should start
            # from the real user URL instead.
            source = re.sub(
                r'(?m)^(?P<indent>\s*)await\s+page\.goto\("data:text/html[^"\n]*"\)\s*$',
                lambda match: f'{match.group("indent")}await page.goto({json.dumps(start_url)})',
                source,
                count=1,
            )
            source = "".join(
                line
                for line in source.splitlines(keepends=True)
                if not re.match(r'^\s*await\s+page\.locator\("(?:html|body)"\)\.click\(\)\s*$', line)
            )
        if start_url:
            # The first navigation only needs the DOM to be ready for the next
            # recorded click. Waiting for the full load event is brittle on
            # pages with slow ads, analytics, or long-polling resources.
            start_url_pattern = re.escape(start_url)
            source = re.sub(
                rf'(?m)^(?P<indent>\s*)await\s+page\.goto\((?P<quote>["\']){start_url_pattern}(?P=quote)\)\s*$',
                lambda match: (
                    f'{match.group("indent")}await page.goto('
                    f'{json.dumps(start_url)}, wait_until="domcontentloaded", timeout=60000)'
                ),
                source,
                count=1,
            )
        # If Codegen already created the first page, keep the user's generated
        # material untouched. This helper only fills the missing bootstrap line.
        if "page = await context.new_page()" in source or "page = context.pages[0]" in source:
            if source != original_source:
                raw_script.write_text(source, encoding="utf-8")
                return True
            return False
        if "await page." not in source:
            if source != original_source:
                raw_script.write_text(source, encoding="utf-8")
                return True
            return False

        lines = source.splitlines(keepends=True)
        repaired: list[str] = []
        changed = False
        for line in lines:
            repaired.append(line)
            if changed:
                continue
            # The common async Python Codegen shape is:
            #   context = await browser.new_context()
            #   await page.goto(...)
            # Insert the missing page creation immediately after context setup.
            if re.match(r"^(?P<indent>\s*)context\s*=\s*await\s+browser\.new_context\(", line):
                indent = re.match(r"^(\s*)", line).group(1)
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                repaired.append(f"{indent}page = await context.new_page(){newline}")
                changed = True

        if changed or source != original_source:
            raw_script.write_text("".join(repaired), encoding="utf-8")
        return changed or source != original_source

    def _save_session(self, session: RecordingSession) -> None:
        write_json(self._session_dir(session) / "session.json", session.to_dict())

    def _load_session_by_id(self, recording_id: str) -> RecordingSession | None:
        for manifest in RECORDING_DIR.glob("*/*/session.json"):
            payload = read_json(manifest)
            if not isinstance(payload, dict) or payload.get("recording_id") != recording_id:
                continue
            try:
                return RecordingSession(**payload)
            except TypeError:
                return None
        return None

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
            codegen_env = {
                **os.environ,
                # Keep Chinese locator text intact in generated raw_codegen.py
                # on Windows terminals whose default code page is not UTF-8.
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }

            try:
                process = await asyncio.create_subprocess_exec(
                    *self._build_command(raw_script, profile_dir, start_url),
                    cwd=str(session_dir),
                    env=codegen_env,
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

    async def test_recording(
        self,
        recording_id: str,
        *,
        timeout_seconds: float = 300,
    ) -> RecordingSession:
        """Replay the saved raw Codegen script once before production hardening."""

        async with self._lock:
            session = self._session if self._session and self._session.recording_id == recording_id else None
            if not session:
                session = self._load_session_by_id(recording_id)
            if not session:
                raise KeyError(f"录制记录不存在: {recording_id}")
            if session.status != "completed" or not session.output_ready:
                raise RuntimeError("录制素材尚未保存成功，不能测试")
            if self._process and self._process.returncode is None:
                raise RuntimeError("录制仍在进行中，请先停止录制")
            session.replay_status = "testing"
            session.replayed_at = utc_now_iso()
            session.replay_result = None
            session.error = None
            self._save_session(session)
            if self._session and self._session.recording_id == recording_id:
                self._session = session

        session_dir = self._session_dir(session)
        stdout_path = session_dir / "replay.stdout.log"
        stderr_path = session_dir / "replay.stderr.log"
        env = {**os.environ, "PYTHONUTF8": "1"}
        repaired_script = self._prepare_replay_script(Path(session.raw_script), session.start_url)

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                session.raw_script,
                cwd=str(session_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
                exit_code = process.returncode
                timed_out = False
            except TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
                exit_code = process.returncode
                timed_out = True
        except Exception as exc:
            stdout = b""
            stderr = str(exc).encode("utf-8", errors="replace")
            exit_code = 500
            timed_out = False

        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        replay_result = {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "repaired_script": repaired_script,
        }

        async with self._lock:
            fresh = self._load_session_by_id(recording_id) or session
            fresh.replayed_at = utc_now_iso()
            fresh.replay_result = replay_result
            if timed_out:
                fresh.replay_status = "failed"
                fresh.error = f"录制回放测试超时，超过 {timeout_seconds:g} 秒"
            elif exit_code == 0:
                fresh.replay_status = "passed"
                fresh.error = None
            else:
                fresh.replay_status = "failed"
                fresh.error = f"录制回放测试失败，exit_code={exit_code}"
            self._save_session(fresh)
            if self._session and self._session.recording_id == recording_id:
                self._session = fresh
            return RecordingSession(**fresh.to_dict())

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
