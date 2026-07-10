"""公共协议和持久化工具的快速契约测试。"""

from core.common_utils import read_json, result, write_json


def test_result_has_exact_standard_shape() -> None:
    payload = result(data={"ok": True})
    assert payload == {
        "code": 0,
        "msg": "success",
        "data": {"ok": True},
        "screenshot": None,
    }


def test_json_round_trip(tmp_path) -> None:
    path = tmp_path / "nested" / "state.json"
    write_json(path, {"中文": "正常", "count": 2})
    assert read_json(path) == {"中文": "正常", "count": 2}

