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
};

const routes = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard" },
  { id: "business", label: "Business", icon: "business_center" },
  { id: "new-task", label: "New Task", icon: "add_task" },
  { id: "batch-task", label: "Batch Task", icon: "account_tree" },
  { id: "task-query", label: "Task Query", icon: "search_check" },
  { id: "runtime-env", label: "Runtime Env", icon: "terminal" },
  { id: "api-debug", label: "API Debug", icon: "api" },
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
  result: { code: 0, msg: "success", data: { query: "local automation console", count: 5, titles: ["Local Automation Orchestration Platform", "Playwright Task Gateway", "Self-healing workflow audit", "Browser profile worker", "FastAPI task queue"], url: "https://www.bing.com/search?q=local+automation+console" }, screenshot: null },
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
  result: { code: 500, msg: "Deployment timeout after 3 retries. Environment stability check triggered.", data: { traceback: "ERROR: Failed to connect to downstream service 'inventory-api'.\\nTraceback (most recent call last):\\n  File \"gateway/sync_service.py\", line 142, in process_batch\\nConnectTimeout: HTTPSConnectionPool(host='api.internal.svc', port=443): Max retries exceeded" }, screenshot: "logs/screenshots/TX-4091-B_exception_20260711.png" },
  error_traceback: "ERROR: Failed to connect to downstream service 'inventory-api'.\n[2026-07-11 06:06:24] Traceback (most recent call last): File \"gateway/sync_service.py\", line 142, in process_batch response = requests.post(TARGET_URL, json=data, timeout=5)\nConnectTimeout: HTTPSConnectionPool(host='api.internal.svc', port=443): Max retries exceeded\n// HEALING ENGINE NOTE: Auto-diagnostics suggests checking cluster DNS health.",
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
    queued: "Queued",
    running: "Running",
    healing: "Healing",
    succeeded: "Succeeded",
    failed: "Failed",
    active: "Active",
    idle: "Idle",
    online: "Online",
  }[status] || status || "Unknown";
  const glyph = { queued: "pending", running: "autorenew", healing: "auto_fix_high", succeeded: "task_alt", failed: "warning", online: "hub" }[status] || "circle";
  return `<span class="badge ${status}">${icon(glyph, status === "succeeded")} ${escapeHtml(label)}</span>`;
}

