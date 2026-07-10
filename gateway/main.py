"""FastAPI 统一调度入口；生产环境只通过本模块调用固化业务。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from core.common_utils import PROJECT_ROOT, get_logger, result

# 统一从项目根目录加载本地配置，生产环境已有环境变量会保持优先。
load_dotenv(PROJECT_ROOT / ".env")

from core.playwright_base import BrowserContextPool
from gateway.business_registry import list_businesses
from gateway.models import BatchTaskRequest, TaskRequest
from gateway.task_manager import TaskManager


browser_pool = BrowserContextPool()
task_manager = TaskManager(browser_pool)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """网关生命周期：Playwright 按需启动，停机时统一释放后台资源。"""

    app.state.task_manager = task_manager
    yield
    await task_manager.shutdown()


app = FastAPI(
    title="Local Automation Orchestration Platform",
    version="1.0.0",
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
