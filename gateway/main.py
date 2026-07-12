"""FastAPI 统一调度入口；生产环境只通过本模块调用固化业务。

新增接口：
- POST /api/v1/recordings/{recording_id}/test      回放测试录制素材
- POST /api/v1/recordings/{recording_id}/finalize  一键固化录制素材
- GET /api/v1/finalize/{finalize_id}               查询固化流水线状态
- GET /api/v1/finalize                              列出所有固化记录
- POST /api/v1/tasks/{task_id}/review               人工审批自愈修复
- GET /api/v1/tasks                                任务列表（支持筛选分页）
- GET /api/v1/businesses/{business}/health          业务健康度指标
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from core.common_utils import PROJECT_ROOT, get_logger, result
from core.codegen_recorder import CodegenRecorder

# 统一从项目根目录加载本地配置，生产环境已有环境变量会保持优先。
load_dotenv(PROJECT_ROOT / ".env")

from core.playwright_base import BrowserContextPool
from gateway.business_registry import list_businesses, update_business_metadata
from gateway.finalize_manager import FinalizeManager
from gateway.models import (
    BatchTaskRequest,
    BusinessDescriptionUpdate,
    FinalizeRecordingRequest,
    RecordingStartRequest,
    ReviewHealRequest,
    TaskRequest,
)
from gateway.task_manager import TaskManager


browser_pool = BrowserContextPool()
task_manager = TaskManager(browser_pool)
codegen_recorder = CodegenRecorder()
finalize_manager = FinalizeManager()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """网关生命周期：Playwright 按需启动，停机时统一释放后台资源。"""

    app.state.task_manager = task_manager
    app.state.codegen_recorder = codegen_recorder
    app.state.finalize_manager = finalize_manager
    yield
    await codegen_recorder.shutdown()
    await task_manager.shutdown()


app = FastAPI(
    title="Local Automation Orchestration Platform",
    version="1.1.0",
    description="网页 Playwright 与桌面 AHK 业务的本地统一调度、自愈和审计网关。",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """将 Pydantic/FastAPI 校验错误也转换为中台统一结构。"""

    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(result(code=422, msg="请求参数错误", data=exc.errors())),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """将 404/409 等显式 HTTP 错误转换为中台统一结构。"""

    return JSONResponse(status_code=exc.status_code, content=result(code=exc.status_code, msg=str(exc.detail)))


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """API 最外层兜底，防止任何异常绕过统一 JSON 协议。"""

    logger.exception("Unhandled gateway exception")
    return JSONResponse(status_code=500, content=result(code=500, msg=str(exc)))


@app.get("/health")
async def health() -> dict[str, Any]:
    """供后台守护进程或监控探测网关存活。"""

    return result(data={"status": "ok", "service": "automation-gateway"})


@app.get("/api/v1/businesses")
async def businesses() -> dict[str, Any]:
    """查看显式接入网关的固化业务白名单。"""

    return result(data=list_businesses())


@app.put("/api/v1/businesses/{business_name}/description")
async def save_business_description(
    business_name: str,
    request: BusinessDescriptionUpdate,
) -> dict[str, Any]:
    """Save a user-edited project purpose for a registered business."""

    try:
        update_business_metadata(
            business_name,
            description=request.description,
            updated_by="user",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    business = next(item for item in list_businesses() if item["name"] == business_name)
    return result(msg="项目作用已保存", data=business)


@app.get("/api/v1/businesses/{business}/health")
async def business_health(business: str) -> dict[str, Any]:
    """查询业务健康度指标。"""

    try:
        metrics = await task_manager.get_business_health(business)
        return result(data=metrics.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/recordings/start", status_code=201)
async def start_recording(request: RecordingStartRequest) -> dict[str, Any]:
    """Launch visible Playwright Codegen for a new business draft."""

    try:
        session = await codegen_recorder.start(
            request.business_name,
            request.start_url,
            request.profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result(msg="录制窗口已启动", data=session.to_dict())


@app.get("/api/v1/recordings/current")
async def current_recording() -> dict[str, Any]:
    """Query the active or most recent recorder session."""

    session = await codegen_recorder.status()
    return result(data=session.to_dict() if session else None)


@app.post("/api/v1/recordings/stop")
async def stop_recording() -> dict[str, Any]:
    """Stop Codegen and expose the saved raw script path."""

    try:
        session = await codegen_recorder.stop()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    message = "录制已停止，原始脚本已保存" if session.output_ready else "录制已停止，但未生成有效脚本"
    return result(msg=message, data=session.to_dict())


@app.post("/api/v1/recordings/{recording_id}/test")
async def test_recording(recording_id: str) -> dict[str, Any]:
    """回放测试原始录制脚本；通过后才允许固化。"""

    try:
        session = await codegen_recorder.test_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    message = "录制回放测试通过" if session.replay_status == "passed" else "录制回放测试失败"
    return result(msg=message, data=session.to_dict())


@app.post("/api/v1/recordings/{recording_id}/finalize", status_code=202)
async def finalize_recording(recording_id: str, request: FinalizeRecordingRequest) -> dict[str, Any]:
    """一键固化录制素材：Codex优化 → 注册 → 测试。"""

    try:
        record = await finalize_manager.start(
            recording_id=recording_id,
            business_name=request.business_name,
            description=request.description,
            start_url=request.start_url,
            auto_test=request.auto_test,
            test_params=request.test_params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result(msg="固化流水线已启动", data=record.model_dump(mode="json"))


@app.get("/api/v1/finalize/{finalize_id}")
async def get_finalize(finalize_id: str) -> dict[str, Any]:
    """查询一键固化流水线状态。"""

    record = await finalize_manager.get(finalize_id)
    if not record:
        raise HTTPException(status_code=404, detail="固化记录不存在")
    return result(data=record.model_dump(mode="json"))


@app.get("/api/v1/finalize")
async def list_finalize() -> dict[str, Any]:
    """列出所有固化流水线记录。"""

    records = await finalize_manager.list_all()
    return result(data=[r.model_dump(mode="json") for r in records])


@app.post("/api/v1/tasks", status_code=202)
async def submit_task(request: TaskRequest) -> dict[str, Any]:
    """提交单个网页或桌面任务并立即返回任务 ID。"""

    try:
        record = await task_manager.submit(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result(msg="任务已进入队列", data=record.model_dump(mode="json"))


@app.post("/api/v1/tasks/batch", status_code=202)
async def submit_batch(request: BatchTaskRequest) -> dict[str, Any]:
    """批量提交任务；任何白名单错误都在提交对应子任务时明确返回。"""

    records = []
    for task_request in request.tasks:
        try:
            record = await task_manager.submit(task_request)
            records.append(result(data=record.model_dump(mode="json")))
        except Exception as exc:
            records.append(result(code=400, msg=str(exc)))
    return result(msg="批量任务已处理", data=records)


@app.get("/api/v1/tasks")
async def list_tasks(
    status: str | None = None,
    business: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列出任务，支持状态、业务、类型筛选和分页。"""

    records = await task_manager.list_all_tasks(
        status=status, business=business, kind=kind, limit=limit, offset=offset
    )
    return result(data=[r.model_dump(mode="json") for r in records])


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、尝试次数、结果及失败证据。"""

    record = await task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    return result(data=record.model_dump(mode="json"))


@app.post("/api/v1/tasks/{task_id}/retry", status_code=202)
async def retry_task(task_id: str) -> dict[str, Any]:
    """为已结束任务创建一个关联的新任务。"""

    try:
        record = await task_manager.retry(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result(msg="重试任务已进入队列", data=record.model_dump(mode="json"))


@app.post("/api/v1/tasks/{task_id}/review")
async def review_heal(task_id: str, request: ReviewHealRequest) -> dict[str, Any]:
    """人工审批或拒绝自愈修复。"""

    try:
        record = await task_manager.review_heal(task_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    action = "已审批通过" if request.approved else "已拒绝并回滚"
    return result(msg=f"自愈修复{action}", data=record.model_dump(mode="json"))


@app.get("/", include_in_schema=False)
async def console_root() -> RedirectResponse:
    """默认打开本地自动化控制台。"""

    return RedirectResponse(url="/console/")


@app.get("/console", include_in_schema=False)
async def console_entry() -> RedirectResponse:
    """兼容无尾斜杠访问。"""

    return RedirectResponse(url="/console/")


app.mount(
    "/console",
    StaticFiles(directory=PROJECT_ROOT / "frontend", html=True),
    name="console",
)
