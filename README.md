# 本地统一自动化调度自愈中台

本项目是后续网页自动化与桌面自动化业务的唯一公共底座。生产任务只能通过 FastAPI 网关调用已固化、已注册的业务，不能以 Browser MCP 或 Playwright MCP 代替业务脚本长期运行。

## 三层目录

```text
auto_computer/
├─ core/                              # 底层公共能力层（全业务复用）
│  ├─ __init__.py
│  ├─ playwright_base.py             # 持久 Chrome、上下文池、等待、重试、截图、统一异常
│  ├─ ai_browser.py                  # 元素失效后的定位诊断工具箱
│  ├─ ahk_runner.py                  # AHK EXE 参数透传、等待、输出与错误采集
│  └─ common_utils.py                # 日志、路径、目录、JSON、快照、统一返回体
├─ business/                          # 固化业务脚本层（一项业务一个目录）
│  ├─ __init__.py
│  └─ demo_search/
│     ├─ __init__.py
│     ├─ task.py                     # 仅包含 Bing 搜索专属步骤
│     └─ readme.md                   # 入参与调用示例
├─ gateway/                           # 调度网关层
│  ├─ __init__.py
│  ├─ main.py                        # FastAPI 路由和统一异常出口
│  ├─ models.py                      # 请求、状态、持久任务模型
│  ├─ business_registry.py           # 固化业务显式白名单
│  ├─ task_manager.py                # 队列、重试、批量、状态、审计与自愈重跑
│  └─ self_healer.py                 # Codex HTTP 自愈执行器适配器
├─ scripts/
│  └─ start_gateway.ps1              # Windows 后台常驻启动脚本
├─ tests/                             # 公共契约测试
├─ .env.example                      # 环境变量示例
├─ .gitignore
├─ pyproject.toml
└─ requirements.txt
```

运行后自动创建且不纳入 Git 的目录：

```text
browser_profiles/<profile>/          # 持久 Chrome 用户目录和登录态
logs/automation.log                  # 全局 JSON 日志
logs/screenshots/                    # 统一异常截图
logs/tasks/<task_id>.json            # 任务最新状态快照
logs/tasks/<task_id>.jsonl           # 任务全生命周期审计日志
runtime/snapshots/healing/            # 交给 Codex 的自愈证据快照
```

## 首次安装

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

默认通过 Playwright 的 `channel=chrome` 启动本机 Chrome，因此本机需安装 Chrome。若改用 Playwright 自带 Chromium，可安装浏览器并相应调整 `BrowserContextPool` 的 channel 配置：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

网关启动时会自动加载项目根目录的 `.env`；进程级环境变量优先级更高，适合生产守护工具注入。

## 启动网关

开发期前台启动（方便查看日志）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn gateway.main:app --host 127.0.0.1 --port 8000 --reload
```

后台常驻启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_gateway.ps1
```

脚本隐藏启动进程，将输出写入 `logs/gateway.stdout.log` 和 `logs/gateway.stderr.log`，并返回 PID。真正的开机自启建议再把该命令接入 Windows 任务计划程序或 NSSM。

## 网关接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 存活检查 |
| `GET` | `/api/v1/businesses` | 查看已注册固化业务 |
| `POST` | `/api/v1/tasks` | 提交网页或桌面任务 |
| `POST` | `/api/v1/tasks/batch` | 批量提交，最多 100 项 |
| `GET` | `/api/v1/tasks/{task_id}` | 查询状态、结果和失败证据 |
| `POST` | `/api/v1/tasks/{task_id}/retry` | 基于原请求创建重试任务 |

所有成功和失败响应均为：

```json
{"code": 0, "msg": "success", "data": {}, "screenshot": null}
```

网页任务调用见 `business/demo_search/readme.md`。桌面业务也必须先在 `BUSINESSES` 注册，API 不接受任意 EXE 路径。注册示例：

