const portalState = {
  token: sessionStorage.getItem("token_portal_access") || "",
  view: "overview",
  profile: null,
  workspaces: [],
  projects: [],
  activeWorkspaceId: Number(sessionStorage.getItem("loktoken_workspace_id") || 0),
  keys: [],
  models: [],
  dashboard: null,
  overviewMode: "cost",
  usage: { page: 1, pageSize: 20, analyticsMode: "tokens", result: null, analytics: null },
  keyFilters: { search: "", status: "" },
  keyColumns: { name: true, key: true, project: true, usage: true, expires: true, "last-used": true, status: true, created: true },
  orders: [],
  marketplace: { query: "", modality: "all", provider: "", providerFilter: "", health: "all", sort: "default", compare: [] },
};

const portalTitles = { overview: "用户概览", models: "模型广场", quota: "额度管理", keys: "密钥管理", usage: "请求记录", orders: "订单管理", redeem: "兑换福利" };

const portalViewFromUrl = () => {
  const view = new URLSearchParams(window.location.search).get("view");
  return Object.prototype.hasOwnProperty.call(portalTitles, view) ? view : "overview";
};

function portalViewUrl(view) {
  const params = new URLSearchParams(window.location.search);
  params.set("view", view);
  return `${window.location.pathname}?${params.toString()}`;
}

function primePortalHistory(view) {
  if (window.history.state?.app === "portal" && window.history.state.view === view) return;
  const url = portalViewUrl(view);
  window.history.replaceState({ app: "portal", view }, "", url);
  window.history.pushState({ app: "portal", view }, "", url);
}

function updatePortalHistory(view, mode = "push") {
  if (mode === "none") return;
  const current = window.history.state;
  if (mode === "replace" || current?.app !== "portal" || current.view !== view) {
    window.history[mode === "replace" ? "replaceState" : "pushState"]({ app: "portal", view }, "", portalViewUrl(view));
  }
}

function navigatePortalBack() {
  if (portalState.view === "overview") return;
  if (window.history.state?.app === "portal") window.history.back();
  else switchPortalView("overview");
}

function portalIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatMoney(micros) {
  return (Number(micros || 0) / 1_000_000).toLocaleString("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
}

function formatTokenPricePerMillion(microsPerThousand) {
  return formatMoney(Number(microsPerThousand || 0) * 1000);
}

function formatMaxOutputTokens(value, fallback = "按上游配置") {
  const tokens = Number(value || 0);
  if (!tokens) return fallback;
  if (tokens >= 1_000_000) return `${tokens / 1_000_000}M`;
  const kiloTokens = tokens / 1_000;
  return `${Number.isInteger(kiloTokens) ? kiloTokens : kiloTokens.toFixed(1)}K`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 18 ? `${text.slice(0, 10)}...${text.slice(-5)}` : text;
}

function emptyRow(columns) {
  return `<tr><td class="empty-row" colspan="${columns}">暂无数据</td></tr>`;
}

function statusBadge(status) {
  const map = {
    success: ["成功", "success"],
    error: ["失败", "error"],
    rejected: ["已拒绝", "warning"],
    pending: ["待处理", "warning"],
    paid: ["已入账", "success"],
    refunded: ["已退款", "neutral"],
  };
  const item = map[status] || [status, "neutral"];
  return `<span class="badge ${item[1]}">${escapeHtml(item[0])}</span>`;
}

function activeBadge(active) {
  return `<span class="badge ${active ? "success" : "neutral"}">${active ? "有效" : "已停用"}</span>`;
}

function keyExpiry(item) {
  const values = [item.expires_at, item.trial_expires_at].filter(Boolean).map((value) => new Date(value));
  return values.length ? new Date(Math.min(...values.map((value) => value.getTime()))) : null;
}

function modelHealth(item) {
  const map = {
    healthy: ["success", "渠道健康"],
    checking: ["warning", "待检测"],
    degraded: ["warning", "部分异常"],
    unavailable: ["error", "暂不可用"],
  };
  const [kind, label] = map[item.health_status] || ["neutral", "状态未知"];
  return `<span class="badge ${kind}">${label}</span>`;
}

function gatewayCapabilitySummary(item) {
  const profile = item.gateway_profile || {};
  const protocol = profile.protocol === "async_task" ? "异步任务" : profile.protocol === "openai_chat_completions" ? "Chat Completions" : "兼容协议";
  const stream = profile.stream_transport === "sse" ? "SSE 流式" : profile.stream_transport === "none" ? "非流式" : "按服务商";
  const auth = profile.auth_scheme === "bearer" ? "Bearer" : profile.auth_scheme || "按服务商";
  return `${protocol} · ${stream} · ${auth}`;
}

function keyStatusBadge(item) {
  if (item.revoked_at) return '<span class="badge error">已撤销</span>';
  const expiry = keyExpiry(item);
  if (expiry && expiry <= new Date()) return '<span class="badge warning">已过期</span>';
  return activeBadge(item.active);
}

function formatKeyBudget(item) {
  const spent = formatMoney(item.spent_micros);
  return item.spending_limit_micros == null ? `${spent} / 不限` : `${spent} / ${formatMoney(item.spending_limit_micros)}`;
}

function hasUsableApiKey() {
  const now = new Date();
  return portalState.keys.some((item) => item.active && (!keyExpiry(item) || keyExpiry(item) > now));
}

function renderIntegrationGuide() {
  const target = document.getElementById("portal-integration-guide");
  if (!target || !portalState.profile) return;
  const hasKey = hasUsableApiKey();
  const hasBalance = Number(portalState.profile.balance_micros || 0) > 0;
  const hasModels = portalState.models.some((item) => item.health_status !== "unavailable");
  const hasRequests = Number(portalState.profile.request_count || 0) > 0;
  target.hidden = hasKey && hasRequests;
  if (target.hidden) return;
  const status = (complete) => `<span class="integration-status ${complete ? "complete" : "pending"}">${complete ? "已完成" : "待完成"}</span>`;
  target.innerHTML = `
    <div class="integration-guide-copy"><div class="integration-guide-icon"><i data-lucide="waypoints"></i></div><div><p>应用接入</p><h2>${hasKey ? "完成模型配置后即可开始调用" : "密钥管理，接入应用"}</h2><span>密钥仅保存在你的应用配置中，不会通过跳转链接传递。</span></div></div>
    <ol class="integration-steps">
      <li><span>1</span><div><strong>创建并保存密钥</strong><small>创建后只展示一次，请先复制保存。</small></div>${status(hasKey)}</li>
      <li><span>2</span><div><strong>选择可用模型</strong><small>${hasModels ? "LokToken 已提供可选模型。" : "当前暂无可用模型，请联系管理员。"}</small></div>${status(hasModels)}</li>
      <li><span>3</span><div><strong>在应用中完成配置</strong><small>在任意兼容 OpenAI 的应用中填写 Base URL、API Key 和模型 ID。</small></div>${status(hasRequests)}</li>
    </ol>
    <div class="integration-guide-actions">
      ${hasKey ? '<button class="primary-button" type="button" data-go="keys"><i data-lucide="key-round"></i><span>管理 API Key</span></button>' : '<button class="primary-button" type="button" data-action="create-key"><i data-lucide="key-round"></i><span>创建 API Key</span></button>'}
      <button class="secondary-button" type="button" data-go="models"><i data-lucide="boxes"></i><span>查看模型</span></button>
      ${hasBalance ? "" : '<button class="secondary-button" type="button" data-go="quota"><i data-lucide="wallet-cards"></i><span>管理额度</span></button>'}
    </div>`;
  portalIcons();
}

function renderMetrics(targetId, items) {
  document.getElementById(targetId).innerHTML = items.map((item) => `
    <article class="metric">
      <div><div class="metric-label">${escapeHtml(item.label)}</div><div class="metric-value">${escapeHtml(item.value)}</div>${item.meta ? `<div class="metric-meta">${escapeHtml(item.meta)}</div>` : ""}</div>
      <div class="metric-icon ${item.color || ""}"><i data-lucide="${item.icon}"></i></div>
    </article>
  `).join("");
  portalIcons();
}

async function portalApi(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (portalState.token) headers.Authorization = `Bearer ${portalState.token}`;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    const messageMap = {
      "missing trial access token": "缺少试用令牌，请使用管理员生成的试用链接进入。",
      "invalid trial access token": "试用令牌格式无效，请重新生成试用链接。",
      "invalid or expired trial access token": "试用令牌无效或已过期，请重新生成试用链接。",
      "billing account is inactive": "当前账户已停用，请联系管理员。",
      "insufficient balance": "账户余额不足，请先充值或兑换额度后再调用模型。",
      "api key spending limit exceeded": "该 API Key 已达到消费上限，请更换 Key 或调整额度限制。",
      "invalid login credentials": "账号或密码错误。",
      "invalid account password": "当前密码错误。",
      "account is inactive": "当前账户已停用，请联系管理员。",
      "security contact verification token is invalid or expired": "安全联系方式验证码无效或已过期，请重新发送。",
      "security contact verification delivery is not configured": "安全验证服务尚未配置，请联系管理员。",
      "security contact verification delivery failed": "验证码发送失败，请稍后重试。",
      "login id already exists": "该账号已存在，请更换账号或直接登录。",
      "redemption code not found": "兑换码不存在，请检查后重试。",
      "redemption code already used by this account": "当前账户已领取过该兑换码。",
      "redemption code is unavailable": "兑换码已失效、过期或已被领取完毕。",
    };
    detail = messageMap[detail] || detail;
    if (response.status === 401 || response.status === 403) {
      sessionStorage.removeItem("token_portal_access");
      portalState.token = "";
      const tokenInput = document.getElementById("portal-token");
      if (tokenInput) tokenInput.value = "";
      showPortalAuth(detail);
    }
    throw new Error(detail);
  }
  return response.json();
}

async function establishPortalSession(result) {
  portalState.token = result.access_token;
  sessionStorage.setItem("token_portal_access", portalState.token);
  await loadProfile();
  await loadWorkspaces();
  showPortalShell();
  await switchPortalView(portalViewFromUrl());
}

function setAuthMode(mode) {
  const forms = { trial: "portal-auth-form", login: "portal-login-form", register: "portal-register-form" };
  Object.entries(forms).forEach(([key, id]) => { document.getElementById(id).hidden = key !== mode; });
  document.querySelectorAll(".auth-mode-tabs button").forEach((button) => button.classList.toggle("active", button.dataset.authMode === mode));
  document.getElementById("portal-auth-error").textContent = "";
  portalIcons();
}

function prioritizeFederatedLogin(button, subtitle) {
  const loginForm = document.getElementById("portal-login-form");
  const localLoginButton = document.getElementById("local-login-button");
  const subtitleElement = document.getElementById("portal-auth-subtitle");
  if (!button || !loginForm || !localLoginButton) return;
  button.hidden = false;
  button.classList.remove("secondary-button");
  button.classList.add("primary-button");
  localLoginButton.classList.remove("primary-button");
  localLoginButton.classList.add("secondary-button");
  loginForm.insertBefore(button, localLoginButton);
  if (subtitleElement) subtitleElement.textContent = subtitle;
}

function passwordSetupDialog(resetToken = "", invitation = false) {
  const title = invitation ? "接受账户邀请" : "设置新密码";
  const copy = invitation ? "设置你的登录密码后即可进入用户中心。此邀请只能使用一次。" : "输入一次性凭证并设置新密码。";
  openPortalDialog(title, `<form id="portal-password-reset-confirm"><div class="dialog-body"><p class="dialog-copy">${copy}</p><div class="field"><label for="reset-token">${invitation ? "邀请凭证" : "重置凭证"}</label><input id="reset-token" name="reset_token" value="${escapeHtml(resetToken)}" autocomplete="one-time-code" required></div><div class="field"><label for="reset-password">新密码</label><input id="reset-password" name="password" type="password" autocomplete="new-password" minlength="8" required></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>稍后处理</button><button class="primary-button" type="submit">${invitation ? "接受邀请" : "更新密码"}</button></div></div></form>`);
  document.getElementById("portal-password-reset-confirm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await establishPortalSession(await portalApi("/auth/password-reset/confirm", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }));
      closePortalDialog();
      portalToast(invitation ? "账户已启用，欢迎进入用户中心" : "密码已更新，其他登录会话已失效");
    } catch (error) { portalToast(error.message, true); }
  });
}

function passwordResetDialog() {
  openPortalDialog("重置密码", `<form id="portal-password-reset-form"><div class="dialog-body"><p class="dialog-copy">输入注册账号后，我们会向已验证的安全联系方式发送一次性重置凭证。为保护账号，不论账号是否存在都会返回相同提示。</p><div class="field"><label for="reset-login-id">账号</label><input id="reset-login-id" name="login_id" autocomplete="username" required minlength="3"></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit">发送重置凭证</button></div></div></form>`);
  document.getElementById("portal-password-reset-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await portalApi("/auth/password-reset/request", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
      passwordSetupDialog(result.development_reset_token || "");
    } catch (error) { portalToast(error.message, true); }
  });
}

function securityContactVerificationDialog(result = {}) {
  const developmentToken = result.development_verification_token || "";
  openPortalDialog("验证安全联系方式", `<form id="portal-contact-verification-form"><div class="dialog-body"><p class="dialog-copy">验证码已发送到你填写的安全联系方式。验证成功后，该联系方式才能用于找回密码。</p><div class="field"><label for="contact-verification-token">验证码</label><input id="contact-verification-token" name="verification_token" value="${escapeHtml(developmentToken)}" autocomplete="one-time-code" required minlength="16"><small class="field-hint">${developmentToken ? "开发环境已自动填入测试验证码。" : "请输入收到的一次性验证码。"}</small></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>稍后验证</button><button class="primary-button" type="submit">确认验证</button></div></form>`);
  document.getElementById("portal-contact-verification-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await portalApi("/portal/security/contact/confirm", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
      await loadProfile();
      closePortalDialog();
      portalToast("安全联系方式已验证，可用于找回密码");
    } catch (error) { portalToast(error.message, true); }
  });
}

function showPortalAuth(message = "") {
  document.getElementById("portal-auth").hidden = false;
  document.getElementById("portal-shell").hidden = true;
  document.getElementById("portal-auth-error").textContent = message;
}

function showPortalShell() {
  document.getElementById("portal-auth").hidden = true;
  document.getElementById("portal-shell").hidden = false;
}

