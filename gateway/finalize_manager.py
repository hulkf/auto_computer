"""一键固化流水线：录制素材 → Codex优化 → 自动注册 → 测试验证。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from core.common_utils import PROJECT_ROOT, RUNTIME_DIR, get_logger, read_json, utc_now_iso, write_json
from gateway.business_registry import register_business_in_memory, write_business_metadata_for_source
from gateway.models import FinalizeRecord, FinalizeStatus


logger = get_logger(__name__)

FINALIZE_DIR = RUNTIME_DIR / "finalize"
FINALIZE_DIR.mkdir(parents=True, exist_ok=True)

BUSINESS_DIR = PROJECT_ROOT / "business"

# 业务名安全校验（与 CodegenRecorder 保持一致）
BUSINESS_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class FinalizeManager:
    """管理录制素材到固化业务的一键流水线。"""

    def __init__(self) -> None:
        self._records: dict[str, FinalizeRecord] = {}
        self._lock = asyncio.Lock()
        self._load_existing()

    def _record_path(self, finalize_id: str) -> Path:
        return FINALIZE_DIR / f"{finalize_id}.json"

    def _load_existing(self) -> None:
        for path in FINALIZE_DIR.glob("*.json"):
            payload = read_json(path)
            if not payload:
                continue
            try:
                record = FinalizeRecord.model_validate(payload)
                self._records[record.finalize_id] = record
            except Exception:
                continue

    async def _save(self, record: FinalizeRecord) -> None:
        payload = record.model_dump(mode="json")
        write_json(self._record_path(record.finalize_id), payload)
        async with self._lock:
            self._records[record.finalize_id] = record

    async def get(self, finalize_id: str) -> FinalizeRecord | None:
        async with self._lock:
            record = self._records.get(finalize_id)
            return record.model_copy(deep=True) if record else None

    async def list_all(self) -> list[FinalizeRecord]:
        async with self._lock:
            return [r.model_copy(deep=True) for r in sorted(self._records.values(), key=lambda x: x.created_at, reverse=True)]

    async def start(
        self,
        recording_id: str,
        business_name: str,
        description: str,
        start_url: str,
        auto_test: bool = True,
        test_params: dict[str, Any] | None = None,
    ) -> FinalizeRecord:
        """启动一键固化流水线。"""

        if not BUSINESS_NAME_PATTERN.fullmatch(business_name):
            raise ValueError("业务名只能使用小写字母、数字和下划线，并以字母开头")

        # 检查录制素材是否存在
        recording_dir = self._find_recording_dir(recording_id)
        if not recording_dir:
            raise KeyError(f"录制记录不存在: {recording_id}")

        raw_script = recording_dir / "raw_codegen.py"
        if not raw_script.is_file():
            raise RuntimeError("录制素材尚未生成，请先完成录制")

        finalize_id = uuid.uuid4().hex
        record = FinalizeRecord(
            finalize_id=finalize_id,
            recording_id=recording_id,
            business_name=business_name,
            description=description,
            status=FinalizeStatus.PENDING,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        await self._save(record)

        # 后台启动流水线
        asyncio.create_task(
            self._run_pipeline(record, raw_script, start_url, auto_test, test_params or {}),
            name=f"finalize-{finalize_id}",
        )

        return record.model_copy(deep=True)

    def _find_recording_dir(self, recording_id: str) -> Path | None:
        """根据 recording_id 查找录制目录。"""

        for biz_dir in (RUNTIME_DIR / "recordings").iterdir():
            if not biz_dir.is_dir():
                continue
            for rec_dir in biz_dir.iterdir():
                if not rec_dir.is_dir():
                    continue
                session_file = rec_dir / "session.json"
                if not session_file.is_file():
                    continue
                payload = read_json(session_file)
                if payload and payload.get("recording_id") == recording_id:
                    return rec_dir
        return None

    async def _run_pipeline(
        self,
        record: FinalizeRecord,
        raw_script: Path,
        start_url: str,
        auto_test: bool,
        test_params: dict[str, Any],
    ) -> None:
        """执行完整固化流水线。"""

        try:
            # Step 1: Codex 优化
            record.status = FinalizeStatus.OPTIMIZING
            record.updated_at = utc_now_iso()
            await self._save(record)

            optimized_source = await self._optimize_with_codex(
                record, raw_script, start_url
            )
            if not optimized_source:
                record.status = FinalizeStatus.FAILED
                record.error = "Codex 优化失败，未生成有效源码"
                record.updated_at = utc_now_iso()
                await self._save(record)
                return

            # Step 2: 写入 business 目录
            biz_dir = BUSINESS_DIR / record.business_name
            biz_dir.mkdir(parents=True, exist_ok=True)
            source_path = biz_dir / "task.py"
            source_path.write_text(optimized_source, encoding="utf-8")

            # 写入 __init__.py
            init_path = biz_dir / "__init__.py"
            if not init_path.exists():
                init_path.write_text("", encoding="utf-8")

            # 写入 readme.md
            readme_path = biz_dir / "readme.md"
            if not readme_path.exists():
                readme_path.write_text(
                    self._generate_readme(record.business_name, start_url),
                    encoding="utf-8",
                )

            record.source_path = str(source_path.resolve())
            output = record.codex_output.get("output") if record.codex_output else None
            ai_summary = output.get("summary", "") if isinstance(output, dict) else ""
            write_business_metadata_for_source(
                source_path,
                description=record.description,
                ai_summary=ai_summary,
                updated_by="ai_finalize",
            )
            record.updated_at = utc_now_iso()
            await self._save(record)

            # Step 3: 注册到白名单
            record.status = FinalizeStatus.REGISTERING
            record.updated_at = utc_now_iso()
            await self._save(record)

            registered = await self._register_business(
                record.business_name,
                record.description,
                source_path,
            )
            if not registered:
                record.status = FinalizeStatus.FAILED
                record.error = "业务注册失败，请检查 gateway/business_registry.py"
                record.updated_at = utc_now_iso()
                await self._save(record)
                return

            record.registered = True
            record.updated_at = utc_now_iso()
            await self._save(record)

            # Step 4: 自动测试
            if auto_test:
                record.status = FinalizeStatus.TESTING
                record.updated_at = utc_now_iso()
                await self._save(record)

                test_result = await self._run_test(record, test_params)
                record.test_result = test_result
                record.updated_at = utc_now_iso()
                await self._save(record)

                if test_result.get("code") != 0:
                    record.status = FinalizeStatus.FAILED
                    record.error = f"自动测试失败: {test_result.get('msg', '未知错误')}"
                    record.updated_at = utc_now_iso()
                    await self._save(record)
                    return

            record.status = FinalizeStatus.COMPLETED
            record.updated_at = utc_now_iso()
            await self._save(record)
            logger.info("Finalize pipeline completed: %s -> %s", record.finalize_id, record.business_name)

        except Exception as exc:
            logger.exception("Finalize pipeline failed")
            record.status = FinalizeStatus.FAILED
            record.error = str(exc)
            record.updated_at = utc_now_iso()
            await self._save(record)

    async def _optimize_with_codex(
        self, record: FinalizeRecord, raw_script: Path, start_url: str
    ) -> str | None:
        """调用 Codex CLI 优化原始录制脚本。"""

        raw_content = raw_script.read_text(encoding="utf-8")

        # 构建隔离工作区
        workspace_root = RUNTIME_DIR / "finalize_workspaces"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix=f"finalize_{record.finalize_id}_", dir=workspace_root))

        try:
            # 复制项目结构
            shutil.copytree(
                PROJECT_ROOT,
                workspace,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "runtime", "logs", "browser_profiles", "__pycache__"
                ),
            )

            # 准备 Codex 提示
            prompt = self._build_codex_prompt(
                record.business_name,
                record.description,
                start_url,
                raw_content,
            )

            output_dir = workspace / ".finalize"
            output_dir.mkdir(parents=True, exist_ok=True)
            schema_path = output_dir / "codex_result_schema.json"
            output_path = output_dir / "result.json"

            write_json(
                schema_path,
                {
                    "type": "object",
                    "properties": {
                        "fixed": {"type": "boolean"},
                        "source": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["fixed", "source", "summary"],
                    "additionalProperties": False,
                },
            )

            codex_command = os.getenv("AUTOMATION_CODEX_COMMAND", "codex").strip()
            command = [
                codex_command,
                "exec",
                "--sandbox",
                "workspace-write",
                "--ephemeral",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                prompt,
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=float(os.getenv("AUTOMATION_HEALING_TIMEOUT_SECONDS", "600")),
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                logger.error("Codex finalize optimization timed out")
                return None

            body = read_json(output_path)
            record.codex_output = {
                "exit_code": process.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "output": body,
            }

            if process.returncode != 0:
                logger.error("Codex finalize exited with code %s", process.returncode)
                return None

            if not isinstance(body, dict) or body.get("fixed") is not True:
                logger.error("Codex did not return fixed=true for finalize")
                return None

            source = body.get("source")
            if not isinstance(source, str) or not source.strip():
                logger.error("Codex did not return valid source code")
                return None

            return source

        except Exception:
            logger.exception("Codex finalize optimization failed")
            return None
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _build_codex_prompt(
        self,
        business_name: str,
        description: str,
        start_url: str,
        raw_content: str,
    ) -> str:
        """构建 Codex 优化提示。"""

        return (
            "你是自动化中台固化执行器。请将下面的 Playwright Codegen 原始录制脚本，"
            "优化为生产就绪的固化业务脚本。\n\n"
            "要求：\n"
            "1. 使用 Playwright 异步 API (async/await)\n"
            "2. 使用 get_by_role / get_by_label / get_by_test_id 等稳定定位器，避免 CSS 选择器\n"
            "3. 添加适当的 wait_for_load_state 和 wait_for 等待\n"
            "4. 将硬编码值参数化（通过 params 字典传入）\n"
            "5. 使用 fixed_operation 包装可能失效的元素操作\n"
            "6. 返回标准 JSON 结构: {code, msg, data, screenshot}\n"
            "7. 只输出业务专属代码，不要包含浏览器初始化、异常处理等公共逻辑\n"
            "8. 函数签名必须是: async def run(params, browser_pool, *, task_id)\n"
            f"9. 业务名: {business_name}\n"
            f"10. 项目作用: {description}\n"
            f"11. 起始网址: {start_url}\n\n"
            "原始脚本：\n"
            "```python\n"
            f"{raw_content}\n"
            "```\n\n"
            "请返回 JSON 格式: {\"fixed\": true, \"source\": \"优化后的完整源码\", \"summary\": \"优化说明\"}"
        )

    async def _register_business(
        self,
        business_name: str,
        description: str,
        source_path: Path,
    ) -> bool:
        """自动注册业务到白名单文件。"""

        try:
            registry_path = PROJECT_ROOT / "gateway" / "business_registry.py"
            if not registry_path.is_file():
                return False

            content = registry_path.read_text(encoding="utf-8")

            # 检查是否已注册
            if f'"{business_name}"' in content:
                logger.info("Business already registered: %s", business_name)
                register_business_in_memory(
                    business_name,
                    description=description,
                    module=f"business.{business_name}.task",
                    source=source_path.relative_to(PROJECT_ROOT).as_posix(),
                )
                return True

            # 构建注册条目
            relative_source = source_path.relative_to(PROJECT_ROOT).as_posix()
            module_path = f"business.{business_name}.task"

            new_entry = (
                f'    "{business_name}": BusinessDefinition(\n'
                f'        kind="web",\n'
                f"        description={description!r},\n"
                f'        module="{module_path}",\n'
                f'        source="{relative_source}",\n'
                f"    ),\n"
            )

            # 在 BUSINESSES 字典中插入新条目
            # 找到 "BUSINESSES: dict[str, BusinessDefinition] = {" 后的第一个条目，在其前面插入
            pattern = r'(BUSINESSES: dict\[str, BusinessDefinition\] = \{\n)(\s+"\w+": BusinessDefinition\()'
            replacement = r'\1' + new_entry + r'\2'

            new_content = re.sub(pattern, replacement, content, count=1)

            if new_content == content:
                # 备选：在 "}" 之前插入
                pattern2 = r'(\n)(\n\n\ndef get_business\()'
                replacement2 = new_entry + r'\1\2'
                new_content = re.sub(pattern2, replacement2, content, count=1)

            if new_content != content:
                registry_path.write_text(new_content, encoding="utf-8")
                register_business_in_memory(
                    business_name,
                    description=description,
                    module=module_path,
                    source=relative_source,
                )
                logger.info("Business registered: %s", business_name)
                return True

            return False

        except Exception:
            logger.exception("Failed to register business")
            return False

    async def _run_test(
        self, record: FinalizeRecord, test_params: dict[str, Any]
    ) -> dict[str, Any]:
        """运行自动测试任务。"""

        try:
            # 延迟导入避免循环依赖
            from gateway.main import task_manager

            from gateway.models import TaskRequest

            request = TaskRequest(
                kind="web",
                business=record.business_name,
                params=test_params or {"query": "test", "limit": 3, "profile": "default"},
                timeout_seconds=60,
                max_retries=0,
                enable_self_healing=False,
            )

            task_record = await task_manager.submit(request)
            record.test_task_id = task_record.task_id
            await self._save(record)

            # 等待任务完成（最多 90 秒）
            for _ in range(90):
                await asyncio.sleep(1)
                updated = await task_manager.get(task_record.task_id)
                if updated and updated.status in {"succeeded", "failed", "healed_pending_review"}:
                    return updated.result or {"code": 500, "msg": "无结果"}

            return {"code": 504, "msg": "测试任务超时"}

        except Exception as exc:
            return {"code": 500, "msg": f"测试执行异常: {exc}"}

    def _generate_readme(self, business_name: str, start_url: str) -> str:
        """生成业务 readme 模板。"""

        return (
            f"# {business_name} 业务\n\n"
            f"该业务通过 Playwright 自动化执行网页操作。\n\n"
            f"起始网址: {start_url}\n\n"
            "## 参数\n\n"
            "| 参数 | 必填 | 默认值 | 说明 |\n"
            "|---|---:|---:|---|\n"
            "| (待补充) | | | |\n\n"
            "## 调用示例\n\n"
            "```powershell\n"
            "$body = @{\n"
            f'    kind = "web"\n'
            f'    business = "{business_name}"\n'
            "    params = @{ }\n"
            "} | ConvertTo-Json -Depth 5\n\n"
            'Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/tasks" -ContentType "application/json" -Body $body\n'
            "```\n"
        )