function kindBadge(kind) {
  const glyph = kind === "desktop" ? "desktop_windows" : "language";
  return `<span class="badge ${kind}">${icon(glyph)} ${escapeHtml((kind || "web").toUpperCase())}</span>`;
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
  await Promise.allSettled([refreshHealth(), refreshBusinesses()]);
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

function shell(content) {
  const active = state.route;
  return `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">${icon("automation")}</div>
          <div>
            <h1 class="brand-title">Donezo</h1>
            <div class="brand-subtitle">Automation Console</div>
          </div>
        </div>
        <div class="sidebar-label">Menu</div>
        <nav class="nav">
          ${routes.map((r) => `<a class="nav-item ${active === r.id ? "active" : ""}" href="#${r.id}" data-route="${r.id}">${icon(r.icon, active === r.id)}<span>${r.label}</span></a>`).join("")}
        </nav>
        <div class="promo-card">
          <strong>Download Mobile App</strong>
          <p>Manage tasks on the go</p>
          <button>Download</button>
        </div>
        <div class="sidebar-footer">
          <a class="nav-item" href="#">${icon("settings")}<span>Settings</span></a>
          <a class="nav-item" href="#">${icon("help")}<span>Help</span></a>
          <a class="nav-item" style="color:var(--error)" href="#">${icon("logout")}<span>Logout</span></a>
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
      <div class="searchbox">${icon("search")}<input placeholder="Search operations or tasks..." /><span class="kbd">⌘F</span></div>
      <div class="top-actions">
        ${state.health?.online ? `<span class="badge online">${icon("hub")} Gateway: Online</span>` : `<span class="badge failed">${icon("warning")} Gateway: Offline</span>`}
        <button class="icon-btn">${icon("mail")}</button>
        <button class="icon-btn">${icon("notifications")}</button>
        <div class="user-block">
          <div>
            <div class="user-name">Totok Michael</div>
            <div class="user-email">Admin Console</div>
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
    "batch-task": batchTaskPage,
    "task-query": taskQueryPage,
    "runtime-env": runtimePage,
    "api-debug": apiDebugPage,
    "task-detail-success": () => taskDetailPage(successTask, "Task Detail", "Real-time status and execution breakdown for automated workflow TXN-88219-X."),
    "task-detail-failure": () => taskDetailPage(failureTask, "Task Detail", "Failure evidence, healing context, and rerun controls for TX-4091-B."),
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
  if (state.route === "batch-task") bindBatchTask();
  if (state.route === "task-query") bindTaskQuery();
  if (state.route === "api-debug") bindApiDebug();
  if (state.route === "business") bindBusiness();
}

function dashboardPage() {
  const tasks = [...state.sessionTasks, ...mockTasks].slice(0, 5);
  return `
    ${pageHead("Dashboard", "Plan, prioritize, and accomplish your automation tasks with ease.", `<button class="btn primary" data-route-button="new-task">${icon("add")} Add Project</button><button class="btn">${icon("cloud_upload")} Import Data</button>`)}
    <section class="card pad" style="margin-bottom:20px">
      <div class="card-head">
        <div>
          <h3 class="card-title">${icon("hub")} automation-gateway</h3>
          <p class="card-subtitle">Status: ${state.health?.online ? "Stable connection" : "Offline"} • Cluster: Local Node</p>
        </div>
        ${statusBadge(state.health?.online ? "online" : "failed")}
      </div>
      <div class="muted tiny">Last Check ${state.health?.checkedAt ? formatTime(state.health.checkedAt) : "checking..."}</div>
    </section>
    <section class="grid cols-12">
      ${metricCard("Queued Tasks", "24", "north_east", "+5 from last month", "queued")}
      ${metricCard("Running", "12", "autorenew", "Active Execution", "running")}
      ${metricCard("Healing", "3", "auto_fix_high", "Self-repairing status", "healing")}
      ${metricCard("Succeeded", "1,024", "task_alt", "Last 24 hours", "succeeded")}
      <div class="span-8 card pad">
        <div class="card-head">
          <h3 class="card-title">Recent Automation Tasks</h3>
          <div class="actions"><button class="icon-btn">${icon("filter_list")}</button><button class="icon-btn">${icon("download")}</button></div>
        </div>
        ${taskTable(tasks)}
        <button class="btn ghost" data-route-button="task-query" style="margin-top:16px">View all tasks ${icon("arrow_forward")}</button>
      </div>
      <div class="span-4 grid">
        <div class="card pad">
          <h3 class="card-title">Quick Actions</h3>
          <div class="divider"></div>
          <button class="btn" data-route-button="new-task" style="width:100%;justify-content:space-between">${icon("language")} New Web Task ${icon("add_circle")}</button>
          <div style="height:12px"></div>
          <button class="btn" data-route-button="new-task" style="width:100%;justify-content:space-between">${icon("desktop_windows")} New Desktop Task ${icon("add_circle")}</button>
        </div>
        <div class="card pad">
          <h3 class="card-title">Monthly Target</h3>
          <div class="metric-value">70%</div>
          <div class="progress"><span style="width:70%"></span></div>
          <p class="muted tiny">21,450 / 30,000 Tasks Completed</p>
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
        <thead><tr><th>Task ID</th><th>Business Name</th><th>Kind</th><th>Status</th><th>Created At</th><th>Actions</th></tr></thead>
        <tbody>
          ${tasks.map((task) => `
            <tr>
              <td class="mono">${escapeHtml(task.task_id)}</td>
              <td>${escapeHtml(task.business || task.request?.business)}</td>
              <td>${kindBadge(task.kind || task.request?.kind)}</td>
              <td>${statusBadge(task.status)}</td>
              <td>${escapeHtml(formatTime(task.created_at))}</td>
              <td><button class="btn ghost" data-route-button="${task.status === "failed" ? "task-detail-failure" : "task-detail-success"}">View</button></td>
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
    ${pageHead("Business List", "Manage and monitor registered business modules across your infrastructure.", `<button class="btn">${icon("file_download")} Import Schema</button><button class="btn primary">${icon("add")} Register Business</button>`)}
    <div class="drawer-layout">
      <section class="card pad">
        <div class="card-head">
          <div class="tabs"><button class="active">All</button><button>Web</button><button>Desktop</button></div>
          <div class="searchbox" style="width:300px;box-shadow:none;border:1px solid var(--outline-variant)">${icon("search")}<input placeholder="Search task or business..." /></div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Kind</th><th>Module / Path</th><th>Source Path</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
              ${businesses.map((biz) => `
                <tr>
                  <td><strong>${escapeHtml(biz.name)}</strong></td>
                  <td>${kindBadge(biz.kind)}</td>
                  <td class="mono tiny">${escapeHtml(biz.module || biz.executable || "-")}</td>
                  <td class="mono tiny">${escapeHtml(biz.source || "-")}</td>
                  <td>${statusBadge("active")}</td>
                  <td><button class="btn ghost" data-business="${escapeHtml(biz.name)}">View Details</button></td>
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
          <p class="card-subtitle">Detailed Business Specification</p>
        </div>
        <button class="icon-btn">${icon("share")}</button>
      </div>
      <div class="grid cols-12">
        <div class="span-6 card pad" style="background:var(--surface-low)"><div class="label-caps">Execution Count</div><h2>12.4k</h2><span class="badge success">${icon("trending_up")} 12%</span></div>
        <div class="span-6 card pad" style="background:var(--surface-low)"><div class="label-caps">Error Rate</div><h2>0.02%</h2><span class="badge success">${icon("trending_down")} 4%</span></div>
      </div>
      <div class="divider"></div>
      <h3 class="card-title">Parameter Schema <span class="badge desktop">JSON Strict</span></h3>
      <table style="margin-top:12px">
        <thead><tr><th>Key</th><th>Type</th><th>Description</th></tr></thead>
        <tbody>
          <tr><td>query</td><td>String</td><td>搜索关键词，示例业务必填。</td></tr>
          <tr><td>limit</td><td>Integer</td><td>结果数量，建议 1 到 20。</td></tr>
          <tr><td>profile</td><td>String</td><td>浏览器持久上下文名称。</td></tr>
        </tbody>
      </table>
      <div class="divider"></div>
      <h3 class="card-title">Implementation Logic <button class="btn ghost" data-copy="${escapeHtml(JSON.stringify(sample))}">${icon("content_copy")} Copy Hook</button></h3>
      <pre class="code-panel">async function run(params, browser_pool, { task_id }) {
  const query = params.query;
  await automation.goto("https://www.bing.com/");
  return { query, titles, url };
}</pre>
      <div class="divider"></div>
      <button class="btn primary" data-route-button="new-task" style="width:100%">${icon("rocket_launch")} Submit Task</button>
    </aside>
  `;
}

function newTaskPage() {
  const businesses = state.businesses.length ? state.businesses : [{ name: "demo_search", kind: "web" }];
  const first = businesses[0]?.name || "demo_search";
  const request = { kind: "web", business: first, params: { query: "local automation console", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true };
  return `
    ${pageHead("New Task Configuration", "Configure automated execution parameters and business logic.", `<button class="btn">${icon("save")} Draft</button><button class="btn primary" id="submit-task">${icon("rocket_launch")} Submit Task</button>`)}
    <section class="grid cols-12">
      <div class="span-8 grid">
        <section class="card pad">
          <div class="card-head"><h3 class="card-title"><span class="badge desktop">01</span> Business Selection</h3><span class="badge desktop">Step 1 of 3</span></div>
          <div class="choice-row" id="kind-choices">
            <button class="choice-card active" data-kind="web">${icon("language")}<h4>Web Automation</h4><p>Browser workflows with Playwright profiles.</p></button>
            <button class="choice-card" data-kind="desktop">${icon("desktop_windows")}<h4>Desktop Client</h4><p>Registered AHK executable automation.</p></button>
            <button class="choice-card" data-kind="web">${icon("auto_fix_high")}<h4>Self-Healing</h4><p>Collect evidence and rerun after repairs.</p></button>
          </div>
          <div class="split" style="margin-top:18px">
            <div class="field"><label>Business</label><select class="select" id="task-business">${businesses.map((b) => `<option value="${escapeHtml(b.name)}" data-kind="${escapeHtml(b.kind)}">${escapeHtml(b.name)} (${escapeHtml(b.kind)})</option>`).join("")}</select></div>
            <div class="field"><label>Timeout Seconds</label><input class="input" id="task-timeout" type="number" value="300" min="1" max="3600" /></div>
          </div>
        </section>
        <section class="card pad">
          <div class="card-head"><h3 class="card-title"><span class="badge desktop">02</span> Runtime Parameters</h3><button class="btn" id="format-json">${icon("format_align_left")} Beautify</button></div>
          <textarea id="task-params" class="code-input">${jsonBlock(request.params)}</textarea>
        </section>
      </div>
      <aside class="span-4 grid">
        <section class="card pad">
          <h3 class="card-title"><span class="badge desktop">03</span> Execution Controls</h3>
          <div class="divider"></div>
          <div class="field"><label>Max Retries</label><input class="input" id="task-retries" type="number" value="1" min="0" max="5" /></div>
          <div class="divider"></div>
          <label class="field"><span class="label-caps">Self Healing</span><select class="select" id="task-healing"><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
        </section>
        <section class="card pad">
          <div class="card-head"><h3 class="card-title">Request Preview</h3><button class="icon-btn" id="copy-preview">${icon("content_copy")}</button></div>
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
      document.getElementById("submit-result").innerHTML = `<h3 class="card-title">${icon("check_circle", true)} Task Created</h3><p class="mono">${payload.data.task_id}</p><button class="btn primary" data-route-button="task-query">${icon("search_check")} View Detail</button>`;
      bindGlobalEvents();
      notify("任务已进入队列");
    } catch (error) {
      document.getElementById("submit-result").innerHTML = `<h3 class="card-title" style="color:var(--error)">${icon("warning")} Submit Failed</h3><pre class="code-panel">${escapeHtml(JSON.stringify(error.payload || { msg: error.message }, null, 2))}</pre>`;
    }
  });
}