function portalToast(message, error = false) {
  const element = document.getElementById("portal-toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("show");
  clearTimeout(portalToast.timer);
  portalToast.timer = setTimeout(() => element.classList.remove("show"), 2600);
}

async function loadProfile() {
  portalState.profile = await portalApi("/portal/profile");
  document.getElementById("portal-user-name").textContent = portalState.profile.name;
  document.getElementById("portal-user-id").textContent = portalState.profile.external_user_id;
  document.getElementById("portal-user-avatar").textContent = (portalState.profile.name || portalState.profile.external_user_id || "用").slice(0, 1).toUpperCase();
  loadPortalNotificationSummary().catch(() => {});
  return portalState.profile;
}

const notificationLabels = {
  low_balance_warning: "账户余额预警", api_key_expiring: "API Key 即将过期", api_key_expired: "API Key 已过期",
  api_key_created: "API Key 已创建", api_key_rotated: "API Key 已轮换", api_key_revoked: "API Key 已撤销",
  api_key_status_updated: "API Key 状态已更新", account_logged_in: "账户登录", account_registered: "账户已注册",
  password_reset_requested: "密码重置请求", portal_sessions_revoked: "登录会话已退出",
};

async function loadPortalNotificationSummary() {
  const result = await portalApi("/portal/security-notifications");
  const badge = document.getElementById("portal-notification-count");
  const unread = Number(result.unread_count || 0);
  badge.textContent = unread > 99 ? "99+" : String(unread);
  badge.hidden = unread === 0;
  return result;
}

async function portalNotificationDialog() {
  const result = await portalApi("/portal/security-notifications");
  const rows = result.data.map((item) => {
    const details = item.details || {};
    const description = item.event_type === "low_balance_warning"
      ? `当前余额 ${formatMoney(details.balance_micros)}，低于预警阈值。`
      : item.event_type.startsWith("api_key_") && details.name ? `${escapeHtml(details.name)}${details.expires_at ? ` · ${formatDate(details.expires_at)}` : ""}` : "账户安全事件已记录。";
    return `<div class="notification-row ${item.read_at ? "read" : "unread"}"><span class="notification-icon"><i data-lucide="${item.read_at ? "bell" : "bell-ring"}"></i></span><div><strong>${escapeHtml(notificationLabels[item.event_type] || item.event_type)}</strong><p>${description}</p><small>${formatDate(item.created_at)}</small></div>${item.read_at ? "" : `<button class="icon-button compact-icon" data-read-notification="${item.id}" title="标记已读" aria-label="标记已读"><i data-lucide="check"></i></button>`}</div>`;
  }).join("") || '<p class="dialog-copy">暂无通知。</p>';
  openPortalDialog("通知中心", `<div class="dialog-body notification-dialog-body"><div class="notification-list">${rows}</div></div><div class="dialog-actions"><button class="secondary-button" type="button" id="portal-read-all">全部标记已读</button><button class="primary-button" type="button" data-close>完成</button></div>`);
  document.getElementById("portal-read-all").addEventListener("click", async () => {
    try { await portalApi("/portal/security-notifications/read-all", { method: "POST", body: "{}" }); await portalNotificationDialog(); await loadPortalNotificationSummary(); } catch (error) { portalToast(error.message, true); }
  });
}

function activeProjectId() {
  return portalState.projects.find((project) => project.slug === "default")?.id || portalState.projects[0]?.id || null;
}

async function loadWorkspaces() {
  const result = await portalApi("/portal/workspaces");
  portalState.workspaces = result.data;
  if (!portalState.workspaces.some((workspace) => workspace.id === portalState.activeWorkspaceId)) {
    portalState.activeWorkspaceId = portalState.workspaces[0]?.id || 0;
  }
  if (portalState.activeWorkspaceId) {
    const projects = await portalApi(`/portal/workspaces/${portalState.activeWorkspaceId}/projects`);
    portalState.projects = projects.data;
    sessionStorage.setItem("loktoken_workspace_id", String(portalState.activeWorkspaceId));
  }
}

async function workspaceManagerDialog() {
  await loadWorkspaces();
  const current = portalState.workspaces.find((workspace) => workspace.id === portalState.activeWorkspaceId);
  if (!current) return;
  const canManage = ["owner", "admin"].includes(current.role);
  const projectRows = portalState.projects.map((project) => `<div class="status-row"><span class="status-name"><i data-lucide="folder-kanban"></i>${escapeHtml(project.name)}<small>${escapeHtml(project.slug)}</small></span><span class="secondary">${project.active ? "有效" : "已停用"}</span></div>`).join("") || '<p class="dialog-copy">暂无项目。</p>';
  openPortalDialog("空间与项目", `<div class="dialog-body"><div class="key-detail-grid"><div><span>新建资源归属空间</span><strong>${escapeHtml(current.name)}</strong></div><div><span>访问角色</span><strong>${escapeHtml(current.role)}</strong></div></div><div class="section-header"><div><h2>项目</h2><p>新建 API Key 和充值申请会归属当前空间的默认项目。</p></div></div><div class="status-list">${projectRows}</div>${canManage ? `<form id="workspace-project-form"><div class="field"><label for="workspace-project-name">新增项目</label><input id="workspace-project-name" name="name" maxlength="120" required placeholder="例如：生产环境"></div><button class="secondary-button" type="submit"><i data-lucide="plus"></i><span>创建项目</span></button></form>` : ""}<div class="section-header"><div><h2>组织空间</h2><p>创建组织后可邀请已注册的 LokToken 用户协作。</p></div></div><form id="workspace-org-form"><div class="field"><label for="workspace-org-name">组织名称</label><input id="workspace-org-name" name="name" maxlength="120" required placeholder="例如：LokSystem 团队"></div><button class="primary-button" type="submit"><i data-lucide="building-2"></i><span>创建组织</span></button></form></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button></div>`);
  const workspaceDialogBody = document.querySelector("#portal-dialog-content .dialog-body");
   workspaceDialogBody.insertAdjacentHTML("afterbegin", `<div class="field"><label for="workspace-manager-select">当前空间</label><select id="workspace-manager-select">${portalState.workspaces.map((workspace) => `<option value="${workspace.id}" ${workspace.id === portalState.activeWorkspaceId ? "selected" : ""}>${workspace.type === "personal" ? "个人" : "组织"} · ${escapeHtml(workspace.name)}</option>`).join("")}</select><small class="field-hint">新建 API Key 和充值申请将归属所选空间的默认项目。</small></div>`);
  const workspaceOrgName = document.getElementById("workspace-org-name");
  if (workspaceOrgName) workspaceOrgName.placeholder = "例如：你的团队";
  document.getElementById("workspace-manager-select").addEventListener("change", async (event) => {
    portalState.activeWorkspaceId = Number(event.target.value);
    try { await loadWorkspaces(); await workspaceManagerDialog(); portalToast("已切换资源归属空间"); } catch (error) { portalToast(error.message, true); }
  });
  const projectForm = document.getElementById("workspace-project-form");
  if (projectForm) projectForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try { await portalApi(`/portal/workspaces/${current.id}/projects`, { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); portalToast("项目已创建"); await workspaceManagerDialog(); } catch (error) { portalToast(error.message, true); }
  });
  document.getElementById("workspace-org-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try { const organization = await portalApi("/portal/organizations", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); portalState.activeWorkspaceId = organization.workspace_id; await loadWorkspaces(); portalToast("组织空间已创建"); await workspaceManagerDialog(); } catch (error) { portalToast(error.message, true); }
  });
}

async function loadOverview() {
  const [profile, keys, models] = await Promise.all([
    loadProfile(),
    portalApi("/portal/api-keys"),
    portalApi("/portal/models"),
  ]);
  portalState.keys = keys.data;
  portalState.models = models.data;
  populateOverviewFilters();
  document.getElementById("overview-balance").textContent = formatMoney(profile.balance_micros);
  document.getElementById("overview-key-count").textContent = formatNumber(profile.api_key_count);
  document.getElementById("overview-request-count").textContent = formatNumber(profile.request_count);
  document.getElementById("portal-base-url").textContent = `${window.location.origin}/v1`;
  renderIntegrationGuide();
  await loadOverviewDashboard();
}

function populateOverviewFilters() {
  const modelSelect = document.getElementById("overview-model");
  const keySelect = document.getElementById("overview-key");
  const selectedModel = modelSelect.value;
  const selectedKey = keySelect.value;
  modelSelect.innerHTML = '<option value="">全部模型</option>' + portalState.models.map((item) => `<option value="${escapeHtml(item.public_name)}">${escapeHtml(item.public_name)}</option>`).join("");
  keySelect.innerHTML = '<option value="">全部 Key</option>' + portalState.keys.map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.key_prefix)}</option>`).join("");
  modelSelect.value = selectedModel;
  keySelect.value = selectedKey;
}

function overviewQueryString() {
  const params = new URLSearchParams({ days: document.getElementById("overview-days").value });
  const model = document.getElementById("overview-model").value;
  const key = document.getElementById("overview-key").value;
  if (model) params.set("model", model);
  if (key) params.set("api_key_id", key);
  return params.toString();
}

async function loadOverviewDashboard() {
  const chartWrap = document.getElementById("overview-chart-wrap");
  chartWrap.classList.add("loading");
  try {
    portalState.dashboard = await portalApi(`/portal/dashboard?${overviewQueryString()}`);
    renderOverviewDashboard();
  } finally {
    chartWrap.classList.remove("loading");
  }
}

function overviewModeConfig(mode) {
  const configs = {
    cost: { field: "amount_micros", label: "周期费用", value: (item) => Number(item.amount_micros || 0) / 1_000_000, format: (value) => formatMoney(value * 1_000_000) },
    tokens: { field: "total_tokens", label: "周期 Token", value: (item) => Number(item.total_tokens || 0), format: formatNumber },
    requests: { field: "request_count", label: "周期请求", value: (item) => Number(item.request_count || 0), format: formatNumber },
  };
  return configs[mode] || configs.cost;
}

function chartValueLabel(value, mode) {
  if (mode === "cost") {
    if (value === 0) return "¥0";
    if (value < 0.01) return `¥${value.toFixed(6).replace(/0+$/, "")}`;
    return `¥${value.toFixed(2)}`;
  }
  return value >= 1000 ? Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value) : formatNumber(Math.round(value));
}

function renderUsageChart(daily, mode) {
  const svg = document.getElementById("overview-chart");
  const config = overviewModeConfig(mode);
  const values = daily.map(config.value);
  const width = 820;
  const height = 260;
  const padding = { top: 20, right: 18, bottom: 38, left: 62 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maxValue = Math.max(...values, 0);
  const scaleMax = maxValue || (mode === "cost" ? 0.000001 : 1);
  const xAt = (index) => padding.left + (daily.length === 1 ? 0 : index * plotWidth / (daily.length - 1));
  const yAt = (value) => padding.top + plotHeight - (value / scaleMax) * plotHeight;
  const tickMarkup = [0, 1, 2, 3, 4].map((index) => {
    const value = scaleMax * index / 4;
    const y = padding.top + plotHeight - plotHeight * index / 4;
    return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="chart-grid-line"></line><text x="${padding.left - 10}" y="${y + 4}" text-anchor="end" class="chart-axis-label">${chartValueLabel(value, mode)}</text>`;
  }).join("");
  const labelStep = Math.max(1, Math.ceil((daily.length - 1) / 6));
  const labelIndexes = new Set(daily.map((_, index) => index).filter((index) => index % labelStep === 0));
  labelIndexes.add(daily.length - 1);
  const dateLabels = [...labelIndexes].map((index) => {
    const date = new Date(`${daily[index].date}T00:00:00Z`);
    return `<text x="${xAt(index)}" y="${height - 12}" text-anchor="middle" class="chart-axis-label">${date.getUTCMonth() + 1}/${date.getUTCDate()}</text>`;
  }).join("");
  const points = values.map((value, index) => `${xAt(index)},${yAt(value)}`).join(" ");
  const area = `${padding.left},${padding.top + plotHeight} ${points} ${width - padding.right},${padding.top + plotHeight}`;
  const pointMarkup = daily.length <= 30 ? values.map((value, index) => `<circle cx="${xAt(index)}" cy="${yAt(value)}" r="3" class="chart-point"><title>${daily[index].date} · ${chartValueLabel(value, mode)}</title></circle>`).join("") : "";
  svg.innerHTML = `${tickMarkup}<polygon points="${area}" class="chart-area"></polygon><polyline points="${points}" class="chart-line"></polyline>${pointMarkup}${dateLabels}`;
}

function renderModelRanking(items) {
  const target = document.getElementById("overview-ranking");
  if (!items.length) {
    target.innerHTML = '<div class="dashboard-empty"><i data-lucide="bar-chart-3"></i><span>该时段暂无用量</span></div>';
    portalIcons();
    return;
  }
  const maxAmount = Math.max(...items.map((item) => Number(item.amount_micros || 0)), 1);
  target.innerHTML = items.map((item, index) => `
    <div class="ranking-item">
      <span class="ranking-index">${index + 1}</span>
      <div class="ranking-content"><div><strong>${escapeHtml(item.model)}</strong><span>${formatMoney(item.amount_micros)}</span></div><div class="ranking-bar"><i style="width:${Math.max(4, Number(item.amount_micros || 0) / maxAmount * 100)}%"></i></div><small>${formatNumber(item.request_count)} 次请求 · ${formatNumber(item.total_tokens)} Token</small></div>
    </div>
  `).join("");
}

function renderActivityHeatmap(items) {
  const target = document.getElementById("activity-heatmap");
  const itemMap = new Map(items.map((item) => [item.date, item]));
  const today = new Date(`${portalState.dashboard.period.to}T00:00:00`);
  const firstDay = new Date(today);
  firstDay.setDate(today.getDate() - 364);
  const gridStart = new Date(firstDay);
  gridStart.setDate(firstDay.getDate() - firstDay.getDay());
  const dayCount = Math.round((today - gridStart) / 86400000) + 1;
  const weekCount = Math.ceil(dayCount / 7);
  const cellGap = 4;
  const weekdayWidth = 22;
  const availableWidth = target.parentElement?.clientWidth || 0;
  const cellSize = Math.max(11, Math.min(22, Math.floor((availableWidth - weekdayWidth - cellGap * (weekCount - 1) - 8) / weekCount)));
  const heatmapWidth = weekCount * cellSize + (weekCount - 1) * cellGap;
  const heatmapUnit = cellSize + cellGap;
  const maxRequests = Math.max(...items.map((item) => Number(item.request_count || 0)), 1);
  const cells = [];
  const months = [];
  let previousMonth = -1;
  for (let index = 0; index < weekCount * 7; index += 1) {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + index);
    const dateText = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const inRange = date >= firstDay && date <= today;
    const item = itemMap.get(dateText);
    const count = Number(item?.request_count || 0);
    const level = count ? Math.max(1, Math.ceil(count / maxRequests * 4)) : 0;
    if (date.getDay() === 0 && date.getMonth() !== previousMonth && inRange) {
      months.push(`<span style="left:${Math.floor(index / 7) * heatmapUnit}px">${date.getMonth() + 1}月</span>`);
      previousMonth = date.getMonth();
    }
    const title = inRange ? `${dateText} · ${formatNumber(count)} 次请求 · ${formatMoney(item?.amount_micros || 0)}` : "";
    cells.push(`<i class="${inRange ? `heat-cell level-${level}` : "heat-cell outside"}" title="${title}"></i>`);
  }
  target.innerHTML = `<div class="heatmap-months" style="width:${heatmapWidth}px">${months.join("")}</div><div class="heatmap-body"><div class="heatmap-weekdays" style="grid-template-rows:repeat(7, ${cellSize}px);line-height:${cellSize}px"><span>日</span><span></span><span>二</span><span></span><span>四</span><span></span><span>六</span></div><div class="heatmap-grid" style="grid-template-columns:repeat(${weekCount}, ${cellSize}px);grid-template-rows:repeat(7, ${cellSize}px);gap:${cellGap}px">${cells.join("")}</div></div>`;
}