```python
"my_desktop_job": BusinessDefinition(
    kind="desktop",
    executable="business/my_desktop_job/task.exe",
    source="business/my_desktop_job/task.ahk",
    cwd="business/my_desktop_job",
),
```

注册后的桌面任务请求：

```json
{
  "kind": "desktop",
  "business": "my_desktop_job",
  "args": ["value1", "value2"],
  "timeout_seconds": 300,
  "max_retries": 1
}
```

## 自愈闭环

1. 网关执行任务并按 `max_retries` 重试。
2. 最终失败时统一收集业务源码绝对路径、完整堆栈、截图路径和原始参数。
3. 修复上下文始终写入 `runtime/snapshots/healing/`。
4. 默认通过官方非交互方式 `codex exec --sandbox workspace-write` 在隔离项目副本中调用本机已认证 Codex CLI；设置 `AUTOMATION_HEALING_BACKEND=http` 后则把内容 POST 给远端执行器。
5. 执行器修改对应网页 `task.py`，或修改 AHK 源码并重新编译已注册 EXE，验证后返回 `{"fixed": true}`。
6. 网关重新加载该业务模块并自动重跑一次；不会无限自愈循环。

本地 Codex 对隔离副本的其他修改会被丢弃，网关只会把已验证的目标业务源码与 AHK 编译产物成组回写。HTTP 执行器也只返回候选源码内容（AHK 另返回 Base64 编译产物），真实工作区写入仍由网关白名单控制。

本地模式要求 `AUTOMATION_CODEX_COMMAND` 指向可由后台账户执行且已认证的 Codex CLI。部分 Windows Store 桌面应用别名会拒绝后台进程启动，此时应安装独立 CLI，或切换 HTTP 后端。任何执行器不可用时仍完整留存修复证据，任务进入 `failed`，不会虚假宣称已自愈。

## 新增业务规范

1. 临时使用 MCP 摸索流程，只用于开发调研。
2. 在 `business/<业务名>/task.py` 实现 `async run(params, browser_pool, *, task_id)`。
3. 页面动作使用 `PlaywrightBase`；不得自行启动浏览器、重复截图或异常封装。
4. 编写同目录 `readme.md`，记录参数、返回数据和调用示例。
5. 在 `gateway/business_registry.py` 的 `BUSINESSES` 中显式注册。
6. 运行测试后，只通过网关投入生产调度。

## 浏览器元素失效诊断

正式业务使用固定 Playwright 选择器，并用 `fixed_operation()` 包装可能失效的元素操作：

```python
await automation.fixed_operation(
    submit_search,
    intent="在搜索框输入关键词并搜索",
    current_locator="get_by_role('searchbox')",
)
```

正常成功时只执行固定脚本，不扫描页面，也不调用模型。固定元素失败后，中台使用本地 DOM 分析收集候选元素，生成 `get_by_role/get_by_label/get_by_test_id` 等可固化定位建议，保存诊断快照并写入统一失败 JSON；普通重试不会调用模型。最终由 Codex结合候选、报错和截图完成一次永久修复，诊断过程不会自动点击或填写页面。

`observe/act/extract` 仍作为探索和故障验证工具保留，不应直接写入日常生产流程。Codex根据诊断候选永久修改业务源码，网关随后自动重跑固定流程。

## 验证

### CodeGraph 代码图谱

项目已启用 CodeGraph。首次克隆后在项目根目录构建本机索引：

```powershell
codegraph init .
codegraph status
```

`.codegraph/codegraph.db` 是本机生成数据，不提交到 Git。后续理解或定位代码时优先使用 Codex 的 `codegraph_explore` MCP 工具；MCP 不可用时使用：

```powershell
codegraph explore "要理解的符号或代码问题"
```

索引会随代码变化自动同步，也可以手动执行 `codegraph sync`。

## 工程验证

```powershell
.\.venv\Scripts\python.exe -m compileall core business gateway
.\.venv\Scripts\python.exe -m pytest
```
