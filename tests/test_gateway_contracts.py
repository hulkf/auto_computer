"""业务白名单、请求校验和 AHK 错误协议测试。"""

import asyncio

import pytest
from pydantic import ValidationError

from core.ahk_runner import AhkRunner
from gateway.business_registry import get_business_source, list_businesses, load_web_business
from gateway.models import FinalizeRecord, TaskRequest
from gateway.self_healer import SelfHealer
import gateway.business_registry as business_registry


def test_demo_business_is_registered() -> None:
    source = get_business_source("demo_search")
    assert source.name == "task.py"
    assert source.parent.name == "demo_search"
    assert callable(load_web_business("demo_search"))


def test_business_list_includes_project_description() -> None:
    demo = next(item for item in list_businesses() if item["name"] == "demo_search")

    assert demo["description"] == "演示如何通过统一中台执行 Bing 搜索并提取结果标题。"


def test_legacy_finalize_record_does_not_require_description() -> None:
    record = FinalizeRecord.model_validate(
        {
            "finalize_id": "legacy",
            "recording_id": "recording",
            "business_name": "legacy_business",
            "status": "completed",
            "created_at": "2026-07-11T00:00:00+00:00",
            "updated_at": "2026-07-11T00:00:00+00:00",
        }
    )

    assert record.description == ""


def test_business_metadata_updates_preserve_user_description(monkeypatch, tmp_path) -> None:
    source = tmp_path / "business" / "sample" / "task.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")
    monkeypatch.setattr(business_registry, "PROJECT_ROOT", tmp_path)

    business_registry.write_business_metadata_for_source(
        source,
        description="用户填写的项目作用",
        updated_by="user",
    )
    metadata = business_registry.write_business_metadata_for_source(
        source,
        ai_summary="AI完成了选择器修复",
        updated_by="ai_self_heal",
    )

    assert metadata["description"] == "用户填写的项目作用"
    assert metadata["description_updated_by"] == "user"
    assert metadata["ai_summary"] == "AI完成了选择器修复"
    assert metadata["ai_summary_updated_by"] == "ai_self_heal"


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