function renderOverviewDashboard() {
  const dashboard = portalState.dashboard;
  const mode = portalState.overviewMode;
  const config = overviewModeConfig(mode);
  document.getElementById("overview-period-label").textContent = `${dashboard.period.from} 至 ${dashboard.period.to}`;
  document.getElementById("overview-trend-label").textContent = config.label;
  document.getElementById("overview-trend-value").textContent = config.format(config.value(dashboard.period));
  renderUsageChart(dashboard.daily, mode);
  renderModelRanking(dashboard.model_ranking);
  document.getElementById("activity-streak").textContent = `${formatNumber(dashboard.activity_summary.longest_streak_days)} 天`;
  document.getElementById("activity-today").textContent = formatMoney(dashboard.activity_summary.today_amount_micros);
  document.getElementById("activity-week").textContent = formatMoney(dashboard.activity_summary.week_amount_micros);
  document.getElementById("activity-total").textContent = formatMoney(dashboard.activity_summary.total_amount_micros);
  renderActivityHeatmap(dashboard.activity);
}

function keyUsageMarkup(item) {
  const spent = formatMoney(item.spent_micros);
  const limit = item.spending_limit_micros == null ? "不限" : formatMoney(item.spending_limit_micros);
  const ratio = item.spending_limit_micros ? Math.min(100, Number(item.spent_micros || 0) / item.spending_limit_micros * 100) : 0;
  return `<div class="key-usage"><strong>${escapeHtml(spent)}</strong><span>${escapeHtml(limit)}</span>${item.spending_limit_micros != null ? `<i><b style="width:${Math.max(2, ratio)}%"></b></i>` : ""}</div>`;
}

function keyMatchesStatus(item, status) {
  if (!status) return true;
  const expiry = keyExpiry(item);
  const expired = expiry && expiry <= new Date();
  if (status === "expired") return Boolean(expired);
  if (status === "active") return Boolean(item.active && !expired);
  return status === "inactive" && !item.active && !expired;
}

function renderKeyMetrics(items) {
  const active = items.filter((item) => item.active && !(keyExpiry(item) && keyExpiry(item) <= new Date())).length;
  const totalSpent = items.reduce((sum, item) => sum + Number(item.spent_micros || 0), 0);
  const limited = items.filter((item) => item.spending_limit_micros != null).length;
  const latest = items.map((item) => item.last_used_at).filter(Boolean).sort().at(-1);
  const balance = Number(portalState.profile?.balance_micros || 0);
  const hasCallableModel = portalState.models.some((item) => item.health_status !== "unavailable");
  renderMetrics("portal-keys-metrics", [
    { label: "账户余额", value: formatMoney(balance), meta: balance > 0 && hasCallableModel ? "可用于模型调用" : "充值或兑换后可调用", icon: "wallet-cards", color: balance > 0 ? "green" : "orange" },
    { label: "Key 总数", value: formatNumber(items.length), meta: `${formatNumber(active)} 个当前有效`, icon: "key-round" },
    { label: "累计用量", value: formatMoney(totalSpent), meta: `${formatNumber(limited)} 个设置了额度`, icon: "receipt-text", color: "blue" },
    { label: "最近使用", value: latest ? formatDate(latest) : "暂无", meta: "最近一次 API 调用", icon: "clock-3", color: "orange" },
    { label: "即将到期", value: formatNumber(items.filter((item) => keyExpiry(item) && keyExpiry(item) > new Date() && keyExpiry(item) <= new Date(Date.now() + 30 * 86400000)).length), meta: "未来 30 天", icon: "calendar-clock" },
  ]);
}

function applyKeyColumns() {
  document.querySelectorAll(".key-table [data-key-col]").forEach((element) => element.classList.toggle("key-col-hidden", !portalState.keyColumns[element.dataset.keyCol]));
  document.querySelectorAll(".key-table [data-key-cell]").forEach((element) => element.classList.toggle("key-col-hidden", !portalState.keyColumns[element.dataset.keyCell]));
}

function renderKeyTable() {
  const search = portalState.keyFilters.search.trim().toLowerCase();
  const status = portalState.keyFilters.status;
  const items = portalState.keys.filter((item) => {
    const text = `${item.name} ${item.key_prefix} ${item.project_name || "个人项目"}`.toLowerCase();
    return (!search || text.includes(search)) && keyMatchesStatus(item, status);
  });
  document.getElementById("key-result-count").textContent = `显示 ${items.length} 个 / 共 ${portalState.keys.length} 个 Key`;
  document.getElementById("portal-keys-table").innerHTML = items.length ? items.map((item) => `
    <tr>
      <td data-key-cell="name"><div class="primary-cell"><strong>${escapeHtml(item.name)}</strong><span class="secondary">ID ${item.id}</span></div></td>
      <td data-key-cell="key"><div class="key-prefix-cell"><code>${escapeHtml(item.key_prefix)}...</code><button class="icon-button compact-icon" data-copy-key-prefix="${escapeHtml(item.key_prefix)}" type="button" title="复制 Key 前缀" aria-label="复制 Key 前缀"><i data-lucide="copy"></i></button></div></td>
      <td data-key-cell="project"><div class="primary-cell"><strong>${escapeHtml(item.project_name || "个人项目")}</strong><span class="secondary">项目 ID ${item.project_id || "-"}</span></div></td>
      <td data-key-cell="usage">${keyUsageMarkup(item)}</td>
      <td data-key-cell="expires">${keyExpiry(item) ? formatDate(keyExpiry(item)) : "长期有效"}</td>
      <td data-key-cell="last-used">${formatDate(item.last_used_at)}</td>
      <td data-key-cell="status">${keyStatusBadge(item)}</td>
      <td data-key-cell="created">${formatDate(item.created_at)}</td>
      <td class="align-right"><div class="key-row-actions"><button class="icon-button compact-icon" data-key-detail="${item.id}" type="button" title="查看 Key 详情" aria-label="查看 Key 详情"><i data-lucide="arrow-up-right"></i></button>${item.active ? `<button class="icon-button compact-icon" data-rotate-key="${item.id}" type="button" title="轮换 Key" aria-label="轮换 Key"><i data-lucide="refresh-cw"></i></button>` : ""}<button class="icon-button compact-icon ${item.active ? "danger-icon" : "success-icon"}" data-toggle-key="${item.id}" data-active="${!item.active}" type="button" title="${item.active ? "停用 Key" : "启用 Key"}" aria-label="${item.active ? "停用 Key" : "启用 Key"}"><i data-lucide="${item.active ? "ban" : "check"}"></i></button></div></td>
    </tr>
  `).join("") : emptyRow(9);
  applyKeyColumns();
  portalIcons();
}

async function loadKeys() {
  const result = await portalApi("/portal/api-keys");
  portalState.keys = result.data;
  if (!portalState.profile) await loadProfile();
  renderKeyMetrics(result.data);
  document.getElementById("key-base-url").textContent = `${window.location.origin}/v1`;
  renderKeyTable();
  renderIntegrationGuide();
}

async function loadModels() {
  const [result, keys] = await Promise.all([
    portalApi("/portal/models"),
    portalApi("/portal/api-keys"),
  ]);
  portalState.models = result.data;
  portalState.keys = keys.data;
  portalState.marketplace.compare = portalState.marketplace.compare.filter((name) => portalState.models.some((item) => item.public_name === name));
  renderModelMarketplace();
}

const marketplaceFilters = [
  ["all", "全部", "layout-grid"], ["text", "文本", "message-square-text"], ["image", "图像", "image"], ["video", "视频", "video"], ["audio", "语音", "audio-lines"],
];

function modelApiType(item) {
  return item.api_type || "chat_completions";
}

function marketplaceModelCategory(item) {
  const type = modelApiType(item);
  const modalities = item.modalities || [];
  if (type === "images_generations") return "image";
  if (type === "video_generations") return "video";
  if (type.startsWith("audio_") || modalities.includes("audio")) return "audio";
  return "text";
}

function marketplaceModelTypeBadge(item) {
  const type = modelApiType(item);
  const modalities = item.modalities || [];
  if (type === "images_generations") return '<span class="badge warning">图像生成</span>';
  if (type === "video_generations") return '<span class="badge neutral">视频生成</span>';
  if (type.startsWith("audio_") || modalities.includes("audio")) return '<span class="badge neutral">语音模型</span>';
  return `<span class="badge success">${modalities.includes("image") ? "多模态对话" : "文本对话"}</span>`;
}

function modelProtocolLabel(item) {
  if (modelApiType(item) === "images_generations") return "图像生成接口";
  if (modelApiType(item) === "video_generations") return "视频异步任务接口";
  if (modelApiType(item) === "audio_speech") return "语音合成接口";
  if (modelApiType(item) === "audio_transcriptions") return "语音识别接口";
  return "对话接口（Chat Completions）";
}

function modelEndpoint(item) {
  if (modelApiType(item) === "images_generations") return `${window.location.origin}/v1/images/generations`;
  if (modelApiType(item) === "video_generations") return `${window.location.origin}/v1/videos/generations`;
  if (modelApiType(item) === "audio_speech") return `${window.location.origin}/v1/audio/speech`;
  if (modelApiType(item) === "audio_transcriptions") return `${window.location.origin}/v1/audio/transcriptions`;
  return `${window.location.origin}/v1/chat/completions`;
}

function taskPriceLabel(item) {
  if (modelApiType(item) === "video_generations") return "每次视频生成";
  if (modelApiType(item) === "audio_speech") return "每次语音合成";
  if (modelApiType(item) === "audio_transcriptions") return "每次语音识别";
  return "每张图片生成";
}

function modelPriceValue(item) {
  if (modelApiType(item) !== "chat_completions") return Number(item.task_price_micros || 0);
  return Number(item.input_price_micros_per_1k || 0) * 1000;
}

function modelContextTokens(item) {
  const value = String(item.context_window || "").trim().toUpperCase();
  const matched = value.match(/^([\d.]+)\s*([KM])$/);
  if (!matched) return 0;
  return Number(matched[1]) * (matched[2] === "M" ? 1_000_000 : 1_000);
}

function modelVersionLabel(item) {
  return item.model_version || "当前版本";
}

function modelMaxOutputLabel(item) {
  return modelApiType(item) === "chat_completions" ? formatMaxOutputTokens(item.max_output_tokens) : "按任务配置";
}

function modelIcon(item) {
  if (modelApiType(item) === "video_generations") return "clapperboard";
  if (modelApiType(item) === "images_generations") return "image-plus";
  if (modelApiType(item).startsWith("audio_")) return "audio-lines";
  if ((item.modalities || []).includes("image")) return "image";
  if ((item.modalities || []).includes("reasoning")) return "brain-circuit";
  if ((item.modalities || []).includes("code")) return "code-2";
  return item.builtin ? "sparkles" : "boxes";
}

function portalProviderDescription(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  if (name.includes("deepseek")) return "面向高强度推理与复杂应用的通用大模型系列，适合研发、开发与智能体场景。";
  if (name.includes("qwen")) return "通义大模型体系，覆盖文本、多模态与视觉能力，适合企业级 AI 应用落地。";
  if (name.includes("智谱") || name.includes("zhipu") || name.includes("glm")) return "GLM 通用大模型系列，覆盖中文理解、生成、对话与代码等企业应用能力。";
  if (name.includes("kimi")) return "Moonshot AI 通用大模型体系，聚焦长上下文理解、高质量推理与知识整合。";
  if (name.includes("minimax")) return "MiniMax 文本、多模态与内容生成模型，适合 Agent、创作与交互场景。";
  if (name.includes("doubao") || name.includes("豆包") || name.includes("字节")) return "字节跳动模型服务，覆盖文本、视觉、图像与视频生成能力。";
  return "来自生态合作伙伴的模型系列，按统一接口接入并由平台集中管理。";
}

function portalProviderLogo(provider, className = "provider-logo-image") {
  const name = String(provider || "").toLocaleLowerCase();
  const slug = name.includes("deepseek") ? "deepseek" : name.includes("qwen") || name.includes("通义") ? "qwen" : name.includes("智谱") || name.includes("zhipu") || name.includes("glm") ? "glm-local" : name.includes("kimi") ? "kimi-local" : name.includes("moonshot") ? "moonshotai" : name.includes("minimax") ? "minimax" : name.includes("doubao") || name.includes("豆包") ? "doubao-local" : name.includes("字节") ? "bytedance" : "";
  const color = { deepseek: "4D6BFE", qwen: "6155F5", moonshotai: "4C8BF5", minimax: "FF5B7F", bytedance: "2A5CAA" }[slug] || "59636D";
  const source = { "glm-local": "/static/provider-logos/glm.png", "kimi-local": "/static/provider-logos/kimi.ico", "doubao-local": "/static/provider-logos/doubao.png" }[slug] || `https://cdn.simpleicons.org/${slug}/${color}`;
  const initial = escapeHtml(String(provider || "自定义").slice(0, 1).toUpperCase());
  return slug ? `<img class="${className}" src="${source}" alt="${escapeHtml(provider)} Logo" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span hidden>${initial}</span>` : initial;
}

const marketplaceProviderOrder = ["deepseek", "qwen", "glm", "zhipu", "智谱", "kimi", "minimax", "doubao", "字节"];
function marketplaceProviderRank(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  const index = marketplaceProviderOrder.findIndex((item) => name.includes(item));
  return index < 0 ? Number.MAX_SAFE_INTEGER : index;
}

function matchesMarketplaceModelWithoutModality(item) {
  const query = portalState.marketplace.query.trim().toLocaleLowerCase();
  const terms = [item.public_name, item.display_name, item.provider, item.model_version, item.summary, ...(item.capabilities || [])].join(" ").toLocaleLowerCase();
  const matchesHealth = portalState.marketplace.health === "all" || (portalState.marketplace.health === "healthy" ? item.health_status === "healthy" : item.health_status !== "healthy");
  const matchesProvider = !portalState.marketplace.providerFilter || (item.provider || "第三方模型") === portalState.marketplace.providerFilter;
  return (!query || terms.includes(query)) && matchesHealth && matchesProvider;
}

function matchesMarketplaceModel(item) {
  return matchesMarketplaceModelWithoutModality(item)
    && (portalState.marketplace.modality === "all" || marketplaceModelCategory(item) === portalState.marketplace.modality);
}

function sortMarketplaceModels(items) {
  const sorted = [...items];
  if (portalState.marketplace.sort === "price-asc") sorted.sort((a, b) => modelPriceValue(a) - modelPriceValue(b) || String(a.public_name).localeCompare(String(b.public_name)));
  if (portalState.marketplace.sort === "context-desc") sorted.sort((a, b) => modelContextTokens(b) - modelContextTokens(a) || String(a.public_name).localeCompare(String(b.public_name)));
  if (portalState.marketplace.sort === "name-asc") sorted.sort((a, b) => String(a.display_name || a.public_name).localeCompare(String(b.display_name || b.public_name)));
  return sorted;
}

const featuredProviderNames = ["deepseek", "qwen", "智谱", "zhipu", "glm", "kimi", "minimax", "doubao", "字节"];

function isFeaturedProvider(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  return featuredProviderNames.some((candidate) => name.includes(candidate));
}

