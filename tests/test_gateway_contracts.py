"""业务白名单、请求校验和 AHK 错误协议测试。"""

import asyncio

import pytest
from pydantic import ValidationError

from core.ahk_runner import AhkRunner
from gateway.business_registry import get_business_source, load_web_business
from gateway.models import TaskRequest
from gateway.self_healer import SelfHealer


def test_demo_business_is_registered() -> None:
    source = get_business_source("demo_search")
    assert source.name == "task.py"
    assert source.parent.name == "demo_search"
    assert callable(load_web_business("demo_search"))


def test_unknown_business_is_rejected() -> None:
    with pytest.raises(KeyError):
        get_business_source("../../unsafe")


def test_web_request_requires_business() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(kind="web")


def test_desktop_request_requires_registered_business_name() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(kind="desktop")


def test_self_healer_requires_real_boolean_true() -> None:
    assert SelfHealer._is_fixed({"fixed": True}) is True
    assert SelfHealer._is_fixed({"fixed": "true"}) is False
    assert SelfHealer._is_fixed({"data": None}) is False
    assert SelfHealer._is_fixed([]) is False


def test_self_healer_applies_only_returned_business_file(tmp_path) -> None:
    source = tmp_path / "task.py"
    source.write_text("old", encoding="utf-8")
    healer = SelfHealer()
    applied = healer._apply_http_candidate(
        {"fixed": True, "source_content": "new"}, source, None
    )
    assert applied is True
    assert source.read_text(encoding="utf-8") == "new"


def test_ahk_runner_returns_standard_error_for_missing_exe(tmp_path) -> None:
    payload = asyncio.run(AhkRunner().run(str(tmp_path / "missing.exe")))
    assert set(payload) == {"code", "msg", "data", "screenshot"}
    assert payload["code"] == 400