function batchTaskPage() {
  const sample = { tasks: [{ kind: "web", business: "demo_search", params: { query: "关键词", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true }, { kind: "web", business: "demo_search", params: { query: "自愈控制台", limit: 3, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true }] };
  return `
    ${pageHead("Batch Task", "Create, validate, and submit multiple automation tasks in one controlled batch.", `<button class="btn">${icon("add")} Add Task</button><button class="btn primary" id="submit-batch">${icon("send")} Submit Batch</button>`)}
    <section class="grid cols-12">
      <div class="span-7 card pad">
        <div class="card-head"><h3 class="card-title">${icon("account_tree")} Batch Payload</h3><button class="btn" id="beautify-batch">Beautify</button></div>
        <textarea id="batch-json" class="code-input" style="min-height:520px">${jsonBlock(sample)}</textarea>
      </div>
      <aside class="span-5 grid">
        <div class="card pad">
          <h3 class="card-title">Validation Summary</h3>
          <div class="grid cols-12" style="margin-top:16px">${metricSmall("Total", "2")}${metricSmall("Valid", "2")}${metricSmall("Errors", "0")}</div>
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
      box.innerHTML = `<h3 class="card-title">${icon("check_circle", true)} Results</h3>${(payload.data || []).map((item, index) => `<p>${index + 1}. ${item.code === 0 ? statusBadge("succeeded") : statusBadge("failed")} <span class="mono">${escapeHtml(item.data?.task_id || item.msg)}</span></p>`).join("")}`;
    } catch (error) {
      box.innerHTML = `<h3 class="card-title" style="color:var(--error)">${icon("warning")} Batch Failed</h3><pre class="code-panel">${escapeHtml(JSON.stringify(error.payload || { msg: error.message }, null, 2))}</pre>`;
    }
  });
}

function taskQueryPage() {
  const task = state.queryTask || state.lastTask || successTask;
  return `
    ${pageHead("Task Query", "Trace and monitor specific automation workflows with precision.", "")}
    <section class="grid cols-12">
      <aside class="span-4 grid">
        <div class="card pad">
          <div class="label-caps">Identifier Lookup</div>
          <div class="searchbox" style="width:100%;margin-top:14px;box-shadow:none;border:1px solid var(--outline-variant)">${icon("fingerprint")}<input id="task-id-input" placeholder="输入 task_id" value="${escapeHtml(state.lastTask?.task_id || "")}" /></div>
          <button class="btn primary" id="query-task" style="width:100%;margin-top:14px">${icon("search_check")} Search</button>
          <p class="muted tiny">${icon("info")} Supports global UUIDs, local sequence IDs, and partial wildcards.</p>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("history")} Recent Queries</h3>
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
        <div><h3 class="card-title">Task Preview ${statusBadge(task.status)}</h3><p class="card-subtitle">Monitoring ID: <span class="mono">${escapeHtml(task.task_id)}</span></p></div>
        <div class="actions"><button class="btn">${icon("file_download")} Log Export</button><button class="btn primary" data-route-button="${task.status === "failed" ? "task-detail-failure" : "task-detail-success"}">${icon("open_in_new")} Open Detail</button></div>
      </div>
      <div class="grid cols-12">${metricSmall("Attempts", task.attempts || 0)}${metricSmall("Status", task.status)}${metricSmall("Healed", task.healed ? "True" : "False")}</div>
      <div class="divider"></div>
      <pre class="code-panel">${jsonBlock(task)}</pre>
    </div>
  `;
}

function taskDetailPage(task, title, subtitle) {
  return `
    ${pageHead(title, subtitle, `<button class="btn" data-copy="${escapeHtml(task.task_id)}">${icon("content_copy")} Copy ID</button><button class="btn primary" id="retry-current">${icon("replay")} Rerun Task</button>`)}
    <section class="grid cols-12">
      <div class="span-12 card pad">
        <div class="grid cols-12">
          <div class="span-3"><div class="label-caps">Current Status</div><h2>${statusBadge(task.status)}</h2></div>
          <div class="span-3"><div class="label-caps">Task ID</div><h2 class="mono">${escapeHtml(task.task_id)}</h2></div>
          <div class="span-3"><div class="label-caps">Kind</div><h2>${kindBadge(task.request.kind)}</h2></div>
          <div class="span-3"><div class="label-caps">Attempts</div><h2>${task.attempts}</h2><p class="muted tiny">${task.healed ? "Healed rerun attempted" : "Succeeded on first run"}</p></div>
        </div>
      </div>
      <div class="span-7 grid">
        <div class="card pad">
          <div class="card-head"><h3 class="card-title">${icon("database")} Execution Result</h3><button class="icon-btn" data-copy="${escapeHtml(JSON.stringify(task.result || {}, null, 2))}">${icon("content_copy")}</button></div>
          <pre class="code-panel">${jsonBlock(task.result)}</pre>
        </div>
        <div class="card pad">
          <div class="card-head"><h3 class="card-title">${icon("outbound")} Original Request</h3><button class="icon-btn" data-copy="${escapeHtml(JSON.stringify(task.request || {}, null, 2))}">${icon("content_copy")}</button></div>
          <pre class="code-panel">${jsonBlock(task.request)}</pre>
        </div>
        ${task.error_traceback ? `<div class="card pad"><div class="card-head"><h3 class="card-title" style="color:var(--error)">${icon("warning")} error_traceback.log</h3><button class="icon-btn" data-copy="${escapeHtml(task.error_traceback)}">${icon("content_copy")}</button></div><pre class="code-panel">${escapeHtml(task.error_traceback)}</pre></div>` : ""}
      </div>
      <aside class="span-5 grid">
        <div class="card pad">
          <h3 class="card-title">${icon("schedule")} Execution Timeline</h3>
          <div class="divider"></div>
          <div class="timeline">
            ${timelineItem("Created", formatTime(task.created_at), "add_circle")}
            ${timelineItem("Started", formatTime(task.started_at), "play_circle")}
            ${timelineItem(task.status === "failed" ? "Finished (Failed)" : "Finished", formatTime(task.finished_at), task.status === "failed" ? "error" : "check_circle")}
          </div>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("auto_fix_high")} Healing Engine</h3>
          <div class="divider"></div>
          <p>${statusBadge(task.healed ? "healing" : "queued")} Healed Status: <strong>${task.healed ? "True (Active)" : "False"}</strong></p>
          <p class="muted">System collected traceback, screenshot path, source path, and request payload for repair context.</p>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("photo_camera")} Snapshot at Failure</h3>
          <p class="mono tiny">${escapeHtml(task.screenshot || "No screenshot generated")}</p>
          <div class="divider"></div>
          <h3 class="card-title">${icon("hub")} Business Source</h3>
          <p class="mono tiny">${escapeHtml(task.business_source || "-")}</p>
        </div>
      </aside>
    </section>
  `;
}

function timelineItem(title, time, glyph) {
  return `<div class="timeline-item"><div class="timeline-dot">${icon(glyph)}</div><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(time)}</span></div></div>`;
}

function runtimePage() {
  return `
    ${pageHead("Runtime Environment", "Monitor local self-healing clusters and manage automated resources.", `<button class="btn dark">${icon("terminal")} Open Console</button><button class="btn primary" id="refresh-health">${icon("refresh")} Restart Cluster</button>`)}
    <section class="grid cols-12">
      <div class="span-8 grid">
        <div class="card pad">
          <div class="card-head"><h3 class="card-title">Live Cluster: Local Node</h3>${statusBadge(state.health?.online ? "online" : "failed")}</div>
          <div class="grid cols-12">${metricSmall("CPU Load", "24%")}${metricSmall("Memory", "4.2 GB")}${metricSmall("Uptime", "12d 04h")}</div>
        </div>
        <div class="card pad">
          <h3 class="card-title">${icon("folder")} Runtime Directories</h3>
          <table style="margin-top:16px">
            <thead><tr><th>Folder Name</th><th>File Count</th><th>Size</th><th>Action</th></tr></thead>
            <tbody>
              <tr><td class="mono">logs/tasks</td><td>Task snapshots</td><td>142.5 MB</td><td>${icon("download")}</td></tr>
              <tr><td class="mono">logs/screenshots</td><td>Failure evidence</td><td>2.1 GB</td><td>${icon("visibility")}</td></tr>
              <tr><td class="mono">browser_profiles</td><td>Persistent profiles</td><td>450 MB</td><td>${icon("security")}</td></tr>
              <tr><td class="mono">runtime/snapshots/healing</td><td>Healing context</td><td>36 MB</td><td>${icon("settings_backup_restore")}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <aside class="span-4 grid">
        <div class="card pad">
          <h3 class="card-title">${icon("auto_fix_high")} Self-Healing Logic</h3>
          <p class="muted">Donezo monitors every automated process in real time. If a task fails due to a network hiccup or UI change, the autonomous repair engine identifies the root cause and attempts a recovery workflow.</p>
          <button class="btn ghost">Learn about AI-Recovery ${icon("arrow_forward")}</button>
        </div>
        <div class="card pad">
          <h3 class="card-title">Data Throughput <span class="badge success">LIVE</span></h3>
          <div class="mini-chart">${[50, 72, 48, 83, 62, 96, 70, 88].map((h) => `<span style="height:${h}%"></span>`).join("")}</div>
        </div>
      </aside>
    </section>
  `;
}

function apiDebugPage() {
  const request = { kind: "web", business: "demo_search", params: { query: "Weekly Infrastructure Sync", limit: 5, profile: "demo" }, args: [], timeout_seconds: 300, max_retries: 1, enable_self_healing: true };
  return `
    ${pageHead("API Debug", "Test, inspect, and monitor your automation endpoints in real-time.", `<button class="btn">${icon("history")} History</button><button class="btn primary" id="send-debug">${icon("send")} Send</button>`)}
    <section class="grid cols-12">
      <div class="span-6 card pad">
        <div class="card-head"><h3 class="card-title">${icon("send")} Request Setup</h3><div class="segmented"><button class="active">POST</button><button>GET</button><button>PUT</button><button>DELETE</button></div></div>
        <div class="field"><label>Endpoint</label><select class="select" id="debug-endpoint"><option value="/api/v1/tasks">POST /api/v1/tasks</option><option value="/health">GET /health</option><option value="/api/v1/businesses">GET /api/v1/businesses</option></select></div>
        <div class="divider"></div>
        <div class="card-head"><h3 class="card-title">JSON Request Body</h3><button class="btn" id="beautify-debug">Beautify</button></div>
        <textarea class="code-input" id="debug-body">${jsonBlock(request)}</textarea>
      </div>
      <div class="span-6 card pad">
        <div class="card-head"><h3 class="card-title">${icon("output")} Response</h3><button class="icon-btn" id="copy-debug-response">${icon("content_copy")}</button></div>
        <div class="grid cols-12" style="margin-bottom:18px">${metricSmall("Status", "Ready")}${metricSmall("Latency", "0 ms")}${metricSmall("Size", "0 KB")}</div>
        <pre class="code-panel" id="debug-response">${jsonBlock({ status: "ready", timestamp: new Date().toISOString() })}</pre>
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