function marketplaceProviderTypeCounts(items) {
  const labels = { text: "文本", image: "图像", video: "视频", audio: "语音" };
  return [...new Set(items.map(marketplaceModelCategory))].map((type) => `${labels[type]} ${items.filter((item) => marketplaceModelCategory(item) === type).length}`).join(" · ");
}

function populateMarketplaceProviderFilter() {
  const select = document.getElementById("model-marketplace-provider-filter");
  const providers = [...new Set(portalState.models.map((item) => item.provider || "第三方模型"))].sort((a, b) => marketplaceProviderRank(a) - marketplaceProviderRank(b) || a.localeCompare(b));
  select.innerHTML = '<option value="">全部服务商</option>' + providers.map((provider) => `<option value="${escapeHtml(provider)}">${escapeHtml(provider)}</option>`).join("");
  select.value = portalState.marketplace.providerFilter;
}

function renderMarketplaceModelCard(item) {
  const selected = portalState.marketplace.compare.includes(item.public_name);
  const parameters = (item.supported_parameters || []).slice(0, 3).join(" · ");
  const capabilities = (item.capabilities || []).slice(0, 4).map((capability) => `<span>${escapeHtml(capability)}</span>`).join("");
  const chatCompatible = modelApiType(item) === "chat_completions";
  return `<article class="admin-model-card portal-model-card ${selected ? "is-compared" : ""}">
    <div class="admin-model-card-header"><span class="admin-model-icon">${portalProviderLogo(item.provider || "第三方模型", "admin-model-logo-image")}</span><div class="admin-model-card-title"><div><h3>${escapeHtml(item.display_name || item.public_name)}</h3><div class="admin-model-version"><span>模型版本</span><strong>${escapeHtml(modelVersionLabel(item))}</strong></div><code>调用 ID：${escapeHtml(item.public_name)}</code></div><span class="badge success">可调用</span></div></div>
    <div class="admin-model-card-meta">${marketplaceModelTypeBadge(item)}<div class="admin-model-tags inline">${capabilities || '<span class="empty">能力待补充</span>'}</div><span class="admin-model-health"><i data-lucide="activity"></i>${formatNumber(item.healthy_channel_count)} / ${formatNumber(item.active_channel_count)} 健康</span></div>
    <div class="admin-model-card-stats"><div><span>上下文</span><strong>${escapeHtml(item.context_window || "按任务配置")}</strong></div><div><span>最大输出</span><strong>${escapeHtml(modelMaxOutputLabel(item))}</strong></div><div><span>参数</span><strong title="${escapeHtml(parameters)}">${escapeHtml(parameters || "按任务协议")}</strong></div></div>
    <div class="admin-model-card-pricing">${chatCompatible ? `<div><span>平台输入 / 1M</span><strong>${formatTokenPricePerMillion(item.input_price_micros_per_1k)}</strong></div><div><span>平台输出 / 1M</span><strong>${formatTokenPricePerMillion(item.output_price_micros_per_1k)}</strong></div>` : `<div><span>${taskPriceLabel(item)}</span><strong>${formatMoney(item.task_price_micros || 0)}</strong></div><div><span>调用方式</span><strong>${escapeHtml(modelProtocolLabel(item))}</strong></div>`}</div>
    <div class="admin-model-card-footer"><button class="text-button" type="button" data-model-detail="${escapeHtml(item.public_name)}"><i data-lucide="file-text"></i><span>完整参数</span></button><div class="admin-model-actions"><button class="table-button" type="button" data-model-test="${escapeHtml(item.public_name)}"><i data-lucide="play"></i><span>测试调用</span></button><button class="table-button" type="button" data-copy-model="${escapeHtml(item.public_name)}"><i data-lucide="copy"></i><span>复制模型 ID</span></button><button class="table-button" type="button" data-model-onboard="${escapeHtml(item.public_name)}"><i data-lucide="plug-zap"></i><span>开始接入</span></button></div></div>
  </article>`;
}

function renderModelCompareDock() {
  const target = document.getElementById("model-compare-dock");
  const selected = portalState.marketplace.compare.map((name) => portalState.models.find((item) => item.public_name === name)).filter(Boolean);
  target.hidden = !selected.length;
  if (!selected.length) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = `<div><span>模型对比</span><div class="model-compare-chips">${selected.map((item) => `<span>${escapeHtml(item.display_name || item.public_name)}<button type="button" data-model-compare="${escapeHtml(item.public_name)}" title="移出对比" aria-label="将 ${escapeHtml(item.display_name || item.public_name)} 移出对比"><i data-lucide="x"></i></button></span>`).join("")}</div><small>${selected.length} / 3，至少选择两个模型</small></div><div><button class="secondary-button" type="button" data-model-compare-clear><i data-lucide="trash-2"></i><span>清空</span></button><button class="primary-button" type="button" data-model-compare-open ${selected.length < 2 ? "disabled" : ""}><i data-lucide="columns-2"></i><span>开始对比</span></button></div>`;
}

function renderModelMarketplace() {
  const visibleModels = sortMarketplaceModels(portalState.models.filter(matchesMarketplaceModel));
  const filteredModels = portalState.models.filter(matchesMarketplaceModelWithoutModality);
  const tabContainer = document.getElementById("model-marketplace-tabs");
  tabContainer.innerHTML = marketplaceFilters.map(([value, label, icon]) => {
    const count = value === "all" ? filteredModels.length : filteredModels.filter((item) => marketplaceModelCategory(item) === value).length;
    return `<button type="button" class="${portalState.marketplace.modality === value ? "active" : ""}" data-model-filter="${value}" aria-pressed="${portalState.marketplace.modality === value}"><i data-lucide="${icon}"></i><span>${label}</span><small>${count}</small></button>`;
  }).join("");
  populateMarketplaceProviderFilter();
  const providerGrid = document.getElementById("portal-provider-grid");
  const providerOverview = document.getElementById("portal-provider-overview");
  const modelList = document.getElementById("portal-model-list");
  const container = document.getElementById("portal-models-table");
  const providerBack = document.getElementById("model-marketplace-provider-back");
  if (portalState.marketplace.provider) {
    const detailModels = portalState.marketplace.provider === "__more__" ? visibleModels.filter((item) => !isFeaturedProvider(item.provider)) : visibleModels.filter((item) => (item.provider || "第三方模型") === portalState.marketplace.provider);
    const providerName = portalState.marketplace.provider === "__more__" ? "更多系列 / 厂商查询" : portalState.marketplace.provider;
    document.getElementById("model-marketplace-count").textContent = `${providerName} · ${detailModels.length} 个模型`;
    providerBack.hidden = false;
    providerGrid.hidden = true;
    providerOverview.hidden = false;
    modelList.hidden = false;
    providerOverview.innerHTML = `<div class="model-provider-overview-main"><div class="model-provider-overview-title"><span class="admin-provider-logo">${portalState.marketplace.provider === "__more__" ? '<i data-lucide="search"></i>' : portalProviderLogo(providerName)}</span><div><span class="eyebrow">MODEL CATALOG</span><h3>${escapeHtml(providerName)}</h3><p>${escapeHtml(portalState.marketplace.provider === "__more__" ? "浏览其他第三方及新接入的可调用模型。" : portalProviderDescription(providerName))}</p></div></div><div class="model-provider-overview-actions"><button class="primary-button compact-provider-button" type="button" data-model-compare-provider="${escapeHtml(providerName)}"><i data-lucide="columns-2"></i><span>模型对比</span></button><button class="primary-button compact-provider-button" type="button" data-go="keys"><i data-lucide="key-round"></i><span>密钥管理</span></button></div></div>`;
    container.innerHTML = detailModels.length ? `<div class="admin-model-card-grid">${detailModels.map(renderMarketplaceModelCard).join("")}</div>` : '<div class="model-catalog-empty"><i data-lucide="search-x"></i><strong>没有符合筛选条件的模型</strong><span>调整搜索、类型或状态筛选后重试</span><button class="secondary-button" type="button" data-model-filter-reset><i data-lucide="rotate-ccw"></i><span>重置筛选</span></button></div>';
  } else {
    const providerMap = new Map();
    visibleModels.forEach((item) => {
      const provider = item.provider || "第三方模型";
      if (!providerMap.has(provider)) providerMap.set(provider, []);
      providerMap.get(provider).push(item);
    });
    const providers = [...providerMap.values()];
    const featured = providers.filter((items) => isFeaturedProvider(items[0].provider)).sort((a, b) => marketplaceProviderRank(a[0].provider) - marketplaceProviderRank(b[0].provider));
    const otherModels = providers.filter((items) => !isFeaturedProvider(items[0].provider)).flat();
    document.getElementById("model-marketplace-count").textContent = `${providers.length} 家供应商 · ${visibleModels.length} 个模型`;
    providerBack.hidden = true;
    providerGrid.hidden = false;
    providerOverview.hidden = true;
    modelList.hidden = true;
    providerGrid.innerHTML = featured.length || otherModels.length ? featured.map((items, index) => {
      const provider = items[0].provider || "第三方模型";
      const chips = items.slice(0, 3).map((item) => `<span>${escapeHtml(item.display_name || item.public_name)}</span>`).join("");
      const hiddenModelCount = Math.max(0, items.length - 3);
      const moreChip = hiddenModelCount ? `<span class="provider-model-chip-more">另有 ${hiddenModelCount} 个</span>` : "";
      const healthyChannels = items.reduce((sum, item) => sum + Number(item.healthy_channel_count || 0), 0);
      const activeChannels = items.reduce((sum, item) => sum + Number(item.active_channel_count || 0), 0);
      return `<article class="admin-provider-card tone-${index % 5}"><span class="admin-provider-logo">${portalProviderLogo(provider)}</span><span class="admin-provider-copy"><span class="provider-card-title"><strong>${escapeHtml(provider)}</strong><span class="badge success">${items.length} 个可调用</span></span><p>${escapeHtml(portalProviderDescription(provider))}</p><span class="provider-model-chips">${chips}${moreChip}</span><small>${items.length} 个模型 · ${marketplaceProviderTypeCounts(items)} · ${healthyChannels}/${activeChannels} 渠道健康</small><span class="provider-card-actions"><button class="table-button" type="button" data-model-provider="${escapeHtml(provider)}"><i data-lucide="list"></i><span>查看系列</span></button><button class="primary-button compact-provider-button" type="button" data-go="keys"><i data-lucide="key-round"></i><span>密钥管理</span></button></span></span></article>`;
    }).join("") + (otherModels.length ? `<button class="admin-provider-card provider-more-card" type="button" data-model-provider-more><span class="admin-provider-more-icon"><i data-lucide="search"></i></span><span class="admin-provider-copy"><strong>更多系列 / 厂商查询</strong><p>${otherModels.length} 个其他第三方及新增模型</p><b>进入查询 <i data-lucide="arrow-right"></i></b></span></button>` : "") : '<div class="empty-state compact"><i data-lucide="boxes"></i><span>没有符合筛选条件的供应商</span><button class="secondary-button" type="button" data-model-filter-reset><i data-lucide="rotate-ccw"></i><span>重置筛选</span></button></div>';
  }
  renderModelCompareDock();
  portalIcons();
}

function modelCurlSnippet(item) {
  if (modelApiType(item) === "audio_speech") return `curl ${window.location.origin}/v1/audio/speech -H "Authorization: Bearer YOUR_API_KEY" -H "Content-Type: application/json" -d '{"model":"${item.public_name}","input":"你好，欢迎使用 LokToken","voice":"alloy"}'`;
  if (modelApiType(item) === "audio_transcriptions") return `curl ${window.location.origin}/v1/audio/transcriptions -H "Authorization: Bearer YOUR_API_KEY" -H "Content-Type: application/json" -d '{"model":"${item.public_name}","audio":"BASE64_AUDIO"}'`;
  if (modelApiType(item) === "images_generations") return `curl ${window.location.origin}/v1/images/generations \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${item.public_name}","prompt":"一幅现代城市夜景"}'`;
  if (modelApiType(item) === "video_generations") return `curl ${window.location.origin}/v1/videos/generations \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${item.public_name}","prompt":"海边日出，电影感镜头"}'`;
  return `curl ${window.location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer YOUR_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${item.public_name}","messages":[{"role":"user","content":"你好"}]}'`;
}

function modelPythonSnippet(item) {
  if (modelApiType(item) === "audio_speech") return `import requests\n\nresponse = requests.post("${window.location.origin}/v1/audio/speech", headers={"Authorization": "Bearer YOUR_API_KEY"}, json={"model": "${item.public_name}", "input": "你好，欢迎使用 LokToken", "voice": "alloy"})\nprint(response.json()["data"][0]["url"])`;
  if (modelApiType(item) === "audio_transcriptions") return `import requests\n\nresponse = requests.post("${window.location.origin}/v1/audio/transcriptions", headers={"Authorization": "Bearer YOUR_API_KEY"}, json={"model": "${item.public_name}", "audio": "BASE64_AUDIO"})\nprint(response.json()["data"][0]["text"])`;
  if (modelApiType(item) === "images_generations") return `import requests\n\nresponse = requests.post(\n    "${window.location.origin}/v1/images/generations",\n    headers={"Authorization": "Bearer YOUR_API_KEY"},\n    json={"model": "${item.public_name}", "prompt": "一幅现代城市夜景"},\n)\nprint(response.json()["data"][0]["url"])`;
  if (modelApiType(item) === "video_generations") return `import requests\n\ncreated = requests.post(\n    "${window.location.origin}/v1/videos/generations",\n    headers={"Authorization": "Bearer YOUR_API_KEY"},\n    json={"model": "${item.public_name}", "prompt": "海边日出，电影感镜头"},\n).json()\ntask = requests.get(\n    "${window.location.origin}/v1/generation-tasks/" + created["id"],\n    headers={"Authorization": "Bearer YOUR_API_KEY"},\n).json()\nprint(task["status"], task["data"])`;
  return `from openai import OpenAI\n\nclient = OpenAI(\n    base_url="${window.location.origin}/v1",\n    api_key="YOUR_API_KEY",\n)\n\nresponse = client.chat.completions.create(\n    model="${item.public_name}",\n    messages=[{"role": "user", "content": "你好"}],\n)\nprint(response.choices[0].message.content)`;
}

