const api = {
  async request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = { code: response.status, msg: response.statusText, data: null, screenshot: null };
    }
    if (!response.ok || payload.code) {
      const error = new Error(payload.msg || response.statusText);
      error.payload = payload;
      error.status = response.status;
      throw error;
    }
    return payload;
  },
  health: () => api.request("/health"),
  businesses: () => api.request("/api/v1/businesses"),
  submitTask: (body) => api.request("/api/v1/tasks", { method: "POST", body: JSON.stringify(body) }),
  submitBatch: (body) => api.request("/api/v1/tasks/batch", { method: "POST", body: JSON.stringify(body) }),
  getTask: (id) => api.request(`/api/v1/tasks/${encodeURIComponent(id)}`),
  retryTask: (id) => api.request(`/api/v1/tasks/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  startRecording: (body) => api.request("/api/v1/recordings/start", { method: "POST", body: JSON.stringify(body) }),
  currentRecording: () => api.request("/api/v1/recordings/current"),
  stopRecording: () => api.request("/api/v1/recordings/stop", { method: "POST" }),
};

const state = {
  route: "dashboard",
  health: null,
  businesses: [],
  sessionTasks: loadSessionTasks(),
  selectedBusiness: null,
  lastTask: null,
  queryTask: null,
  queryError: null,
  busy: false,
  recording: null,
};

const routes = [
  { id: "dashboard", label: "总览", icon: "dashboard" },
  { id: "business", label: "业务", icon: "business_center" },
  { id: "new-task", label: "新建任务", icon: "add_task" },
  { id: "recorder", label: "业务录制", icon: "fiber_manual_record" },
  { id: "batch-task", label: "批量提交", icon: "account_tree" },
  { id: "task-query", label: "任务查询", icon: "search_check" },
  { id: "runtime-env", label: "运行环境", icon: "terminal" },
  { id: "api-debug", label: "接口调试", icon: "api" },
];

const routeIds = new Set([...routes.map((route) => route.id), "task-detail-success", "task-detail-failure"]);

const mockTasks = [
  { task_id: "#TK-8912", business: "demo_search", kind: "web", status: "running", created_at: "12:45 PM", attempts: 1, healed: false },
  { task_id: "#TK-8911", business: "login_collect", kind: "desktop", status: "healing", created_at: "12:30 PM", attempts: 3, healed: true },
  { task_id: "#TK-8910", business: "data_sync_engine", kind: "web", status: "succeeded", created_at: "11:50 AM", attempts: 1, healed: false },
  { task_id: "#TK-8909", business: "legacy_payment", kind: "web", status: "failed", created_at: "10:15 AM", attempts: 2, healed: false },
];

const successTask = {
  task_id: "TXN-88219-X",
  status: "succeeded",
  request: { kind: "web", business: "demo_search", params: { query: "local automation console", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true },
  attempts: 1,
  healed: false,
  parent_task_id: null,
  created_at: "2026-07-11T04:50:27Z",
  started_at: "2026-07-11T04:50:30Z",
  finished_at: "2026-07-11T04:50:35Z",
  result: { code: 0, msg: "success", data: { query: "本地自动化控制台", count: 5, titles: ["本地自动化调度平台", "Playwright 任务网关", "自愈工作流审计", "浏览器 Profile Worker", "FastAPI 任务队列"], url: "https://www.bing.com/search?q=local+automation+console" }, screenshot: null },
  business_source: "D:\\JR_project\\auto_computer\\business\\demo_search\\task.py",
  screenshot: null,
};

const failureTask = {
  task_id: "TX-4091-B",
  status: "failed",
  request: { kind: "web", business: "demo_search", params: { query: "", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 2, enable_self_healing: true },
  attempts: 3,
  healed: true,
  parent_task_id: null,
  created_at: "2026-07-11T06:02:10Z",
  started_at: "2026-07-11T06:02:12Z",
  finished_at: "2026-07-11T06:06:24Z",
  result: { code: 500, msg: "重试 3 次后部署超时，已触发环境稳定性检查。", data: { traceback: "ERROR: 无法连接下游服务 'inventory-api'.\\nTraceback (most recent call last):\\n  File \"gateway/sync_service.py\", line 142, in process_batch\\nConnectTimeout: HTTPSConnectionPool(host='api.internal.svc', port=443): Max retries exceeded" }, screenshot: "logs/screenshots/TX-4091-B_exception_20260711.png" },
  error_traceback: "ERROR: 无法连接下游服务 'inventory-api'.\n[2026-07-11 06:06:24] Traceback (most recent call last): File \"gateway/sync_service.py\", line 142, in process_batch response = requests.post(TARGET_URL, json=data, timeout=5)\nConnectTimeout: HTTPSConnectionPool(host='api.internal.svc', port=443): Max retries exceeded\n// 自愈引擎提示：建议检查集群 DNS 健康状态。",
  business_source: "D:\\JR_project\\auto_computer\\business\\demo_search\\task.py",
  screenshot: "logs/screenshots/TX-4091-B_exception_20260711.png",
};

function loadSessionTasks() {
  try {
    return JSON.parse(localStorage.getItem("donezo.sessionTasks") || "[]");
  } catch {
    return [];
  }
}

function saveSessionTasks() {
  localStorage.setItem("donezo.sessionTasks", JSON.stringify(state.sessionTasks.slice(0, 20)));
}

function icon(name, fill = false) {
  return `<span class="material-symbols-outlined" style="font-variation-settings:'FILL' ${fill ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 24">${name}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function jsonBlock(value) {
  return escapeHtml(JSON.stringify(value ?? {}, null, 2));
}

function statusBadge(status) {
  const label = {
    queued: "排队中",
    running: "运行中",
    healing: "自愈中",
    succeeded: "成功",
    failed: "失败",
    active: "活跃",
    idle: "空闲",
    online: "在线",
    recording: "录制中",
    completed: "已保存",
  }[status] || status || "未知";
  const glyph = { queued: "pending", running: "autorenew", healing: "auto_fix_high", succeeded: "task_alt", failed: "warning", online: "hub", recording: "fiber_manual_record", completed: "save" }[status] || "circle";
  return `<span class="badge ${status}">${icon(glyph, status === "succeeded")} ${escapeHtml(label)}</span>`;
}

function kindBadge(kind) {
  const glyph = kind === "desktop" ? "desktop_windows" : "language";
  return `<span class="badge ${kind}">${icon(glyph)} ${escapeHtml(kind === "desktop" ? "桌面" : "网页")}</span>`;
}

function formatTime(value) {
  if (!value) return "-";
  if (String(value).includes(" ")) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function notify(message) {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove("show"), 2400);
}

async function copyText(text) {
  await navigator.clipboard.writeText(String(text ?? ""));
  notify("已复制到剪贴板");
}

function setRoute(route) {
  state.route = route;
  location.hash = route;
  render();
}

async function bootstrap() {
  const hash = location.hash.replace("#", "");
  state.route = routeIds.has(hash) ? hash : "dashboard";
  render();
  await Promise.allSettled([refreshHealth(), refreshBusinesses(), refreshRecording()]);
  render();
}

async function refreshHealth() {
  try {
    state.health = { online: true, ...(await api.health()).data, checkedAt: new Date().toISOString() };
  } catch (error) {
    state.health = { online: false, status: "offline", service: "automation-gateway", checkedAt: new Date().toISOString(), error: error.message };
  }
}

async function refreshBusinesses() {
  try {
    state.businesses = (await api.businesses()).data || [];
  } catch {
    state.businesses = [
      { name: "demo_search", kind: "web", module: "business.demo_search.task", executable: null, source: "business/demo_search/task.py" },
    ];
  }
}

async function refreshRecording() {
  try {
    state.recording = (await api.currentRecording()).data;
  } catch {
    state.recording = null;
  }
}

function shell(content) {
  const active = state.route;
  return `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">${icon("automation")}</div>
          <div>
            <h1 class="brand-title">Donezo</h1>
            <div class="brand-subtitle">本地自动化控制台</div>
          </div>
        </div>
        <div class="sidebar-label">菜单</div>
        <nav class="nav">
          ${routes.map((r) => `<a class="nav-item ${active === r.id ? "active" : ""}" href="#${r.id}" data-route="${r.id}">${icon(r.icon, active === r.id)}<span>${r.label}</span></a>`).join("")}
        </nav>
        <div class="promo-card">
          <strong>本地控制台</strong>
          <p>统一调度与自愈审计</p>
          <button>查看状态</button>
        </div>
        <div class="sidebar-footer">
          <a class="nav-item" href="#">${icon("settings")}<span>设置</span></a>
          <a class="nav-item" href="#">${icon("help")}<span>帮助</span></a>
          <a class="nav-item" style="color:var(--error)" href="#">${icon("logout")}<span>退出</span></a>
        </div>
      </aside>
      <main class="main-frame">
        <div class="content-wrap">
          ${topbar()}
          ${content}
        </div>
      </main>
    </div>
    <div class="toast"></div>
  `;
}

function topbar() {
  return `
    <header class="topbar">
      <div class="searchbox">${icon("search")}<input placeholder="搜索业务、任务或操作..." /><span class="kbd">⌘F</span></div>
      <div class="top-actions">
        ${state.health?.online ? `<span class="badge online">${icon("hub")} 网关：在线</span>` : `<span class="badge failed">${icon("warning")} 网关：离线</span>`}
        <button class="icon-btn">${icon("mail")}</button>
        <button class="icon-btn">${icon("notifications")}</button>
        <div class="user-block">
          <div>
            <div class="user-name">Totok Michael</div>
            <div class="user-email">管理控制台</div>
          </div>
          <div class="avatar">TM</div>
        </div>
      </div>
    </header>
  `;
}

function pageHead(title, subtitle, actions = "") {
  return `
    <section class="page-head">
      <div>
        <h2 class="page-title">${title}</h2>
        <p class="page-subtitle">${subtitle}</p>
      </div>
      <div class="actions">${actions}</div>
    </section>
  `;
}

function render() {
  const page = {
    dashboard: dashboardPage,
    business: businessPage,
    "new-task": newTaskPage,
    recorder: recorderPage,
    "batch-task": batchTaskPage,
    "task-query": taskQueryPage,
    "runtime-env": runtimePage,
    "api-debug": apiDebugPage,
    "task-detail-success": () => taskDetailPage(successTask, "任务详情", "自动化任务 TXN-88219-X 的实时状态、执行结果与请求明细。"),
    "task-detail-failure": () => taskDetailPage(failureTask, "任务详情", "任务 TX-4091-B 的失败证据、自愈上下文与重跑控制。"),
  }[state.route] || dashboardPage;
  document.getElementById("app").innerHTML = shell(page());
  bindGlobalEvents();
  bindPageEvents();
}

function bindGlobalEvents() {
  document.querySelectorAll("[data-route]").forEach((node) => {
    node.addEventListener("click", (event) => {
      event.preventDefault();
      setRoute(node.dataset.route);
    });
  });
}

function bindPageEvents() {
  document.querySelectorAll("[data-copy]").forEach((node) => {
    node.addEventListener("click", () => copyText(node.dataset.copy));
  });
  document.querySelectorAll("[data-route-button]").forEach((node) => {
    node.addEventListener("click", () => setRoute(node.dataset.routeButton));
  });
  if (state.route === "new-task") bindNewTask();
  if (state.route === "recorder") bindRecorder();
  if (state.route === "batch-task") bindBatchTask();
  if (state.route === "task-query") bindTaskQuery();
  if (state.route === "api-debug") bindApiDebug();
  if (state.route === "business") bindBusiness();
}

function dashboardPage() {
  const tasks = [...state.sessionTasks, ...mockTasks].slice(0, 5);
  return `
    ${pageHead("总览", "查看本地自动化网关状态、任务概览与快捷操作。", `<button class="btn primary" data-route-button="new-task">${icon("add")} 新建任务</button><button class="btn">${icon("cloud_upload")} 导入数据</button>`)}
    <section class="card pad" style="margin-bottom:20px">
      <div class="card-head">
        <div>
          <h3 class="card-title">${icon("hub")} automation-gateway</h3>
          <p class="card-subtitle">状态：${state.health?.online ? "连接稳定" : "离线"} • 节点：本地</p>
        </div>
        ${statusBadge(state.health?.online ? "online" : "failed")}
      </div>
      <div class="muted tiny">最近检查：${state.health?.checkedAt ? formatTime(state.health.checkedAt) : "检查中..."}</div>
    </section>
    <section class="grid cols-12">
      ${metricCard("排队任务", "24", "north_east", "较上月 +5", "queued")}
      ${metricCard("运行中", "12", "autorenew", "正在执行", "running")}
      ${metricCard("自愈中", "3", "auto_fix_high", "自动修复状态", "healing")}
      ${metricCard("已成功", "1,024", "task_alt", "近 24 小时", "succeeded")}
      <div class="span-8 card pad">
        <div class="card-head">
          <h3 class="card-title">最近自动化任务</h3>
          <div class="actions"><button class="icon-btn">${icon("filter_list")}</button><button class="icon-btn">${icon("download")}</button></div>
        </div>
        ${taskTable(tasks)}
        <button class="btn ghost" data-route-button="task-query" style="margin-top:16px">查看全部任务 ${icon("arrow_forward")}</button>
      </div>
      <div class="span-4 grid">
        <div class="card pad">
          <h3 class="card-title">快捷操作</h3>
          <div class="divider"></div>
          <button class="btn" data-route-button="new-task" style="width:100%;justify-content:space-between">${icon("language")} 新建网页任务 ${icon("add_circle")}</button>
          <div style="height:12px"></div>
          <button class="btn" data-route-button="new-task" style="width:100%;justify-content:space-between">${icon("desktop_windows")} 新建桌面任务 ${icon("add_circle")}</button>
        </div>
        <div class="card pad">
          <h3 class="card-title">月度目标</h3>
          <div class="metric-value">70%</div>
          <div class="progress"><span style="width:70%"></span></div>
          <p class="muted tiny">已完成 21,450 / 30,000 个任务</p>
        </div>
      </div>
    </section>
  `;
}

function metricCard(label, value, glyph, note, status) {
  return `
    <div class="span-3 card metric">
      <div class="metric-icon ${status === "healing" ? "pulse" : ""}">${icon(glyph)}</div>
      <div class="metric-value">${value}</div>
      <div class="metric-label">${label}</div>
      <div class="metric-note">${note}</div>
    </div>
  `;
}

function taskTable(tasks) {
  if (!tasks.length) return `<div class="empty">暂无任务记录，可以从新建任务开始。</div>`;
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>任务 ID</th><th>业务名称</th><th>类型</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>
          ${tasks.map((task) => `
            <tr>
              <td class="mono">${escapeHtml(task.task_id)}</td>
              <td>${escapeHtml(task.business || task.request?.business)}</td>
              <td>${kindBadge(task.kind || task.request?.kind)}</td>
              <td>${statusBadge(task.status)}</td>
              <td>${escapeHtml(formatTime(task.created_at))}</td>
              <td><button class="btn ghost" data-route-button="${task.status === "failed" ? "task-detail-failure" : "task-detail-success"}">查看</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function businessPage() {
  const businesses = state.businesses.length ? state.businesses : [{ name: "demo_search", kind: "web", module: "business.demo_search.task", source: "business/demo_search/task.py" }];
  const selected = state.selectedBusiness || businesses[0];
  return `
    ${pageHead("业务列表", "管理和查看当前已注册、可调度的业务白名单。", `<button class="btn">${icon("file_download")} 导入 Schema</button><button class="btn primary">${icon("add")} 注册业务</button>`)}
    <div class="drawer-layout">
      <section class="card pad">
        <div class="card-head">
          <div class="tabs"><button class="active">全部</button><button>网页</button><button>桌面</button></div>
          <div class="searchbox" style="width:300px;box-shadow:none;border:1px solid var(--outline-variant)">${icon("search")}<input placeholder="搜索任务或业务..." /></div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>名称</th><th>类型</th><th>模块 / 路径</th><th>源码路径</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              ${businesses.map((biz) => `
                <tr>
                  <td><strong>${escapeHtml(biz.name)}</strong></td>
                  <td>${kindBadge(biz.kind)}</td>
                  <td class="mono tiny">${escapeHtml(biz.module || biz.executable || "-")}</td>
                  <td class="mono tiny">${escapeHtml(biz.source || "-")}</td>
                  <td>${statusBadge("active")}</td>
                  <td><button class="btn ghost" data-business="${escapeHtml(biz.name)}">查看详情</button></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </section>
      ${businessDrawer(selected)}
    </div>
  `;
}

function businessDrawer(biz) {
  if (!biz) return `<aside class="card pad drawer empty">暂无已注册业务。</aside>`;
  const sample = { query: "关键词", limit: 5, profile: "demo" };
  return `
    <aside class="card pad drawer">
      <div class="card-head">
        <div>
          <h3 class="card-title">${icon("close")} ${escapeHtml(biz.name)}</h3>
          <p class="card-subtitle">业务详细规格</p>
        </div>
        <button class="icon-btn">${icon("share")}</button>
      </div>
      <div class="grid cols-12">
        <div class="span-6 card pad" style="background:var(--surface-low)"><div class="label-caps">执行次数</div><h2>12.4k</h2><span class="badge success">${icon("trending_up")} 12%</span></div>
        <div class="span-6 card pad" style="background:var(--surface-low)"><div class="label-caps">错误率</div><h2>0.02%</h2><span class="badge success">${icon("trending_down")} 4%</span></div>
      </div>
      <div class="divider"></div>
      <h3 class="card-title">参数 Schema <span class="badge desktop">JSON 严格模式</span></h3>
      <table style="margin-top:12px">
        <thead><tr><th>字段</th><th>类型</th><th>说明</th></tr></thead>
        <tbody>
          <tr><td>query</td><td>String</td><td>搜索关键词，示例业务必填。</td></tr>
          <tr><td>limit</td><td>Integer</td><td>结果数量，建议 1 到 20。</td></tr>
          <tr><td>profile</td><td>String</td><td>浏览器持久上下文名称。</td></tr>
        </tbody>
      </table>
      <div class="divider"></div>
      <h3 class="card-title">实现逻辑 <button class="btn ghost" data-copy="${escapeHtml(JSON.stringify(sample))}">${icon("content_copy")} 复制示例</button></h3>
      <pre class="code-panel">async function run(params, browser_pool, { task_id }) {
  const query = params.query;
  await automation.goto("https://www.bing.com/");
  return { query, titles, url };
}</pre>
      <div class="divider"></div>
      <button class="btn primary" data-route-button="new-task" style="width:100%">${icon("rocket_launch")} 提交任务</button>
    </aside>
  `;
}

function newTaskPage() {
  const businesses = state.businesses.length ? state.businesses : [{ name: "demo_search", kind: "web" }];
  const first = businesses[0]?.name || "demo_search";
  const request = { kind: "web", business: first, params: { query: "local automation console", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true };
  return `
    ${pageHead("新建任务配置", "配置自动化任务的执行参数、业务和运行策略。", `<button class="btn">${icon("save")} 草稿</button><button class="btn primary" id="submit-task">${icon("rocket_launch")} 提交任务</button>`)}
    <section class="grid cols-12">
      <div class="span-8 grid">
        <section class="card pad">
          <div class="card-head"><h3 class="card-title"><span class="badge desktop">01</span> 业务选择</h3><span class="badge desktop">第 1 / 3 步</span></div>
          <div class="choice-row" id="kind-choices">
            <button class="choice-card active" data-kind="web">${icon("language")}<h4>网页自动化</h4><p>使用 Playwright profile 执行浏览器流程。</p></button>
            <button class="choice-card" data-kind="desktop">${icon("desktop_windows")}<h4>桌面客户端</h4><p>执行已注册的 AHK EXE 自动化。</p></button>
            <button class="choice-card" data-kind="web">${icon("auto_fix_high")}<h4>自愈任务</h4><p>收集失败证据，修复后重跑。</p></button>
          </div>
          <div class="split" style="margin-top:18px">
            <div class="field"><label>业务</label><select class="select" id="task-business">${businesses.map((b) => `<option value="${escapeHtml(b.name)}" data-kind="${escapeHtml(b.kind)}">${escapeHtml(b.name)} (${b.kind === "desktop" ? "桌面" : "网页"})</option>`).join("")}</select></div>
            <div class="field"><label>超时时间（秒）</label><input class="input" id="task-timeout" type="number" value="300" min="1" max="3600" /></div>
          </div>
        </section>
        <section class="card pad">
          <div class="card-head"><h3 class="card-title"><span class="badge desktop">02</span> 运行参数</h3><button class="btn" id="format-json">${icon("format_align_left")} 格式化</button></div>
          <textarea id="task-params" class="code-input">${jsonBlock(request.params)}</textarea>
        </section>
      </div>
      <aside class="span-4 grid">
        <section class="card pad">
          <h3 class="card-title"><span class="badge desktop">03</span> 执行控制</h3>
          <div class="divider"></div>
          <div class="field"><label>最大重试次数</label><input class="input" id="task-retries" type="number" value="1" min="0" max="5" /></div>
          <div class="divider"></div>
          <label class="field"><span class="label-caps">自愈</span><select class="select" id="task-healing"><option value="true">开启</option><option value="false">关闭</option></select></label>
        </section>
        <section class="card pad">
          <div class="card-head"><h3 class="card-title">请求预览</h3><button class="icon-btn" id="copy-preview">${icon("content_copy")}</button></div>
          <pre class="code-panel" id="request-preview">${jsonBlock(request)}</pre>
        </section>
        <section class="card pad" id="submit-result"><p class="muted">提交成功后会在这里显示任务 ID。</p></section>
      </aside>
    </section>
  `;
}

function bindNewTask() {
  const preview = document.getElementById("request-preview");
  const params = document.getElementById("task-params");
  const business = document.getElementById("task-business");
  const timeout = document.getElementById("task-timeout");
  const retries = document.getElementById("task-retries");
  const healing = document.getElementById("task-healing");
  let kind = "web";

  function build() {
    let parsed = {};
    try { parsed = JSON.parse(params.value || "{}"); } catch { parsed = { invalid_json: true }; }
    return { kind, business: business.value, params: kind === "web" ? parsed : {}, args: kind === "desktop" ? Object.values(parsed).map(String) : [], timeout_seconds: Number(timeout.value), max_retries: Number(retries.value), enable_self_healing: healing.value === "true" };
  }
  function update() { preview.textContent = JSON.stringify(build(), null, 2); }
  document.querySelectorAll("[data-kind]").forEach((node) => node.addEventListener("click", () => {
    document.querySelectorAll("[data-kind]").forEach((item) => item.classList.remove("active"));
    node.classList.add("active");
    kind = node.dataset.kind;
    update();
  }));
  [params, business, timeout, retries, healing].forEach((node) => node.addEventListener("input", update));
  document.getElementById("format-json").addEventListener("click", () => {
    try { params.value = JSON.stringify(JSON.parse(params.value), null, 2); update(); } catch { notify("JSON 格式错误，无法格式化"); }
  });
  document.getElementById("copy-preview").addEventListener("click", () => copyText(preview.textContent));
  document.getElementById("submit-task").addEventListener("click", async () => {
    try {
      const body = build();
      if (body.params.invalid_json) throw new Error("JSON 参数格式错误");
      const payload = await api.submitTask(body);
      state.lastTask = payload.data;
      state.sessionTasks.unshift({ ...payload.data, business: payload.data.request.business, kind: payload.data.request.kind });
      saveSessionTasks();
      document.getElementById("submit-result").innerHTML = `<h3 class="card-title">${icon("check_circle", true)} 任务已创建</h3><p class="mono">${payload.data.task_id}</p><button class="btn primary" data-route-button="task-query">${icon("search_check")} 查看详情</button>`;
      bindGlobalEvents();
      notify("任务已进入队列");
    } catch (error) {
      document.getElementById("submit-result").innerHTML = `<h3 class="card-title" style="color:var(--error)">${icon("warning")} 提交失败</h3><pre class="code-panel">${escapeHtml(JSON.stringify(error.payload || { msg: error.message }, null, 2))}</pre>`;
    }
  });
}

function batchTaskPage() {
  const sample = { tasks: [{ kind: "web", business: "demo_search", params: { query: "关键词", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true }, { kind: "web", business: "demo_search", params: { query: "自愈控制台", limit: 3, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true }] };
  return `
    ${pageHead("批量提交", "一次创建、校验并提交多个自动化任务。", `<button class="btn">${icon("add")} 添加任务</button><button class="btn primary" id="submit-batch">${icon("send")} 提交批量任务</button>`)}
    <section class="grid cols-12">
      <div class="span-7 card pad">
        <div class="card-head"><h3 class="card-title">${icon("account_tree")} 批量请求体</h3><button class="btn" id="beautify-batch">格式化</button></div>
        <textarea id="batch-json" class="code-input" style="min-height:520px">${jsonBlock(sample)}</textarea>
      </div>
      <aside class="span-5 grid">
        <div class="card pad">
          <h3 class="card-title">校验摘要</h3>
          <div class="grid cols-12" style="margin-top:16px">${metricSmall("总数", "2")}${metricSmall("有效", "2")}${metricSmall("错误", "0")}</div>
        </div>
        <div class="card pad" id="batch-result"><div class="empty">批量提交后逐条显示成功或失败结果。</div></div>
      </aside>
    </section>
  `;
}

function metricSmall(label, value) {
  return `<div class="span-4 card pad" style="background:var(--surface-low)"><div class="label-caps">${label}</div><h2>${value}</h2></div>`;
}

function bindBatchTask() {
  const editor = document.getElementById("batch-json");
  document.getElementById("beautify-batch").addEventListener("click", () => {
    try { editor.value = JSON.stringify(JSON.parse(editor.value), null, 2); } catch { notify("JSON 格式错误"); }
  });
  document.getElementById("submit-batch").addEventListener("click", async () => {
    const box = document.getElementById("batch-result");
    try {
      const body = JSON.parse(editor.value);
      const payload = await api.submitBatch(body);
      box.innerHTML = `<h3 class="card-title">${icon("check_circle", true)} 提交结果</h3>${(payload.data || []).map((item, index) => `<p>${index + 1}. ${item.code === 0 ? statusBadge("succeeded") : statusBadge("failed")} <span class="mono">${escapeHtml(item.data?.task_id || item.msg)}</span></p>`).join("")}`;
    } catch (error) {
      box.innerHTML = `<h3 class="card-title" style="color:var(--error)">${icon("warning")} 批量提交失败</h3><pre class="code-panel">${escapeHtml(JSON.stringify(error.payload || { msg: error.message }, null, 2))}</pre>`;
    }
  });
}

function taskQueryPage() {
  const task = state.queryTask || state.lastTask || successTask;
  return `
    ${pageHead("任务查询", "通过任务 ID 精确追踪和查看自动化任务状态。", "")}
    <section class="grid cols-12">
      <aside class="span-4 grid">
        <div class="card pad">
          <div class="label-caps">任务标识查询</div>
          <div class="searchbox" style="width:100%;margin-top:14px;box-shadow:none;border:1px solid var(--outline-variant)">${icon("fingerprint")}<input id="task-id-input" placeholder="输入 task_id" value="${escapeHtml(state.lastTask?.task_id || "")}" /></div>
          <button class="btn primary" id="query-task" style="width:100%;margin-top:14px">${icon("search_check")} 查询</button>
          <p class="muted tiny">${icon("info")} 支持完整任务 ID、局部 ID 和最近提交记录。</p>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("history")} 最近查询</h3>
          ${[...(state.sessionTasks || []), successTask, failureTask].slice(0, 4).map((t) => `<p><button class="btn ghost" data-query-id="${escapeHtml(t.task_id)}">${icon("receipt_long")} ${escapeHtml(t.task_id)}</button></p>`).join("")}
        </div>
      </aside>
      <section class="span-8">
        ${state.queryError ? `<div class="card pad"><h3 class="card-title" style="color:var(--error)">${icon("warning")} 未找到任务</h3><p>${escapeHtml(state.queryError)}</p></div>` : taskPreview(task)}
      </section>
    </section>
  `;
}

function bindTaskQuery() {
  document.querySelectorAll("[data-query-id]").forEach((node) => node.addEventListener("click", () => {
    document.getElementById("task-id-input").value = node.dataset.queryId;
  }));
  document.getElementById("query-task").addEventListener("click", async () => {
    const id = document.getElementById("task-id-input").value.trim();
    if (!id) return notify("请输入任务 ID");
    if (id === successTask.task_id) { state.queryTask = successTask; state.queryError = null; render(); return; }
    if (id === failureTask.task_id) { state.queryTask = failureTask; state.queryError = null; render(); return; }
    try {
      const payload = await api.getTask(id);
      state.queryTask = payload.data;
      state.queryError = null;
    } catch (error) {
      state.queryTask = null;
      state.queryError = error.message;
    }
    render();
  });
}

function taskPreview(task) {
  return `
    <div class="card pad">
      <div class="card-head">
        <div><h3 class="card-title">任务预览 ${statusBadge(task.status)}</h3><p class="card-subtitle">监控 ID：<span class="mono">${escapeHtml(task.task_id)}</span></p></div>
        <div class="actions"><button class="btn">${icon("file_download")} 导出日志</button><button class="btn primary" data-route-button="${task.status === "failed" ? "task-detail-failure" : "task-detail-success"}">${icon("open_in_new")} 打开详情</button></div>
      </div>
      <div class="grid cols-12">${metricSmall("尝试次数", task.attempts || 0)}${metricSmall("状态", statusBadge(task.status))}${metricSmall("已自愈", task.healed ? "是" : "否")}</div>
      <div class="divider"></div>
      <pre class="code-panel">${jsonBlock(task)}</pre>
    </div>
  `;
}

function taskDetailPage(task, title, subtitle) {
  return `
    ${pageHead(title, subtitle, `<button class="btn" data-copy="${escapeHtml(task.task_id)}">${icon("content_copy")} 复制 ID</button><button class="btn primary" id="retry-current">${icon("replay")} 重跑任务</button>`)}
    <section class="grid cols-12">
      <div class="span-12 card pad">
        <div class="grid cols-12">
          <div class="span-3"><div class="label-caps">当前状态</div><h2>${statusBadge(task.status)}</h2></div>
          <div class="span-3"><div class="label-caps">任务 ID</div><h2 class="mono">${escapeHtml(task.task_id)}</h2></div>
          <div class="span-3"><div class="label-caps">任务类型</div><h2>${kindBadge(task.request.kind)}</h2></div>
          <div class="span-3"><div class="label-caps">尝试次数</div><h2>${task.attempts}</h2><p class="muted tiny">${task.healed ? "已执行自愈后重跑" : "首次执行成功"}</p></div>
        </div>
      </div>
      <div class="span-7 grid">
        <div class="card pad">
          <div class="card-head"><h3 class="card-title">${icon("database")} 执行结果</h3><button class="icon-btn" data-copy="${escapeHtml(JSON.stringify(task.result || {}, null, 2))}">${icon("content_copy")}</button></div>
          <pre class="code-panel">${jsonBlock(task.result)}</pre>
        </div>
        <div class="card pad">
          <div class="card-head"><h3 class="card-title">${icon("outbound")} 原始请求</h3><button class="icon-btn" data-copy="${escapeHtml(JSON.stringify(task.request || {}, null, 2))}">${icon("content_copy")}</button></div>
          <pre class="code-panel">${jsonBlock(task.request)}</pre>
        </div>
        ${task.error_traceback ? `<div class="card pad"><div class="card-head"><h3 class="card-title" style="color:var(--error)">${icon("warning")} error_traceback.log</h3><button class="icon-btn" data-copy="${escapeHtml(task.error_traceback)}">${icon("content_copy")}</button></div><pre class="code-panel">${escapeHtml(task.error_traceback)}</pre></div>` : ""}
      </div>
      <aside class="span-5 grid">
        <div class="card pad">
          <h3 class="card-title">${icon("schedule")} 执行时间线</h3>
          <div class="divider"></div>
          <div class="timeline">
            ${timelineItem("已创建", formatTime(task.created_at), "add_circle")}
            ${timelineItem("已开始", formatTime(task.started_at), "play_circle")}
            ${timelineItem(task.status === "failed" ? "已结束（失败）" : "已结束", formatTime(task.finished_at), task.status === "failed" ? "error" : "check_circle")}
          </div>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("auto_fix_high")} 自愈引擎</h3>
          <div class="divider"></div>
          <p>${statusBadge(task.healed ? "healing" : "queued")} 自愈状态：<strong>${task.healed ? "已触发（活跃）" : "未触发"}</strong></p>
          <p class="muted">系统已收集堆栈、截图路径、源码路径和请求体，作为修复上下文。</p>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("photo_camera")} 失败快照</h3>
          <p class="mono tiny">${escapeHtml(task.screenshot || "未生成截图")}</p>
          <div class="divider"></div>
          <h3 class="card-title">${icon("hub")} 业务源码</h3>
          <p class="mono tiny">${escapeHtml(task.business_source || "-")}</p>
        </div>
      </aside>
    </section>
  `;
}

function timelineItem(title, time, glyph) {
  return `<div class="timeline-item"><div class="timeline-dot">${icon(glyph)}</div><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(time)}</span></div></div>`;
}

function recorderPage() {
  const session = state.recording;
  const active = session?.status === "recording";
  return `
    ${pageHead("业务录制", "人工操作一次，Playwright Codegen 自动生成第一版流程素材。", `<button class="btn" id="refresh-recorder">${icon("refresh")} 刷新状态</button>`)}
    <section class="grid cols-12">
      <div class="span-7 card pad">
        <div class="card-head">
          <div><h3 class="card-title">${icon("fiber_manual_record")} 启动新录制</h3><p class="card-subtitle">录制窗口使用独立持久 Profile，不影响生产浏览器池。</p></div>
          ${active ? statusBadge("recording") : statusBadge(session?.status || "idle")}
        </div>
        <form id="recorder-form" class="grid">
          <div class="field"><label for="record-business">业务名称</label><input class="input" id="record-business" value="new_business" pattern="[a-z][a-z0-9_]+" ${active ? "disabled" : ""} /></div>
          <div class="field"><label for="record-url">起始网址</label><input class="input" id="record-url" type="url" placeholder="https://example.com" ${active ? "disabled" : ""} /></div>
          <div class="field"><label for="record-profile">录制 Profile</label><input class="input" id="record-profile" value="default" pattern="[A-Za-z0-9_-]+" ${active ? "disabled" : ""} /></div>
          <div class="actions">
            <button class="btn primary" type="submit" ${active ? "disabled" : ""}>${icon("play_arrow")} 开始录制</button>
            <button class="btn danger" type="button" id="stop-recorder" ${active ? "" : "disabled"}>${icon("stop")} 停止并保存</button>
          </div>
        </form>
      </div>
      <aside class="span-5 grid">
        <div class="card pad">
          <h3 class="card-title">${icon("format_list_numbered")} 使用步骤</h3>
          <div class="divider"></div>
          <div class="timeline">
            ${timelineItem("填写信息并开始录制", "弹出浏览器和 Inspector", "looks_one")}
            ${timelineItem("人工完成完整业务流程", "点击、输入和断言会自动记录", "looks_two")}
            ${timelineItem("停止并保存原始素材", "再由 Codex优化并固化", "looks_3")}
          </div>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("folder_open")} 当前录制</h3>
          <div class="divider"></div>
          ${session ? `
            <p>状态：${statusBadge(session.status)}</p>
            <p class="tiny muted">业务：<span class="mono">${escapeHtml(session.business_name)}</span></p>
            <p class="tiny muted">开始：${escapeHtml(formatTime(session.started_at))}</p>
            <p class="tiny muted">原始脚本：</p>
            <pre class="code-panel">${escapeHtml(session.raw_script)}</pre>
            ${session.error ? `<p class="tiny" style="color:var(--error)">${escapeHtml(session.error)}</p>` : ""}
          ` : `<div class="empty">尚未启动录制</div>`}
        </div>
      </aside>
    </section>
  `;
}

function bindRecorder() {
  document.getElementById("refresh-recorder").addEventListener("click", async () => {
    await refreshRecording();
    render();
  });
  document.getElementById("recorder-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    state.busy = true;
    try {
      const payload = await api.startRecording({
        business_name: document.getElementById("record-business").value.trim(),
        start_url: document.getElementById("record-url").value.trim(),
        profile: document.getElementById("record-profile").value.trim(),
      });
      state.recording = payload.data;
      notify("录制窗口已启动，请在浏览器中完成业务流程");
    } catch (error) {
      notify(error.message);
    } finally {
      state.busy = false;
      render();
    }
  });
  document.getElementById("stop-recorder")?.addEventListener("click", async () => {
    if (state.busy) return;
    state.busy = true;
    try {
      const payload = await api.stopRecording();
      state.recording = payload.data;
      notify(payload.data.output_ready ? "录制已保存，可以交给 Codex固化" : "未生成有效脚本，请查看错误日志后重录");
    } catch (error) {
      notify(error.message);
    } finally {
      state.busy = false;
      render();
    }
  });
}

function runtimePage() {
  return `
    ${pageHead("运行环境", "查看本地自愈运行环境、目录和自动化资源状态。", `<button class="btn dark">${icon("terminal")} 打开控制台</button><button class="btn primary" id="refresh-health">${icon("refresh")} 刷新状态</button>`)}
    <section class="grid cols-12">
      <div class="span-8 grid">
        <div class="card pad">
          <div class="card-head"><h3 class="card-title">实时节点：本地节点</h3>${statusBadge(state.health?.online ? "online" : "failed")}</div>
          <div class="grid cols-12">${metricSmall("CPU 负载", "24%")}${metricSmall("内存", "4.2 GB")}${metricSmall("运行时长", "12天 04时")}</div>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("folder")} 运行目录</h3>
          <table style="margin-top:16px">
            <thead><tr><th>目录名称</th><th>用途</th><th>大小</th><th>操作</th></tr></thead>
            <tbody>
              <tr><td class="mono">logs/tasks</td><td>任务快照</td><td>142.5 MB</td><td>${icon("download")}</td></tr>
              <tr><td class="mono">logs/screenshots</td><td>失败证据</td><td>2.1 GB</td><td>${icon("visibility")}</td></tr>
              <tr><td class="mono">browser_profiles</td><td>持久浏览器 Profile</td><td>450 MB</td><td>${icon("security")}</td></tr>
              <tr><td class="mono">runtime/snapshots/healing</td><td>自愈上下文</td><td>36 MB</td><td>${icon("settings_backup_restore")}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <aside class="span-4 grid">
        <div class="card pad">
          <h3 class="card-title">${icon("auto_fix_high")} 自愈逻辑</h3>
          <p class="muted">Donezo 实时监控每个自动化流程。当任务因网络抖动或页面变化失败时，自愈引擎会识别根因并尝试恢复流程。</p>
          <button class="btn ghost">了解自愈机制 ${icon("arrow_forward")}</button>
        </div>
        <div class="card pad">
          <h3 class="card-title">数据吞吐 <span class="badge success">实时</span></h3>
          <div class="mini-chart">${[50, 72, 48, 83, 62, 96, 70, 88].map((h) => `<span style="height:${h}%"></span>`).join("")}</div>
        </div>
      </aside>
    </section>
  `;
}

function apiDebugPage() {
  const request = { kind: "web", business: "demo_search", params: { query: "每周基础设施同步", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true };
  return `
    ${pageHead("接口调试", "实时测试、检查和观察自动化接口响应。", `<button class="btn">${icon("history")} 历史记录</button><button class="btn primary" id="send-debug">${icon("send")} 发送</button>`)}
    <section class="grid cols-12">
      <div class="span-6 card pad">
        <div class="card-head"><h3 class="card-title">${icon("send")} 请求配置</h3><div class="segmented"><button class="active">POST</button><button>GET</button><button>PUT</button><button>DELETE</button></div></div>
        <div class="field"><label>接口地址</label><select class="select" id="debug-endpoint"><option value="/api/v1/tasks">POST /api/v1/tasks</option><option value="/health">GET /health</option><option value="/api/v1/businesses">GET /api/v1/businesses</option></select></div>
        <div class="divider"></div>
        <div class="card-head"><h3 class="card-title">JSON 请求体</h3><button class="btn" id="beautify-debug">格式化</button></div>
        <textarea class="code-input" id="debug-body">${jsonBlock(request)}</textarea>
      </div>
      <div class="span-6 card pad">
        <div class="card-head"><h3 class="card-title">${icon("output")} 响应结果</h3><button class="icon-btn" id="copy-debug-response">${icon("content_copy")}</button></div>
        <div class="grid cols-12" style="margin-bottom:18px">${metricSmall("状态", "就绪")}${metricSmall("延迟", "0 ms")}${metricSmall("大小", "0 KB")}</div>
        <pre class="code-panel" id="debug-response">${jsonBlock({ status: "ready", message: "等待发送请求", timestamp: new Date().toISOString() })}</pre>
      </div>
    </section>
  `;
}

function bindApiDebug() {
  const body = document.getElementById("debug-body");
  const response = document.getElementById("debug-response");
  document.getElementById("beautify-debug").addEventListener("click", () => {
    try { body.value = JSON.stringify(JSON.parse(body.value), null, 2); } catch { notify("JSON 格式错误"); }
  });
  document.getElementById("copy-debug-response").addEventListener("click", () => copyText(response.textContent));
  document.getElementById("send-debug").addEventListener("click", async () => {
    const endpoint = document.getElementById("debug-endpoint").value;
    const started = performance.now();
    try {
      let payload;
      if (endpoint === "/health") payload = await api.health();
      else if (endpoint === "/api/v1/businesses") payload = await api.businesses();
      else payload = await api.submitTask(JSON.parse(body.value));
      response.textContent = JSON.stringify({ latency_ms: Math.round(performance.now() - started), ...payload }, null, 2);
    } catch (error) {
      response.textContent = JSON.stringify({ error: error.message, payload: error.payload || null }, null, 2);
    }
  });
}

function bindBusiness() {
  document.querySelectorAll("[data-business]").forEach((node) => node.addEventListener("click", () => {
    state.selectedBusiness = state.businesses.find((biz) => biz.name === node.dataset.business) || null;
    render();
  }));
}

window.addEventListener("hashchange", () => {
  const hash = location.hash.replace("#", "");
  if (routeIds.has(hash)) {
    state.route = hash;
    render();
  }
});

bootstrap();
