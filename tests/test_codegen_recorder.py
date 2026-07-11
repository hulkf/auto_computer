"""Codegen recorder validation and process lifecycle tests."""

import asyncio
from pathlib import Path
from urllib.parse import unquote

import pytest

import core.codegen_recorder as recorder_module
from core.codegen_recorder import CodegenRecorder


def test_recording_request_rejects_unsafe_paths_and_urls() -> None:
    with pytest.raises(ValueError):
        CodegenRecorder.validate_request("../unsafe", "https://example.com", "default")
    with pytest.raises(ValueError):
        CodegenRecorder.validate_request("safe_name", "file:///secret", "default")
    with pytest.raises(ValueError):
        CodegenRecorder.validate_request("safe_name", "https://example.com", "../profile")


def test_codegen_command_uses_async_python_and_persistent_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTOMATION_BROWSER_CHANNEL", "chrome")
    output = tmp_path / "raw.py"
    profile = tmp_path / "profile"

    command = CodegenRecorder._build_command(output, profile, "https://example.com")

    assert "--target=python-async" in command
    assert f"--output={output}" in command
    assert f"--user-data-dir={profile}" in command
    assert command[-1].startswith("data:text/html;charset=utf-8,")
    assert "https://example.com" in unquote(command[-1])
    assert "setTimeout" in unquote(command[-1])


def test_recorder_starts_and_stops_one_visible_process(monkeypatch, tmp_path) -> None:
    commands: list[tuple[str, ...]] = []

    class FakeProcess:
        def __init__(self, output: Path) -> None:
            self.returncode = None
            self.finished = asyncio.Event()
            self.output = output

        async def wait(self) -> int:
            await self.finished.wait()
            return int(self.returncode)

        def terminate(self) -> None:
            # Windows may report a non-zero code for an intentional termination.
            self.output.write_text("from playwright.async_api import async_playwright\n", encoding="utf-8")
            self.returncode = 1
            self.finished.set()

        def kill(self) -> None:
            self.terminate()

    async def fake_subprocess(*command: str, **kwargs):
        commands.append(command)
        output_arg = next(item for item in command if item.startswith("--output="))
        return FakeProcess(Path(output_arg.removeprefix("--output=")))

    monkeypatch.setattr(recorder_module, "RECORDING_DIR", tmp_path / "recordings")
    monkeypatch.setattr(recorder_module, "BROWSER_PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    async def scenario() -> None:
        recorder = CodegenRecorder()
        started = await recorder.start("demo_record", "https://example.com", "login")
        assert started.status == "recording"
        with pytest.raises(RuntimeError):
            await recorder.start("second_record", "https://example.com", "login")
        stopped = await recorder.stop()
        assert stopped.status == "completed"
        assert stopped.output_ready is True
        assert Path(stopped.raw_script).name == "raw_codegen.py"

    asyncio.run(scenario())
    assert commands