function modelTestDialog(modelName) {
  const item = portalState.models.find((model) => model.public_name === modelName);
  if (!item) return;
  const apiType = modelApiType(item);
  if (!["chat_completions", "images_generations", "video_generations", "audio_speech", "audio_transcriptions"].includes(apiType)) {
    portalToast("当前模型的统一调用适配器尚未启用", true);
    return;
  }
  const keys = portalState.keys.filter((key) => key.active && (!keyExpiry(key) || keyExpiry(key) > new Date()));
  if (!keys.length) {
    openPortalDialog("测试调用 · 需要 API Key", `<div class="dialog-body"><div class="model-test-warning"><i data-lucide="key-round"></i><div><strong>请先创建一个有效的 API Key</strong><span>测试调用会消耗账户额度，并写入请求记录。创建完成后再从模型详情进入测试。</span></div></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>稍后处理</button><button class="primary-button" type="button" data-action="create-key"><i data-lucide="key-round"></i><span>创建 API Key</span></button></div>`);
    return;
  }
  const promptLabel = apiType === "audio_speech" ? "合成文本" : "测试提示词";
  const audioField = apiType === "audio_transcriptions" ? `<div class="field"><label for="model-test-audio">音频内容（Base64 或 URL）</label><textarea id="model-test-audio" name="audio" rows="3" maxlength="16000000" required placeholder="粘贴音频 Base64 或供应商可访问的音频 URL"></textarea></div>` : "";
  openPortalDialog(`测试调用 · ${item.display_name || item.public_name}`, `<form id="model-test-form"><div class="dialog-body"><div class="model-test-warning"><i data-lucide="triangle-alert"></i><div><strong>本次测试会消耗额度</strong><span>请求将使用所选 API Key，并按模型当前平台价格计费，结果会写入请求记录。</span></div></div><div class="field"><label for="model-test-key">API Key</label><select id="model-test-key" name="api_key_id" required>${keys.map((key) => `<option value="${key.id}">${escapeHtml(key.name)} · ${escapeHtml(key.key_prefix)}...</option>`).join("")}</select></div><div class="field"><label for="model-test-prompt">${promptLabel}</label><textarea id="model-test-prompt" name="prompt" rows="4" maxlength="2000" ${apiType === "audio_transcriptions" ? "" : "required"} placeholder="${apiType === "audio_speech" ? "例如：欢迎使用 LokToken" : "例如：用一句话介绍这个模型适合什么场景"}"></textarea><small class="field-hint">最多 2,000 个字符，建议使用简短内容。</small></div>${audioField}<div class="field"><label for="model-test-max-tokens">最大输出 Token</label><input id="model-test-max-tokens" name="max_tokens" type="number" min="32" max="4096" step="1" value="256"></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="play"></i><span>开始测试</span></button></div></form>`);
  document.getElementById("model-test-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(event.currentTarget));
      const result = await portalApi("/portal/model-tests", { method: "POST", body: JSON.stringify({ model: item.public_name, api_key_id: Number(data.api_key_id), prompt: data.prompt || "", audio: data.audio || null, max_tokens: Number(data.max_tokens || 256) }) });
      const choice = result.response?.choices?.[0];
      const output = choice?.message?.content || choice?.text || JSON.stringify(result.response || {}, null, 2);
      openPortalDialog("测试调用结果", `<div class="dialog-body"><div class="model-test-success"><i data-lucide="circle-check"></i><div><strong>调用成功</strong><span>请求已完成并按实际用量结算。</span></div></div><div class="model-test-output"><pre>${escapeHtml(String(output))}</pre></div><div class="key-detail-grid"><div><span>输入 Token</span><strong>${formatNumber(result.input_tokens)}</strong></div><div><span>输出 Token</span><strong>${formatNumber(result.output_tokens)}</strong></div><div><span>本次扣费</span><strong>${formatMoney(result.amount_micros)}</strong></div></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
      await loadKeys();
      await loadProfile();
    } catch (error) {
      if (submit) submit.disabled = false;
      portalToast(error.message, true);
    }
  });
}

function usablePortalKeys() {
  return portalState.keys.filter((key) => key.active && (!keyExpiry(key) || keyExpiry(key) > new Date()));
}

function modelOnboardingDialog(modelName, createdKey = "") {
  const item = portalState.models.find((model) => model.public_name === modelName);
  if (!item) return;
  const keys = usablePortalKeys();
  const endpoint = modelEndpoint(item);
  const keyMarkup = createdKey
    ? `<div class="model-onboarding-secret"><div class="model-onboarding-secret-head"><strong>新建 API Key</strong><span>完整密钥只展示这一次，请立即保存。</span></div><div class="secret-box mono" id="model-onboarding-secret">${escapeHtml(createdKey)}</div><button class="secondary-button" type="button" data-copy-onboarding-key="${escapeHtml(createdKey)}"><i data-lucide="copy"></i><span>复制完整 Key</span></button></div>`
    : keys.length
      ? `<div class="field"><label for="model-onboarding-key">使用 API Key</label><select id="model-onboarding-key">${keys.map((key) => `<option value="${key.id}">${escapeHtml(key.name)} · ${escapeHtml(key.key_prefix)}...</option>`).join("")}</select><small class="field-hint">已创建的完整 Key 不会再次显示，请从你的密钥管理系统中读取。</small></div>`
      : `<div class="model-onboarding-warning"><i data-lucide="key-round"></i><div><strong>还没有可用的 API Key</strong><span>创建后即可把下面的接入信息配置到应用中。API Key 只在创建成功时完整展示一次。</span></div></div>`;
  openPortalDialog(`开始接入 · ${item.display_name || item.public_name}`, `<div class="dialog-body model-onboarding-body">
    <div class="model-onboarding-summary"><div class="model-card-icon">${portalProviderLogo(item.provider || "第三方模型", "model-detail-logo-image")}</div><div><span class="eyebrow">MODEL ONBOARDING</span><h3>${escapeHtml(item.display_name || item.public_name)}</h3><p>将 API Key、统一端点和模型 ID 配置到你的应用中，即可开始调用。</p></div></div>
    <section class="model-onboarding-step"><div class="model-onboarding-step-title"><span>1</span><div><strong>准备 API Key</strong><small>用于鉴权并记录调用用量</small></div></div>${keyMarkup}</section>
    <section class="model-onboarding-step"><div class="model-onboarding-step-title"><span>2</span><div><strong>配置调用信息</strong><small>以下信息适用于当前模型</small></div></div><div class="model-onboarding-grid"><div class="model-endpoint-row"><div><span>统一端点</span><code>${escapeHtml(endpoint)}</code></div><button class="icon-button compact-icon" type="button" data-copy-endpoint="${escapeHtml(endpoint)}" title="复制统一端点" aria-label="复制统一端点"><i data-lucide="copy"></i></button></div><div class="model-endpoint-row"><div><span>模型 ID</span><code>${escapeHtml(item.public_name)}</code></div><button class="icon-button compact-icon" type="button" data-copy-model="${escapeHtml(item.public_name)}" title="复制模型 ID" aria-label="复制模型 ID"><i data-lucide="copy"></i></button></div></div></section>
    <section class="model-onboarding-step"><div class="model-onboarding-step-title"><span>3</span><div><strong>复制示例代码</strong><small>${escapeHtml(modelProtocolLabel(item))}</small></div></div><section class="model-code-section"><div class="model-code-heading"><div><strong>cURL</strong><span>适合快速验证</span></div><button class="icon-button compact-icon" type="button" data-copy-model-code="curl" data-model-name="${escapeHtml(item.public_name)}" title="复制 cURL 示例" aria-label="复制 cURL 示例"><i data-lucide="copy"></i></button></div><pre><code>${escapeHtml(modelCurlSnippet(item))}</code></pre></section><section class="model-code-section"><div class="model-code-heading"><div><strong>Python</strong><span>${modelApiType(item) === "chat_completions" ? "使用 OpenAI SDK" : "使用 HTTP 请求"}</span></div><button class="icon-button compact-icon" type="button" data-copy-model-code="python" data-model-name="${escapeHtml(item.public_name)}" title="复制 Python 示例" aria-label="复制 Python 示例"><i data-lucide="copy"></i></button></div><pre><code>${escapeHtml(modelPythonSnippet(item))}</code></pre></section></section>
  </div><div class="dialog-actions">${keys.length || createdKey ? `<button class="secondary-button" type="button" data-go="keys"><i data-lucide="key-round"></i><span>管理 API Key</span></button>` : `<button class="primary-button" type="button" data-action="model-onboarding-create-key" data-model-name="${escapeHtml(item.public_name)}"><i data-lucide="key-round"></i><span>先创建 API Key</span></button>`}<button class="${keys.length || createdKey ? "primary-button" : "secondary-button"} " type="button" data-close>完成</button></div>`);
}

function modelDetailDialog(modelName) {
  const item = portalState.models.find((model) => model.public_name === modelName);
  if (!item) return;
  const limit = item.rate_limit || {};
  openPortalDialog(`模型详情 · ${item.display_name || item.public_name}`, `<div class="dialog-body model-detail-body">
    <div class="model-detail-hero"><div class="model-card-icon">${portalProviderLogo(item.provider || "第三方模型", "model-detail-logo-image")}</div><div><div class="model-card-title"><h3>${escapeHtml(item.display_name || item.public_name)}</h3>${item.builtin ? '<span class="badge success">内置</span>' : ""}</div><p>${escapeHtml(item.summary)}</p></div></div>
    <div class="model-detail-tags">${(item.capabilities || []).map((capability) => `<span>${escapeHtml(capability)}</span>`).join("")}</div>
    <div class="model-endpoint-row"><div><span>统一调用地址</span><code>${escapeHtml(modelEndpoint(item))}</code></div><button class="icon-button compact-icon" type="button" data-copy-endpoint="${escapeHtml(modelEndpoint(item))}" title="复制调用地址" aria-label="复制调用地址"><i data-lucide="copy"></i></button></div>
    <div class="model-detail-grid"><div><span>模型 ID</span><strong class="mono">${escapeHtml(item.public_name)}</strong></div><div><span>模型版本</span><strong>${escapeHtml(modelVersionLabel(item))}</strong></div><div><span>渠道状态</span><strong>${modelHealth(item)} <small>${item.healthy_channel_count || 0} / ${item.active_channel_count || 0} 健康</small></strong></div><div><span>调用协议</span><strong>${escapeHtml(modelProtocolLabel(item))}</strong></div><div><span>上下文</span><strong>${escapeHtml(item.context_window || "按任务配置")}</strong></div><div><span>最大输出</span><strong>${escapeHtml(modelMaxOutputLabel(item))}</strong></div><div><span>调用频率</span><strong>${formatNumber(limit.requests || 0)} 次 / ${formatNumber(limit.window_seconds || 60)} 秒</strong></div>${modelApiType(item) === "chat_completions" ? `<div><span>输入价格 / 1M Token</span><strong>${formatTokenPricePerMillion(item.input_price_micros_per_1k)}</strong></div><div><span>输出价格 / 1M Token</span><strong>${formatTokenPricePerMillion(item.output_price_micros_per_1k)}</strong></div>` : `<div><span>${taskPriceLabel(item)}</span><strong>${formatMoney(item.task_price_micros || 0)}</strong></div>`}<div class="model-parameter-cell"><span>支持参数</span><strong>${escapeHtml((item.supported_parameters || []).join(" · ") || "按任务协议")}</strong></div></div>
    <p class="model-pricing-note">平台价格由管理控制台根据官方价格和目标利润率统一计算，调整后仅对新请求生效。</p>
    <section class="model-code-section"><div class="model-code-heading"><div><strong>cURL 调用</strong><span>${escapeHtml(modelProtocolLabel(item))}</span></div><button class="icon-button compact-icon" type="button" data-copy-model-code="curl" data-model-name="${escapeHtml(item.public_name)}" title="复制 cURL 示例" aria-label="复制 cURL 示例"><i data-lucide="copy"></i></button></div><pre><code>${escapeHtml(modelCurlSnippet(item))}</code></pre></section>
    <section class="model-code-section"><div class="model-code-heading"><div><strong>Python 调用</strong><span>${modelApiType(item) === "chat_completions" ? "使用 OpenAI SDK" : "使用 HTTP 请求"}</span></div><button class="icon-button compact-icon" type="button" data-copy-model-code="python" data-model-name="${escapeHtml(item.public_name)}" title="复制 Python 示例" aria-label="复制 Python 示例"><i data-lucide="copy"></i></button></div><pre><code>${escapeHtml(modelPythonSnippet(item))}</code></pre></section>
  </div><div class="dialog-actions">${["chat_completions", "images_generations", "video_generations", "audio_speech", "audio_transcriptions"].includes(modelApiType(item)) ? `<button class="secondary-button" type="button" data-model-test="${escapeHtml(item.public_name)}"><i data-lucide="play"></i><span>测试调用</span></button>` : ""}<button class="secondary-button" type="button" data-copy-model="${escapeHtml(item.public_name)}"><i data-lucide="copy"></i><span>复制模型 ID</span></button><button class="primary-button" type="button" data-model-onboard="${escapeHtml(item.public_name)}"><i data-lucide="plug-zap"></i><span>开始接入</span></button></div>`);
}

function modelComparisonPrice(item) {
  if (modelApiType(item) !== "chat_completions") return `${taskPriceLabel(item)} ${formatMoney(item.task_price_micros || 0)}`;
  return `输入 ${formatTokenPricePerMillion(item.input_price_micros_per_1k)} / 输出 ${formatTokenPricePerMillion(item.output_price_micros_per_1k)}`;
}

function modelCompareDialog() {
  const items = portalState.marketplace.compare.map((name) => portalState.models.find((item) => item.public_name === name)).filter(Boolean);
  if (items.length < 2) {
    portalToast("请至少选择两个模型进行对比", true);
    return;
  }
  const rows = [
    ["供应商", (item) => item.provider || "第三方模型"],
    ["模型版本", modelVersionLabel],
    ["能力", (item) => (item.capabilities || []).join(" · ") || "-"],
    ["调用协议", modelProtocolLabel],
    ["上下文", (item) => item.context_window || "按任务配置"],
    ["最大输出", modelMaxOutputLabel],
    ["平台定价", modelComparisonPrice],
    ["渠道状态", (item) => item.health_status === "healthy" ? "渠道健康" : item.health_status === "degraded" ? "部分异常" : item.health_status === "checking" ? "待检测" : "暂不可用"],
  ];
  openPortalDialog("模型对比", `<div class="dialog-body model-compare-body"><p class="dialog-copy">对比信息来自管理控制台当前已发布配置，实际费用按调用用量或任务次数结算。</p><div class="model-compare-table-wrap"><table class="model-compare-table"><thead><tr><th>对比项</th>${items.map((item) => `<th><span>${escapeHtml(item.provider || "第三方模型")}</span><strong>${escapeHtml(item.display_name || item.public_name)}</strong><code>${escapeHtml(item.public_name)}</code></th>`).join("")}</tr></thead><tbody>${rows.map(([label, value]) => `<tr><th>${escapeHtml(label)}</th>${items.map((item) => `<td>${escapeHtml(value(item))}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button><button class="primary-button" type="button" data-action="model-create-key"><i data-lucide="key-round"></i><span>密钥管理</span></button></div>`);
}

function comparisonCandidates(providerName) {
  return providerName === "更多系列 / 厂商查询"
    ? portalState.models.filter((item) => !isFeaturedProvider(item.provider))
    : portalState.models.filter((item) => (item.provider || "第三方模型") === providerName);
}

function compareProviderModels(providerName) {
  const items = comparisonCandidates(providerName);
  if (items.length < 2) {
    portalToast("当前供应商至少需要两个可用模型才能对比", true);
    return;
  }
  const selected = new Set(portalState.marketplace.compare.filter((name) => items.some((item) => item.public_name === name)));
  openPortalDialog("选择模型对比", `<form id="model-compare-select-form"><div class="dialog-body model-compare-select-body"><p class="dialog-copy">请选择 2～3 个模型进行对比，模型名称较长时会自动换行显示。</p><div class="model-compare-select-list">${items.map((item) => `<label class="model-compare-select-item"><input type="checkbox" name="model" value="${escapeHtml(item.public_name)}" ${selected.has(item.public_name) ? "checked" : ""}><span><strong>${escapeHtml(item.display_name || item.public_name)}</strong><small>${escapeHtml(item.public_name)} · ${escapeHtml(modelVersionLabel(item))}</small></span></label>`).join("")}</div><small class="field-hint" id="model-compare-select-hint">已选择 ${selected.size} 个，最多选择 3 个</small></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit" id="model-compare-submit" ${selected.size < 2 ? "disabled" : ""}><i data-lucide="columns-2"></i><span>开始对比</span></button></div></form>`);
  const form = document.getElementById("model-compare-select-form");
  const submit = document.getElementById("model-compare-submit");
  const hint = document.getElementById("model-compare-select-hint");
  const syncSelection = () => {
    const count = form.querySelectorAll('input[name="model"]:checked').length;
    hint.textContent = `已选择 ${count} 个，最多选择 3 个`;
    submit.disabled = count < 2 || count > 3;
  };
  form.addEventListener("change", (event) => {
    if (event.target.name !== "model") return;
    const checked = [...form.querySelectorAll('input[name="model"]:checked')];
    if (checked.length > 3) event.target.checked = false;
    syncSelection();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const names = [...new FormData(form).getAll("model")];
    if (names.length < 2 || names.length > 3) return;
    portalState.marketplace.compare = names;
    modelCompareDialog();
  });
}

function toggleModelComparison(modelName) {
  const selected = portalState.marketplace.compare;
  const index = selected.indexOf(modelName);
  if (index >= 0) selected.splice(index, 1);
  else if (selected.length >= 3) {
    portalToast("最多同时对比 3 个模型", true);
    return;
  } else selected.push(modelName);
  renderModelMarketplace();
}

async function loadUsage() {
  if (!portalState.keys.length || !portalState.models.length) {
    const [keys, models] = await Promise.all([portalApi("/portal/api-keys"), portalApi("/portal/models")]);
    portalState.keys = keys.data;
    portalState.models = models.data;
  }
  populateUsageFilters();
  const query = usageQueryString();
  const analyticsQuery = usageAnalyticsQueryString();
  const granularity = document.getElementById("usage-range-filter").value === "24h" ? "hour" : "day";
  const [summary, records, analytics] = await Promise.all([
    portalApi(`/portal/usage${query}`),
    portalApi(`/portal/usage/records${query ? `${query}&` : "?"}page=${portalState.usage.page}&page_size=${portalState.usage.pageSize}`),
    portalApi(`/portal/usage/analytics${analyticsQuery}&granularity=${granularity}`),
  ]);
  portalState.usage.result = records;
  portalState.usage.analytics = analytics;
  renderMetrics("portal-usage-metrics", [
    { label: "总请求数", value: formatNumber(summary.request_count), meta: `${formatNumber(summary.success_count)} 次成功`, icon: "send" },
    { label: "总 Token", value: formatNumber(summary.total_tokens), meta: `输入 ${formatNumber(summary.input_tokens)} · 输出 ${formatNumber(summary.output_tokens)}`, icon: "box", color: "blue" },
    { label: "总消费", value: formatMoney(summary.amount_micros), meta: `${formatNumber(summary.failed_count)} 次失败或拒绝`, icon: "receipt-text", color: "orange" },
    { label: "平均耗时", value: `${formatNumber(summary.average_latency_ms)} ms`, meta: `成功率 ${summary.success_rate}%`, icon: "timer" },
  ]);
  renderUsageAnalytics(analytics);
  document.getElementById("usage-result-count").textContent = `共 ${formatNumber(records.total)} 条记录`;
  document.getElementById("usage-page-label").textContent = `第 ${records.page} / ${records.total_pages} 页`;
  document.getElementById("usage-page-prev").disabled = records.page <= 1;
  document.getElementById("usage-page-next").disabled = records.page >= records.total_pages;
  document.getElementById("portal-usage-table").innerHTML = records.data.length ? records.data.map((item) => `
    <tr>
      <td>${formatDate(item.created_at)}</td>
      <td class="mono" title="${escapeHtml(item.request_id)}">${escapeHtml(shortId(item.request_id))}</td>
      <td>${escapeHtml(item.api_key_name)}</td>
      <td>${escapeHtml(item.model)}</td>
      <td><div class="token-cell"><strong>${formatNumber(item.total_tokens)}</strong><span>↓ ${formatNumber(item.input_tokens)} · ↑ ${formatNumber(item.output_tokens)}</span></div></td>
      <td>${formatNumber(item.latency_ms)} ms</td>
      <td>${formatMoney(item.amount_micros)}</td>
      <td>${statusBadge(item.status)}</td>
      <td class="align-right"><button class="icon-button compact-icon" data-request-id="${escapeHtml(item.request_id)}" type="button" title="查看请求详情" aria-label="查看请求详情"><i data-lucide="arrow-up-right"></i></button></td>
    </tr>
  `).join("") : emptyRow(9);
  portalIcons();
}

function populateUsageFilters() {
  const modelSelect = document.getElementById("usage-model-filter");
  const keySelect = document.getElementById("usage-key-filter");
  const selectedModel = modelSelect.value;
  const selectedKey = keySelect.value;
  modelSelect.innerHTML = '<option value="">全部模型</option>' + portalState.models.map((item) => `<option value="${escapeHtml(item.public_name)}">${escapeHtml(item.public_name)}</option>`).join("");
  keySelect.innerHTML = '<option value="">全部 Key</option>' + portalState.keys.map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.key_prefix)}</option>`).join("");
  modelSelect.value = selectedModel;
  keySelect.value = selectedKey;
  toggleUsageCustomDates();
}

function usageQueryString() {
  const form = document.getElementById("portal-usage-filter");
  const data = new FormData(form);
  const params = new URLSearchParams();
  if (data.get("model")) params.set("model", data.get("model"));
  if (data.get("api_key_id")) params.set("api_key_id", data.get("api_key_id"));
  const dates = usageDateValues();
  if (dates.from) params.set("from", dates.from);
  if (dates.to) params.set("to", dates.to);
  if (data.get("status")) params.set("status", data.get("status"));
  if (data.get("request_id")) params.set("request_id", data.get("request_id"));
  const value = params.toString();
  return value ? `?${value}` : "";
}

function usageAnalyticsQueryString() {
  const query = usageQueryString();
  return query || "?";
}

function usageDateValues() {
  const form = document.getElementById("portal-usage-filter");
  const range = form.elements.range.value;
  if (range !== "custom") {
    const hours = range === "24h" ? 24 : range === "30d" ? 24 * 30 : 24 * 7;
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600 * 1000);
    return { from: start.toISOString(), to: end.toISOString() };
  }
  return {
    from: form.elements.from.value ? new Date(form.elements.from.value).toISOString() : "",
    to: form.elements.to.value ? new Date(form.elements.to.value).toISOString() : "",
  };
}

function toggleUsageCustomDates() {
  const custom = document.getElementById("usage-range-filter").value === "custom";
  document.getElementById("usage-custom-dates").hidden = !custom;
}

function renderDistribution(targetId, items, emptyText) {
  const target = document.getElementById(targetId);
  if (!items.length) {
    target.innerHTML = `<div class="distribution-empty"><i data-lucide="pie-chart"></i><span>${emptyText}</span></div>`;
    portalIcons();
    return;
  }
  const total = items.reduce((sum, item) => sum + Number(item.total_tokens || 0), 0) || 1;
  // Keep analytical charts in the LokSystem palette: brand blue plus quiet neutrals.
  const colors = ["#3f6ff5", "#7b91c9", "#59636d", "#9aa5b1", "#b0b8c0", "#c2c9d0"];
  let offset = 0;
  const stops = items.map((item, index) => {
    const share = Number(item.total_tokens || 0) / total * 100;
    const stop = `${colors[index % colors.length]} ${offset}% ${offset + share}%`;
    offset += share;
    return stop;
  }).join(", ");
  target.innerHTML = `<div class="donut" style="background:conic-gradient(${stops})"><div><strong>${formatNumber(total)}</strong><span>Token</span></div></div><div class="distribution-list">${items.slice(0, 6).map((item, index) => {
    const share = Number(item.total_tokens || 0) / total * 100;
    return `<div class="distribution-row"><i style="background:${colors[index % colors.length]}"></i><span title="${escapeHtml(item.name || item.model)}">${escapeHtml(item.name || item.model)}</span><strong>${share.toFixed(1)}%</strong><small>${formatNumber(item.total_tokens)} Token</small></div>`;
  }).join("")}</div>`;
}

function renderUsageAnalytics(analytics) {
  renderDistribution("usage-model-distribution", analytics.model_distribution, "当前筛选暂无模型数据");
  renderDistribution("usage-key-distribution", analytics.key_distribution, "当前筛选暂无 Key 数据");
  document.getElementById("usage-trend-period").textContent = analytics.granularity === "hour" ? "按小时统计" : "按日统计";
  renderUsageTrend(analytics.trend, portalState.usage.analyticsMode);
}

function usageTrendConfig(mode) {
  if (mode === "requests") return [{ key: "request_count", label: "请求数", color: "#3f6ff5", format: formatNumber }];
  if (mode === "cost") return [{ key: "amount_micros", label: "消费", color: "#59636d", format: (value) => formatMoney(value) }];
  return [
    { key: "input_tokens", label: "输入 Token", color: "#3f6ff5", format: formatNumber },
    { key: "output_tokens", label: "输出 Token", color: "#7b91c9", format: formatNumber },
  ];
}

function renderUsageTrend(items, mode) {
  const svg = document.getElementById("usage-trend-chart");
  const configs = usageTrendConfig(mode);
  const width = 1120; const height = 270;
  const padding = { top: 20, right: 20, bottom: 38, left: 62 };
  const plotWidth = width - padding.left - padding.right; const plotHeight = height - padding.top - padding.bottom;
  const values = configs.flatMap((config) => items.map((item) => Number(item[config.key] || 0) * (mode === "cost" ? 1 / 1_000_000 : 1)));
  const scaleMax = Math.max(...values, 0) || 1;
  const xAt = (index) => items.length <= 1 ? padding.left + plotWidth / 2 : padding.left + index * plotWidth / (items.length - 1);
  const yAt = (value) => padding.top + plotHeight - value / scaleMax * plotHeight;
  const grid = [0, 1, 2, 3, 4].map((index) => {
    const value = scaleMax * index / 4; const y = padding.top + plotHeight - plotHeight * index / 4;
    const label = mode === "cost" ? formatMoney(value * 1_000_000) : chartValueLabel(value, "tokens");
    return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="chart-grid-line"></line><text x="${padding.left - 10}" y="${y + 4}" text-anchor="end" class="chart-axis-label">${label}</text>`;
  }).join("");
  const step = Math.max(1, Math.ceil((items.length - 1) / 6));
  const labels = items.map((item, index) => {
    if (index % step !== 0 && index !== items.length - 1) return "";
    const label = item.bucket.length === 10 ? item.bucket.slice(5) : `${item.bucket.slice(5, 10)} ${item.bucket.slice(11, 16)}`;
    return `<text x="${xAt(index)}" y="${height - 12}" text-anchor="middle" class="chart-axis-label">${escapeHtml(label)}</text>`;
  }).join("");
  const lines = configs.map((config) => {
    const points = items.map((item, index) => `${xAt(index)},${yAt(Number(item[config.key] || 0) * (mode === "cost" ? 1 / 1_000_000 : 1))}`).join(" ");
    const circles = items.length <= 31 ? items.map((item, index) => `<circle cx="${xAt(index)}" cy="${yAt(Number(item[config.key] || 0) * (mode === "cost" ? 1 / 1_000_000 : 1))}" r="3" fill="var(--surface)" stroke="${config.color}" stroke-width="2"><title>${escapeHtml(item.bucket)} · ${escapeHtml(config.format(item[config.key]))}</title></circle>`).join("") : "";
    return `<polyline points="${points}" fill="none" stroke="${config.color}" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"></polyline>${circles}`;
  }).join("");
  svg.innerHTML = items.length ? `${grid}${lines}${labels}` : `<text x="560" y="135" text-anchor="middle" class="chart-axis-label">当前筛选暂无趋势数据</text>`;
  document.getElementById("usage-trend-legend").innerHTML = configs.map((config) => `<span><i style="background:${config.color}"></i>${config.label}</span>`).join("");
}

