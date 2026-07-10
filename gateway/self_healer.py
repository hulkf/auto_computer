"""Codex 自愈回调适配器：采集失败证据并请求外部执行器永久修改业务源码。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from core.common_utils import get_logger, save_snapshot


class SelfHealer:
    """调用用户部署的 Codex 执行器；未配置 URL 时只落盘修复上下文。"""

    def __init__(self) -> None:
        self.url = os.getenv("AUTOMATION_HEALING_URL", "").strip()
        self.token = os.getenv("AUTOMATION_HEALING_TOKEN", "").strip()
        self.timeout = float(os.getenv("AUTOMATION_HEALING_TIMEOUT_SECONDS", "600"))
        self.logger = get_logger(__name__)

    async def repair(
        self,
        *,
        task_id: str,
        business: str,
        source_path: Path,
        error_traceback: str,
        screenshot: str | None,
        params: dict[str, Any],
    ) -> bool:
        """发送固定修复契约；仅当执行器明确返回 fixed=true 才允许自动重跑。"""

        payload = {
            "task_id": task_id,
            "business": business,
            "business_source": str(source_path.resolve()),
            "error_traceback": error_traceback,
            "screenshot": screenshot,
            "params": params,
            "instruction": (
                "只修改 business_source 对应业务源码，必须复用 core 公共层；"
                "网页业务完成后执行语法检查；AHK 业务还必须重新编译已注册 EXE；"
                "验证完成后返回 fixed=true。"
            ),
        }
        evidence_path = save_snapshot("healing", task_id, payload)
        self.logger.info("Self-healing evidence saved: %s", evidence_path)
        if not self.url:
            self.logger.warning("AUTOMATION_HEALING_URL is empty; repair callback skipped")
            return False

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except Exception:
            self.logger.exception("Codex self-healing callback failed")
            return False
        fixed = bool(body.get("fixed") or body.get("data", {}).get("fixed"))
        self.logger.info("Codex self-healing callback completed, fixed=%s", fixed)
        return fixed