async function exportUsage() {
  const response = await fetch(`/portal/usage/export${usageQueryString()}`, { headers: { Authorization: `Bearer ${portalState.token}` } });
  if (!response.ok) throw new Error(`导出失败 (${response.status})`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `token-usage-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

async function loadUsageDetail(requestId) {
  const item = await portalApi(`/portal/usage/records/${encodeURIComponent(requestId)}`);
  openPortalDialog("请求详情", `
    <div class="dialog-body request-detail-body">
      <div class="detail-hero"><div><span class="detail-label">请求 ID</span><strong class="mono">${escapeHtml(item.request_id)}</strong></div>${statusBadge(item.status)}</div>
      <div class="detail-grid">
        <div><span>创建时间</span><strong>${formatDate(item.created_at)}</strong></div>
        <div><span>Trace ID</span><strong class="mono">${escapeHtml(shortId(item.trace_id))}</strong></div>
        <div><span>API Key</span><strong>${escapeHtml(item.api_key_name)}</strong></div>
        <div><span>模型</span><strong>${escapeHtml(item.model)}</strong></div>
        <div><span>输入 Token</span><strong>${formatNumber(item.input_tokens)}</strong></div>
        <div><span>输出 Token</span><strong>${formatNumber(item.output_tokens)}</strong></div>
        <div><span>总 Token</span><strong>${formatNumber(item.total_tokens)}</strong></div>
        <div><span>费用</span><strong>${formatMoney(item.amount_micros)}</strong></div>
        <div><span>耗时</span><strong>${formatNumber(item.latency_ms)} ms</strong></div>
      </div>
      ${item.error_message ? `<div class="detail-error"><span>错误摘要</span><p>${escapeHtml(item.error_message)}</p></div>` : ""}
    </div>
    <div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
}

function transactionBadge(type) {
  const map = {
    payment: ["订单入账", "success"], topup: ["额度充值", "success"], redemption: ["福利兑换", "success"],
    reservation: ["模型消费", "neutral"], settlement: ["结算调整", "neutral"], refund: ["订单退款", "warning"],
  };
  const item = map[type] || [type, "neutral"];
  return `<span class="badge ${item[1]}">${escapeHtml(item[0])}</span>`;
}

function formatBalanceChange(value) {
  const amount = Number(value || 0);
  return `<strong class="${amount >= 0 ? "amount-positive" : "amount-negative"}">${amount >= 0 ? "+" : ""}${formatMoney(amount)}</strong>`;
}

async function loadQuota() {
  const [summary, transactions] = await Promise.all([portalApi("/portal/balance-summary"), portalApi("/portal/transactions")]);
  document.getElementById("quota-balance").textContent = formatMoney(summary.balance_micros);
  document.getElementById("quota-total-credit").textContent = formatMoney(summary.total_credit_micros);
  document.getElementById("quota-total-consumed").textContent = formatMoney(summary.total_consumed_micros);
  document.getElementById("quota-transaction-count").textContent = `${formatNumber(summary.transaction_count)} 笔`;
  document.getElementById("quota-history-count").textContent = `最近 ${formatNumber(transactions.data.length)} 条记录`;
  document.getElementById("portal-transactions-table").innerHTML = transactions.data.length ? transactions.data.map((item) => `
    <tr><td>${formatDate(item.created_at)}</td><td>${transactionBadge(item.type)}</td><td>${escapeHtml(item.description || "账户余额调整")}</td><td class="mono" title="${escapeHtml(item.reference_id)}">${escapeHtml(shortId(item.reference_id))}</td><td class="align-right">${formatBalanceChange(item.amount_micros)}</td></tr>
  `).join("") : emptyRow(5);
}

function renderOrders() {
  const selectedStatus = document.getElementById("order-status-filter").value;
  const items = portalState.orders.filter((item) => !selectedStatus || item.status === selectedStatus);
  document.getElementById("order-result-count").textContent = `显示 ${formatNumber(items.length)} 个 / 共 ${formatNumber(portalState.orders.length)} 个订单`;
  document.getElementById("portal-orders-table").innerHTML = items.length ? items.map((item) => `
    <tr><td class="mono" title="${escapeHtml(item.order_no)}">${escapeHtml(shortId(item.order_no))}</td><td>${escapeHtml(item.provider)}</td><td><strong>${formatMoney(item.amount_micros)}</strong></td><td>${statusBadge(item.status)}</td><td>${formatDate(item.created_at)}</td><td>${item.paid_at ? formatDate(item.paid_at) : "-"}</td></tr>
  `).join("") : emptyRow(6);
}

async function loadOrders() {
  const orders = await portalApi("/portal/payment-orders");
  portalState.orders = orders.data;
  renderOrders();
}

async function loadRedeem() {
  const [summary, redemptions] = await Promise.all([portalApi("/portal/balance-summary"), portalApi("/portal/redemptions")]);
  document.getElementById("redeem-balance").textContent = formatMoney(summary.balance_micros);
  document.getElementById("redeem-claim-count").textContent = redemptions.data.length ? `已兑换 ${formatNumber(redemptions.data.length)} 次福利` : "暂无兑换记录";
  document.getElementById("redeem-history-count").textContent = `最近 ${formatNumber(redemptions.data.length)} 条记录`;
  document.getElementById("portal-redemptions-table").innerHTML = redemptions.data.length ? redemptions.data.map((item) => `
    <tr><td><div class="primary-cell"><strong>${escapeHtml(item.label)}</strong><span class="secondary">兑换成功</span></div></td><td>${formatDate(item.redeemed_at)}</td><td class="align-right">${formatBalanceChange(item.amount_micros)}</td></tr>
  `).join("") : emptyRow(3);
}

async function redeemBenefit(form) {
  const code = String(new FormData(form).get("code") || "").trim();
  if (!code) return;
  const result = await portalApi("/portal/redemption-codes/redeem", { method: "POST", body: JSON.stringify({ code }) });
  form.reset();
  portalToast(`兑换成功，${formatMoney(result.amount_micros)} 已到账`);
  await Promise.all([loadRedeem(), loadQuota(), loadProfile()]);
}

const portalLoaders = { overview: loadOverview, keys: loadKeys, models: loadModels, usage: loadUsage, quota: loadQuota, orders: loadOrders, redeem: loadRedeem };

function renderPortalTopbarContext(view) {
  const target = document.querySelector(`#portal-view-${view} .content-page-actions`);
  if (!target) return;
  const actions = {
    keys: '<button class="secondary-button topbar-context-button" type="button" data-action="key-columns"><i data-lucide="columns-3"></i><span>列设置</span></button><button class="primary-button topbar-context-button" type="button" data-action="create-key"><i data-lucide="plus"></i><span>创建 Key</span></button>',
    models: '<button class="icon-button model-page-back" id="model-marketplace-provider-back" type="button" data-model-provider-back hidden title="返回模型广场" aria-label="返回模型广场"><i data-lucide="arrow-left"></i></button>',
    quota: '<button class="primary-button topbar-context-button" type="button" data-action="create-payment"><i data-lucide="plus"></i><span>充值额度</span></button>',
    orders: '<button class="primary-button topbar-context-button" type="button" data-go="quota"><i data-lucide="plus"></i><span>创建充值申请</span></button>',
  };
  const globalActions = `${view === "overview" ? '<a class="secondary-button topbar-doc-link" href="/guide/user" target="_blank" rel="noopener"><i data-lucide="book-open"></i><span>用户文档</span></a>' : ""}<button class="icon-button" type="button" data-action="portal-back" title="返回上一页" aria-label="返回上一页" ${view === "overview" ? "disabled" : ""}><i data-lucide="arrow-left"></i></button><button class="icon-button" type="button" data-action="portal-refresh" title="刷新数据" aria-label="刷新数据"><i data-lucide="refresh-cw"></i></button>`;
  target.innerHTML = `${actions[view] || ""}${globalActions}`;
  portalIcons();
}

async function switchPortalView(view, { historyMode = "push" } = {}) {
  if (!Object.prototype.hasOwnProperty.call(portalTitles, view)) view = "overview";
  portalState.view = view;
  updatePortalHistory(view, historyMode);
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === `portal-view-${view}`));
  renderPortalTopbarContext(view);
  try { await portalLoaders[view](); } catch (error) { portalToast(error.message, true); }
}

function closePortalAccountMenu() {
  document.getElementById("portal-account-menu").hidden = true;
  document.getElementById("portal-account-trigger").setAttribute("aria-expanded", "false");
}

function openPortalDialog(title, content) {
  document.getElementById("portal-dialog-title").textContent = title;
  document.getElementById("portal-dialog-content").innerHTML = content;
  const dialog = document.getElementById("portal-dialog");
  if (!dialog.open) dialog.showModal();
  portalIcons();
}

function closePortalDialog() {
  document.getElementById("portal-dialog").close();
}

function keyDetailDialog(keyId) {
  const item = portalState.keys.find((key) => key.id === Number(keyId));
  if (!item) return;
  openPortalDialog("API Key 详情", `<div class="dialog-body key-detail-body">
    <div class="key-detail-head"><div><span class="detail-label">名称</span><strong>${escapeHtml(item.name)}</strong></div>${keyStatusBadge(item)}</div>
    <div class="key-detail-grid">
      <div><span>Key 前缀</span><strong class="mono">${escapeHtml(item.key_prefix)}...</strong></div>
      <div><span>所属项目</span><strong>${escapeHtml(item.project_name || "个人项目")}</strong></div>
      <div><span>消费额度</span><strong>${escapeHtml(formatKeyBudget(item))}</strong></div>
      <div><span>创建时间</span><strong>${formatDate(item.created_at)}</strong></div>
      <div><span>过期时间</span><strong>${keyExpiry(item) ? formatDate(keyExpiry(item)) : "长期有效"}</strong></div>
      <div><span>最近使用</span><strong>${formatDate(item.last_used_at)}</strong></div>
      <div><span>安全提示</span><strong>完整密钥不可恢复</strong></div>
    </div>
  </div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
}

function keyColumnsDialog() {
  const columns = [
    ["project", "所属项目"], ["usage", "用量"], ["expires", "有效期"], ["last-used", "最近使用"], ["created", "创建时间"],
  ];
  openPortalDialog("列设置", `<form id="key-columns-form"><div class="dialog-body"><p class="dialog-copy">选择在 API Key 列表中显示的信息。</p><div class="column-options">${columns.map(([value, label]) => `<label><input type="checkbox" name="column" value="${value}" ${portalState.keyColumns[value] ? "checked" : ""}><span>${label}</span></label>`).join("")}</div></div><div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">应用设置</button></div></form>`);
  document.getElementById("key-columns-form").addEventListener("submit", (event) => {
    event.preventDefault();
    columns.forEach(([value]) => { portalState.keyColumns[value] = false; });
    new FormData(event.currentTarget).getAll("column").forEach((value) => { portalState.keyColumns[value] = true; });
    renderKeyTable();
    closePortalDialog();
  });
}

function keyDialog(options = {}) {
  const minimumDate = new Date();
  minimumDate.setDate(minimumDate.getDate() + 1);
  const minimumDateText = minimumDate.toISOString().slice(0, 10);
  openPortalDialog("密钥管理", `
    <form id="portal-dialog-form">
      <div class="dialog-body">
        <div class="key-dialog-intro"><i data-lucide="shield-check"></i><p>用于调用 LokToken 模型 API 的访问凭证。密钥创建后只会完整展示一次，请妥善保存。</p></div>
        <div class="field"><label for="portal-key-name">名称</label><input id="portal-key-name" name="name" required maxlength="120" placeholder="例如：生产服务"></div>
        <div class="field"><label for="portal-key-project">归属项目</label><select id="portal-key-project" name="project_id">${portalState.projects.map((project) => `<option value="${project.id}" ${project.id === activeProjectId() ? "selected" : ""}>${escapeHtml(project.name)} · ${escapeHtml(project.slug)}</option>`).join("")}</select></div>
        <div class="key-dialog-section"><div><strong>额度与有效期</strong><span>留空表示不限制</span></div></div>
        <div class="field-row"><div class="field"><label for="portal-key-limit">消费额度（元）</label><input id="portal-key-limit" name="spending_limit" type="number" min="0.01" step="0.01" placeholder="不限"><small class="field-hint">达到额度后将拒绝新的请求</small></div><div class="field"><label for="portal-key-expiry">过期时间</label><input id="portal-key-expiry" name="expires_at" type="date" min="${minimumDateText}"><small class="field-hint">不选择则长期有效</small></div></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="key-round"></i><span>密钥管理</span></button></div>
    </form>`);
  document.getElementById("portal-dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    if (data.expires_at) data.expires_at = new Date(`${data.expires_at}T23:59:59`).toISOString(); else delete data.expires_at;
    if (data.spending_limit) data.spending_limit_micros = Math.round(Number(data.spending_limit) * 1_000_000);
    delete data.spending_limit;
    data.idempotency_key = `portal-key-${crypto.randomUUID()}`;
    try {
      const result = await portalApi("/portal/api-keys", { method: "POST", body: JSON.stringify(data) });
      openPortalDialog("API Key 创建成功", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="triangle-alert"></i><span>完整密钥只展示这一次。关闭窗口后将无法再次查看。</span></div><div class="field"><label>完整 API Key</label><div class="secret-box mono" id="portal-key-secret">${escapeHtml(result.key)}</div></div><div class="secret-actions"><button class="secondary-button" id="portal-copy-key"><i data-lucide="copy"></i><span>复制完整 Key</span></button></div><p class="dialog-copy">复制后可直接回到 LokSystem 模型管理。系统会预选 LokToken，粘贴密钥后自动读取可用模型。</p></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>稍后配置</button><button class="primary-button" type="button" id="portal-configure-loksystem"><i data-lucide="settings-2"></i><span>复制并前往 LokSystem</span></button></div>`);
       document.getElementById("portal-copy-key").addEventListener("click", async () => { await navigator.clipboard.writeText(result.key); portalToast("密钥已复制"); });
       const integrationButton = document.getElementById("portal-configure-loksystem");
       if (integrationButton) { integrationButton.textContent = "完成"; integrationButton.removeAttribute("id"); integrationButton.dataset.close = ""; integrationButton.querySelector("i")?.remove(); }
       const integrationCopy = document.querySelector("#portal-dialog-content .dialog-copy");
       if (integrationCopy) integrationCopy.textContent = "请将密钥保存到你的应用或服务端密钥管理系统中。";
      await loadKeys();
      if (typeof options.onCreated === "function") options.onCreated(result.key);
    } catch (error) { portalToast(error.message, true); }
  });
}

async function paymentDialog() {
  const providers = await portalApi("/portal/payment-providers");
  openPortalDialog("创建充值申请", `
    <form id="portal-dialog-form">
      <div class="dialog-body">
        <div class="field"><label for="portal-payment-amount">充值金额（元）</label><input id="portal-payment-amount" name="amount" type="number" min="0.01" step="0.01" required></div>
        <div class="field"><label for="portal-payment-provider">充值渠道</label><select id="portal-payment-provider" name="provider">${providers.data.map((item) => `<option value="${escapeHtml(item.id)}" ${item.available ? "" : "disabled"}>${escapeHtml(item.name)}${item.available ? "" : " · 未接入"}</option>`).join("")}</select></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">提交申请</button></div>
    </form>`);
  document.getElementById("portal-dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = { account_id: portalState.profile.id, project_id: activeProjectId(), amount_micros: Math.round(Number(data.amount) * 1_000_000), provider: data.provider };
    try { await portalApi("/portal/payment-orders", { method: "POST", body: JSON.stringify(payload) }); closePortalDialog(); portalToast("充值申请已提交"); await Promise.all([loadQuota(), loadOrders()]); } catch (error) { portalToast(error.message, true); }
  });
}

async function rotatePortalKey(keyId) {
  const item = portalState.keys.find((key) => key.id === Number(keyId));
  if (!item || !window.confirm(`轮换“${item.name}”后，旧 Key 将立即失效。是否继续？`)) return;
  try {
    const result = await portalApi(`/portal/api-keys/${keyId}/rotate`, { method: "POST", body: "{}" });
    openPortalDialog("API Key 已轮换", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="triangle-alert"></i><span>新 Key 只展示一次，旧 Key 已失效。请立即更新你的应用配置。</span></div><div class="field"><label>新 API Key</label><div class="secret-box mono" id="portal-key-secret">${escapeHtml(result.key)}</div></div><div class="secret-actions"><button class="secondary-button" id="portal-copy-key"><i data-lucide="copy"></i><span>复制新 Key</span></button></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>我已保存</button></div>`);
    document.getElementById("portal-copy-key").addEventListener("click", async () => { await navigator.clipboard.writeText(result.key); portalToast("新 Key 已复制"); });
    await loadKeys();
  } catch (error) { portalToast(error.message, true); }
}

async function portalSecurityDialog() {
  try {
    const notifications = await portalApi("/portal/security-notifications");
    const contact = portalState.profile?.security_contact || "未绑定";
    const contactStatus = portalState.profile?.security_contact_verified_at ? "已验证" : (portalState.profile?.security_contact ? "待验证" : "未绑定");
    const items = notifications.data.slice(0, 6).map((item) => `<div class="status-row"><span class="status-name"><i data-lucide="shield-check"></i>${escapeHtml(item.event_type)}</span><span class="secondary">${formatDate(item.created_at)}</span></div>`).join("") || '<p class="dialog-copy">暂无安全事件。</p>';
    openPortalDialog("账号安全", `<div class="dialog-body"><div class="key-detail-grid"><div><span>安全联系方式 · ${escapeHtml(contactStatus)}</span><strong>${escapeHtml(contact)}</strong></div><div><span>登录会话</span><strong>可全部退出</strong></div></div><div class="field"><label for="security-contact">绑定安全联系方式</label><input id="security-contact" value="${escapeHtml(portalState.profile?.security_contact || "")}" placeholder="邮箱或手机号"><small class="field-hint">保存后需要通过一次性验证码确认，才能用于找回密码。</small></div><div class="field"><label for="security-password">当前密码</label><input id="security-password" type="password" autocomplete="current-password"></div><div class="section-header"><div><h2>最近安全事件</h2></div></div><div class="status-list">${items}</div></div><div class="dialog-actions"><button class="secondary-button" type="button" id="portal-logout-all">退出其他会话</button><button class="primary-button" type="button" id="portal-bind-security">发送验证码</button></div>`);
    document.getElementById("portal-bind-security").addEventListener("click", async () => {
      try {
        const contactValue = document.getElementById("security-contact").value.trim();
        const password = document.getElementById("security-password").value;
        if (!contactValue || !password) throw new Error("请填写联系方式和当前密码");
        const result = await portalApi("/portal/security/contact", { method: "PUT", body: JSON.stringify({ contact: contactValue, password }) });
        await loadProfile();
        securityContactVerificationDialog(result);
      } catch (error) { portalToast(error.message, true); }
    });
    document.getElementById("portal-logout-all").addEventListener("click", async () => {
      try {
        await portalApi("/portal/security/logout-all", { method: "POST", body: "{}" });
        sessionStorage.removeItem("token_portal_access"); portalState.token = ""; closePortalDialog(); showPortalAuth("所有登录会话已退出，请重新登录。");
      } catch (error) { portalToast(error.message, true); }
    });
  } catch (error) { portalToast(error.message, true); }
}

document.addEventListener("DOMContentLoaded", async () => {
  portalIcons();
  const fragmentParams = new URLSearchParams(window.location.hash.slice(1));
  const invitationToken = fragmentParams.get("invite_token") || new URLSearchParams(window.location.search).get("invite_token") || "";
  const fragmentToken = fragmentParams.get("access_token");
  const loksystemSsoError = fragmentParams.get("sso_error");
  if (fragmentToken) {
    portalState.token = fragmentToken;
    sessionStorage.setItem("token_portal_access", fragmentToken);
    window.history.replaceState({}, "", "/portal");
  } else if (loksystemSsoError) {
    window.history.replaceState({}, "", "/portal");
  }
  document.getElementById("portal-auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    portalState.token = String(new FormData(event.currentTarget).get("token") || "").trim();
    if (!portalState.token.startsWith("trl_")) {
      showPortalAuth("此处需要 trl_ 开头的试用令牌；change-me 仅用于管理后台。");
      return;
    }
    try { await loadProfile(); await loadWorkspaces(); sessionStorage.setItem("token_portal_access", portalState.token); showPortalShell(); await switchPortalView("overview"); } catch (error) { showPortalAuth(error.message); }
  });
  document.querySelectorAll(".auth-mode-tabs button").forEach((button) => button.addEventListener("click", () => setAuthMode(button.dataset.authMode)));
  setAuthMode("login");
  if (invitationToken) {
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete("invite_token");
    cleanUrl.hash = "";
    window.history.replaceState(window.history.state, "", `${cleanUrl.pathname}${cleanUrl.search}`);
    passwordSetupDialog(invitationToken, true);
  }
  const loksystemLoginButton = document.getElementById("loksystem-login-button");
  try {
    const loksystemStatus = await fetch("/auth/loksystem/status").then((response) => response.json());
    if (loksystemStatus.enabled) loksystemLoginButton.hidden = false;
  } catch (_) {}
  loksystemLoginButton.addEventListener("click", () => { window.location.href = "/auth/loksystem/start"; });
  const oidcLoginButton = document.getElementById("oidc-login-button");
  try {
    const oidcStatus = await fetch("/auth/oidc/status").then((response) => response.json());
    if (oidcStatus.enabled) oidcLoginButton.hidden = false;
  } catch (_) {}
  oidcLoginButton.addEventListener("click", () => { window.location.href = "/auth/oidc/start"; });
  document.querySelectorAll("[data-toggle-password]").forEach((button) => button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.togglePassword);
    input.type = input.type === "password" ? "text" : "password";
  }));
  document.getElementById("portal-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = Object.fromEntries(new FormData(event.currentTarget));
      await establishPortalSession(await portalApi("/auth/login", { method: "POST", body: JSON.stringify(data) }));
    } catch (error) { showPortalAuth(error.message); }
  });
  document.getElementById("portal-forgot-password").addEventListener("click", passwordResetDialog);
  document.getElementById("portal-register-link").addEventListener("click", () => setAuthMode("register"));
  document.getElementById("portal-register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = Object.fromEntries(new FormData(event.currentTarget));
      const result = await portalApi("/auth/register", { method: "POST", body: JSON.stringify(data) });
      await establishPortalSession(result);
      if (result.security_contact_verification_required) securityContactVerificationDialog(result);
    } catch (error) { showPortalAuth(error.message); }
  });
  document.getElementById("toggle-portal-token").addEventListener("click", () => {
    const input = document.getElementById("portal-token");
    input.type = input.type === "password" ? "text" : "password";
  });
  document.getElementById("portal-account-trigger").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = document.getElementById("portal-account-menu");
    menu.hidden = !menu.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.getElementById("portal-logout").addEventListener("click", () => {
    sessionStorage.removeItem("token_portal_access"); portalState.token = ""; closePortalAccountMenu(); document.getElementById("portal-token").value = ""; showPortalAuth();
  });
  document.getElementById("portal-security").addEventListener("click", () => { closePortalAccountMenu(); portalSecurityDialog(); });
  document.getElementById("portal-notifications").addEventListener("click", () => { closePortalAccountMenu(); portalNotificationDialog().catch((error) => portalToast(error.message, true)); });
  document.getElementById("portal-workspace-manager").addEventListener("click", () => { closePortalAccountMenu(); workspaceManagerDialog().catch((error) => portalToast(error.message, true)); });
  document.getElementById("portal-dialog-close").addEventListener("click", closePortalDialog);
  document.getElementById("key-search").addEventListener("input", (event) => { portalState.keyFilters.search = event.target.value; renderKeyTable(); });
  document.getElementById("key-search").addEventListener("change", (event) => { portalState.keyFilters.search = event.target.value; renderKeyTable(); });
  document.getElementById("key-status-filter").addEventListener("change", (event) => { portalState.keyFilters.status = event.target.value; renderKeyTable(); });
  document.getElementById("model-marketplace-search").addEventListener("input", (event) => { portalState.marketplace.query = event.target.value; renderModelMarketplace(); });
  document.getElementById("model-marketplace-provider-filter").addEventListener("change", (event) => { portalState.marketplace.providerFilter = event.target.value; portalState.marketplace.provider = ""; renderModelMarketplace(); });
  document.getElementById("model-marketplace-health").addEventListener("change", (event) => { portalState.marketplace.health = event.target.value; renderModelMarketplace(); });
  document.getElementById("order-status-filter").addEventListener("change", renderOrders);
  document.getElementById("portal-redeem-form").addEventListener("submit", (event) => {
    event.preventDefault();
    redeemBenefit(event.currentTarget).catch((error) => portalToast(error.message, true));
  });
  document.getElementById("portal-usage-filter").addEventListener("submit", (event) => { event.preventDefault(); portalState.usage.page = 1; loadUsage().catch((error) => portalToast(error.message, true)); });
  document.getElementById("usage-range-filter").addEventListener("change", () => { toggleUsageCustomDates(); if (document.getElementById("usage-range-filter").value !== "custom") { loadUsage().catch((error) => portalToast(error.message, true)); } });
  document.getElementById("usage-reset").addEventListener("click", () => {
    const form = document.getElementById("portal-usage-filter"); form.reset(); document.getElementById("usage-range-filter").value = "7d"; document.getElementById("usage-status-filter").value = ""; portalState.usage.page = 1; toggleUsageCustomDates(); loadUsage().catch((error) => portalToast(error.message, true));
  });
  document.getElementById("usage-export").addEventListener("click", () => exportUsage().then(() => portalToast("请求记录文件已导出")).catch((error) => portalToast(error.message, true)));
  document.getElementById("usage-page-size").addEventListener("change", (event) => { portalState.usage.pageSize = Number(event.target.value); portalState.usage.page = 1; loadUsage().catch((error) => portalToast(error.message, true)); });
  document.getElementById("usage-page-prev").addEventListener("click", () => { if (portalState.usage.page > 1) { portalState.usage.page -= 1; loadUsage().catch((error) => portalToast(error.message, true)); } });
  document.getElementById("usage-page-next").addEventListener("click", () => { const result = portalState.usage.result; if (result && portalState.usage.page < result.total_pages) { portalState.usage.page += 1; loadUsage().catch((error) => portalToast(error.message, true)); } });
  document.querySelectorAll("#usage-trend-mode button").forEach((button) => button.addEventListener("click", () => { portalState.usage.analyticsMode = button.dataset.mode; document.querySelectorAll("#usage-trend-mode button").forEach((item) => item.classList.toggle("active", item === button)); if (portalState.usage.analytics) renderUsageTrend(portalState.usage.analytics.trend, portalState.usage.analyticsMode); }));
  ["overview-days", "overview-model", "overview-key"].forEach((id) => document.getElementById(id).addEventListener("change", () => loadOverviewDashboard().catch((error) => portalToast(error.message, true))));
  document.querySelectorAll("#overview-mode button").forEach((button) => button.addEventListener("click", () => {
    portalState.overviewMode = button.dataset.mode;
    document.querySelectorAll("#overview-mode button").forEach((item) => item.classList.toggle("active", item === button));
    if (portalState.dashboard) renderOverviewDashboard();
  }));
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => switchPortalView(item.dataset.view)));
  document.body.addEventListener("click", async (event) => {
    if (!event.target.closest(".account-menu")) closePortalAccountMenu();
    const target = event.target.closest("button, [data-go]");
    if (!target) return;
    if (target.dataset.close !== undefined) closePortalDialog();
    if (target.dataset.go) switchPortalView(target.dataset.go);
    if (target.dataset.action === "portal-back") navigatePortalBack();
    if (target.dataset.action === "portal-refresh") switchPortalView(portalState.view, { historyMode: "none" });
    if (target.dataset.readNotification) {
      try { await portalApi(`/portal/security-notifications/${target.dataset.readNotification}/read`, { method: "POST", body: "{}" }); await portalNotificationDialog(); await loadPortalNotificationSummary(); } catch (error) { portalToast(error.message, true); }
    }
    if (target.dataset.action === "create-key") keyDialog();
    if (target.dataset.action === "model-create-key") { closePortalDialog(); await switchPortalView("keys"); keyDialog(); }
    if (target.dataset.action === "model-onboarding-create-key") {
      const modelName = target.dataset.modelName;
      closePortalDialog();
      keyDialog({ onCreated: (key) => modelOnboardingDialog(modelName, key) });
    }
    if (target.dataset.action === "key-columns") keyColumnsDialog();
    if (target.dataset.action === "create-payment") paymentDialog().catch((error) => portalToast(error.message, true));
    if (target.dataset.toggleKey) {
      try { await portalApi(`/portal/api-keys/${target.dataset.toggleKey}`, { method: "PATCH", body: JSON.stringify({ active: target.dataset.active === "true" }) }); portalToast(target.dataset.active === "true" ? "Key 已启用" : "Key 已停用"); await loadKeys(); } catch (error) { portalToast(error.message, true); }
    }
    if (target.dataset.rotateKey) rotatePortalKey(target.dataset.rotateKey);
    if (target.dataset.copy === "base-url") { await navigator.clipboard.writeText(document.getElementById("portal-base-url").textContent); portalToast("Base URL 已复制"); }
    if (target.dataset.copyKeyPrefix) { await navigator.clipboard.writeText(target.dataset.copyKeyPrefix); portalToast("Key 前缀已复制"); }
    if (target.dataset.copyModel) { await navigator.clipboard.writeText(target.dataset.copyModel); portalToast("模型 ID 已复制"); }
    if (target.dataset.copyEndpoint) { await navigator.clipboard.writeText(target.dataset.copyEndpoint); portalToast("调用地址已复制"); }
    if (target.dataset.copyOnboardingKey) { await navigator.clipboard.writeText(target.dataset.copyOnboardingKey); portalToast("完整 API Key 已复制"); }
    if (target.dataset.modelFilter) { portalState.marketplace.modality = target.dataset.modelFilter; renderModelMarketplace(); }
    if (target.dataset.modelProvider) { portalState.marketplace.provider = target.dataset.modelProvider; renderModelMarketplace(); }
    if (target.dataset.modelProviderMore !== undefined) { portalState.marketplace.provider = "__more__"; renderModelMarketplace(); }
    if (target.dataset.modelProviderBack !== undefined) { portalState.marketplace.provider = ""; renderModelMarketplace(); }
    if (target.dataset.modelFilterReset !== undefined) {
      portalState.marketplace.query = "";
      portalState.marketplace.modality = "all";
      portalState.marketplace.providerFilter = "";
      portalState.marketplace.health = "all";
      portalState.marketplace.sort = "default";
      document.getElementById("model-marketplace-search").value = "";
      document.getElementById("model-marketplace-provider-filter").value = "";
      document.getElementById("model-marketplace-health").value = "all";
      renderModelMarketplace();
    }
    if (target.dataset.modelDetail) modelDetailDialog(target.dataset.modelDetail);
    if (target.dataset.modelTest) modelTestDialog(target.dataset.modelTest);
    if (target.dataset.modelOnboard) modelOnboardingDialog(target.dataset.modelOnboard);
    if (target.dataset.modelCompare) toggleModelComparison(target.dataset.modelCompare);
    if (target.dataset.modelCompareClear !== undefined) { portalState.marketplace.compare = []; renderModelMarketplace(); }
    if (target.dataset.modelCompareProvider) compareProviderModels(target.dataset.modelCompareProvider);
    if (target.dataset.modelCompareOpen !== undefined) modelCompareDialog();
    if (target.dataset.copyModelCode) {
      const model = portalState.models.find((item) => item.public_name === target.dataset.modelName);
      if (model) {
        await navigator.clipboard.writeText(target.dataset.copyModelCode === "python" ? modelPythonSnippet(model) : modelCurlSnippet(model));
        portalToast("调用示例已复制");
      }
    }
    if (target.dataset.keyDetail) keyDetailDialog(target.dataset.keyDetail);
    if (target.dataset.requestId) loadUsageDetail(target.dataset.requestId).catch((error) => portalToast(error.message, true));
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closePortalAccountMenu(); });
  window.addEventListener("popstate", (event) => {
    if (!portalState.token) return;
    if (event.state?.app === "portal") switchPortalView(event.state.view, { historyMode: "none" });
    else {
      primePortalHistory(portalState.view);
      switchPortalView(portalState.view, { historyMode: "none" });
    }
  });
  if (portalState.token) {
    document.getElementById("portal-token").value = portalState.token;
    try { await loadProfile(); await loadWorkspaces(); showPortalShell(); const view = portalViewFromUrl(); primePortalHistory(view); await switchPortalView(view, { historyMode: "none" }); } catch (error) { showPortalAuth(error.message); }
  } else {
    showPortalAuth(loksystemSsoError || "");
  }
});
