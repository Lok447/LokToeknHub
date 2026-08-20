const state = {
  token: sessionStorage.getItem("token_admin_token") || "",
  role: sessionStorage.getItem("token_admin_role") || "",
  view: "overview",
  accounts: [],
  models: [],
  channels: [],
  identity: null,
  modelFilters: { query: "", provider: "", type: "", publicationState: "" },
};

function isSuperadmin() {
  return state.role === "superadmin";
}

function isAuditor() {
  return state.role === "auditor";
}

function canOperate() {
  return state.role === "superadmin" || state.role === "operator";
}

function setAdminIdentity(identity) {
  state.identity = identity || null;
  state.role = identity?.role || "";
  if (state.role) sessionStorage.setItem("token_admin_role", state.role);
  const roleLabels = { superadmin: "超级管理员", operator: "运营人员", auditor: "审计员" };
  const loginId = identity?.login_id || "管理员";
  document.getElementById("admin-user-name").textContent = loginId;
  document.getElementById("admin-user-role").textContent = roleLabels[state.role] || "管理账号";
  document.getElementById("admin-user-avatar").textContent = loginId.slice(0, 1).toUpperCase();
  applyRoleUi();
}

function applyRoleUi() {
  const hiddenActions = new Set(isAuditor() ? [
    "create-account", "create-key", "health-check-all", "import-models", "create-model",
    "create-payment", "create-redemption", "trial-link", "topup", "confirm-payment",
    "refund-payment", "toggle-redemption", "edit-model-pricing", "manage-channels",
    "preflight-model", "edit-channel", "check-channel", "toggle-channel", "toggle-entity",
  ] : [
    "manage-admins", "refund-payment", "preflight-model",
  ]);
  document.querySelectorAll("[data-action]").forEach((element) => {
    element.hidden = hiddenActions.has(element.dataset.action);
  });
  const roleOnlyControls = {
    "manage-admins": isSuperadmin(),
    "create-account": canOperate(),
    "create-key": canOperate(),
    "health-check-all": canOperate(),
    "import-models": canOperate(),
    "create-model": canOperate(),
    "create-payment": canOperate(),
    "create-redemption": canOperate(),
    "reconcile-ledger": !isAuditor(),
  };
  Object.entries(roleOnlyControls).forEach(([action, visible]) => {
    document.querySelectorAll(`[data-action="${action}"]`).forEach((element) => { element.hidden = !visible; });
  });
}
const titles = {
  overview: "管理概览",
  models: "模型管理",
  accounts: "账户管理",
  keys: "API管理",
  payments: "订单管理",
  redemptions: "福利管理",
  usage: "用量管理",
  audit: "安全审计",
};
function icons() {
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

function yuanPerMillionToMicrosPerThousand(value) {
  return Math.round(Number(value || 0) * 1000);
}

function microsPerThousandToYuanPerMillion(value) {
  return Number(value || 0) / 1000;
}

function officialPriceSummary(item) {
  const pricing = item.official_pricing;
  if (!pricing?.off_peak) return "-";
  const input = pricing.off_peak.input_cache_miss_micros;
  const output = pricing.off_peak.output_micros;
  return `${formatMoney(input)} / ${formatMoney(output)}`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 18 ? `${text.slice(0, 10)}...${text.slice(-5)}` : text;
}

function statusBadge(status) {
  const map = {
    success: ["success", "成功"],
    error: ["error", "失败"],
    rejected: ["warning", "已拒绝"],
  };
  const [kind, label] = map[status] || ["neutral", status || "未知"];
  return `<span class="badge ${kind}">${escapeHtml(label)}</span>`;
}

function activeBadge(active) {
  return `<span class="badge ${active ? "success" : "neutral"}">${active ? "启用" : "停用"}</span>`;
}

function paymentBadge(status) {
  const map = {
    pending: ["warning", "待支付"],
    paid: ["success", "已支付"],
    refunded: ["neutral", "已退款"],
  };
  const [kind, label] = map[status] || ["neutral", status || "未知"];
  return `<span class="badge ${kind}">${escapeHtml(label)}</span>`;
}

function channelStatusBadge(status) {
  const map = {
    healthy: ["success", "健康"],
    unhealthy: ["error", "异常"],
    unknown: ["neutral", "未检测"],
  };
  const [kind, label] = map[status] || ["neutral", status || "未知"];
  return `<span class="badge ${kind}">${escapeHtml(label)}</span>`;
}

function channelCredentialLabel(item) {
  if (item.credential_source === "console") return "控制台托管密钥";
  if (item.credential_source === "environment") return item.provider_api_key_env;
  return "默认密钥";
}

function modelPublicationBadge(item) {
  const map = {
    published: ["success", "已上架"],
    mock_published: ["warning", "Mock 已上架"],
    candidate: ["neutral", "候选"],
    blocked: ["error", "待完善"],
  };
  const [kind, label] = map[item.publication_state] || ["neutral", item.active ? "已启用" : "停用"];
  const reasons = item.publication_reasons?.length ? ` title="${escapeHtml(item.publication_reasons.join("；"))}"` : "";
  return `<span class="badge ${kind}"${reasons}>${escapeHtml(label)}</span>`;
}

function modelApiType(item) {
  return item.catalog_metadata?.api_type || "chat_completions";
}

function modelCategory(item) {
  const type = modelApiType(item);
  const modalities = item.catalog_metadata?.modalities || [];
  if (type === "images_generations") return "image";
  if (type === "video_generations") return "video";
  if (type.startsWith("audio_") || modalities.includes("audio")) return "audio";
  return "text";
}

function modelTypeBadge(item) {
  const type = modelApiType(item);
  const modalities = item.catalog_metadata?.modalities || [];
  const map = {
    images_generations: ["warning", "图像生成"],
    video_generations: ["neutral", "视频生成"],
    audio_speech: ["neutral", "语音生成"],
    audio_transcriptions: ["neutral", "语音识别"],
  };
  const [kind, label] = map[type] || (modalities.includes("image") ? ["success", "多模态对话"] : ["success", "文本对话"]);
  return `<span class="badge ${kind}">${label}</span>`;
}

function emptyRow(columns, label = "暂无数据") {
  return `<tr><td class="empty-row" colspan="${columns}">${escapeHtml(label)}</td></tr>`;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, {
    ...options,
    headers,
  });
  let data = {};
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem("token_admin_token");
      state.token = "";
      showAuth();
    }
    throw new Error(data.detail || `请求失败 (${response.status})`);
  }
  return data;
}

function toast(message, error = false) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove("show"), 2600);
}

function showAuth(message = "") {
  document.getElementById("app-shell").hidden = true;
  document.getElementById("auth-screen").hidden = false;
  document.getElementById("auth-error").textContent = message;
}

function showApp() {
  document.getElementById("auth-screen").hidden = true;
  document.getElementById("app-shell").hidden = false;
}

function renderMetrics(targetId, metrics) {
  document.getElementById(targetId).innerHTML = metrics.map((item) => `
    <article class="metric">
      <div><div class="metric-label">${escapeHtml(item.label)}</div><div class="metric-value" title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</div></div>
      <span class="metric-icon ${item.color || ""}"><i data-lucide="${item.icon}"></i></span>
    </article>
  `).join("");
  icons();
}

async function loadOverview() {
  const [overview, records, runtime] = await Promise.all([api("/admin/overview"), api("/admin/usage/records"), api("/admin/runtime")]);
  const environmentBadge = document.getElementById("environment-badge");
  environmentBadge.classList.toggle("real-environment", runtime.data_mode === "real");
  environmentBadge.classList.toggle("mock-environment", runtime.data_mode === "mock");
  environmentBadge.innerHTML = `<span class="status-dot"></span>${runtime.data_mode === "mock" ? "Mock 环境" : `真实环境 · ${escapeHtml(runtime.environment)}`}`;
  renderMetrics("overview-metrics", [
    { label: "账户余额", value: formatMoney(overview.total_balance_micros), icon: "wallet-cards" },
    { label: "累计请求", value: formatNumber(overview.request_count), icon: "send", color: "blue" },
    { label: "累计 Token", value: formatNumber(overview.total_tokens), icon: "binary", color: "orange" },
    { label: "累计消费", value: formatMoney(overview.amount_micros), icon: "receipt-text" },
  ]);
  const alerts = overview.alerts || [];
  const alertCount = document.getElementById("operational-alert-count");
  alertCount.textContent = alerts.length ? `${alerts.length} 项待处理` : "运行正常";
  alertCount.className = `badge ${alerts.some((item) => item.severity === "critical") ? "danger" : alerts.length ? "warning" : "success"}`;
  document.getElementById("operational-alerts").innerHTML = alerts.length ? alerts.map((item) => `
    <div class="operational-alert ${escapeHtml(item.severity)}">
      <span class="metric-icon"><i data-lucide="${item.severity === "critical" ? "triangle-alert" : item.severity === "warning" ? "circle-alert" : "clipboard-list"}"></i></span>
      <div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p><span>${escapeHtml(item.action || "请及时处理")}</span></div>
      ${item.release_blocking ? '<span class="badge danger">阻断发布</span>' : '<span class="badge neutral">运营待办</span>'}
    </div>
  `).join("") : '<div class="empty-state compact"><i data-lucide="circle-check-big"></i><span>当前没有需要处理的运营告警</span></div>';
  const recent = records.data.slice(0, 8);
  document.getElementById("recent-usage").innerHTML = recent.length ? recent.map((item) => `
    <tr>
      <td class="mono" title="${escapeHtml(item.request_id)}">${escapeHtml(shortId(item.request_id))}</td>
      <td>${escapeHtml(item.account_name)}</td>
      <td>${escapeHtml(item.model)}</td>
      <td>${formatNumber(item.total_tokens)}</td>
      <td>${formatMoney(item.amount_micros)}</td>
      <td>${statusBadge(item.status)}</td>
    </tr>
  `).join("") : emptyRow(6);
  document.getElementById("platform-status").innerHTML = [
    ["server", "API 服务", "正常"],
    ["route", `正式上架模型 · ${overview.published_model_count}`, overview.published_model_count ? "正常" : "待上架"],
    ["boxes", `候选模型 · ${overview.candidate_model_count}`, overview.candidate_model_count ? "待配置" : "暂无"],
    ["flask-conical", `Mock 可调用模型 · ${overview.mock_published_model_count}`, overview.mock_published_model_count ? "仅开发" : "暂无"],
    ["key-round", `供应商密钥 · ${(runtime.provider_credentials || []).filter((item) => item.configured).length}/${(runtime.provider_credentials || []).length}`, (runtime.provider_credentials || []).length === 0 || (runtime.provider_credentials || []).every((item) => item.configured) ? "正常" : "待配置"],
    ["key-round", `有效 Key · ${overview.active_key_count}`, overview.active_key_count ? "正常" : "待创建"],
    ["activity", `渠道健康 · ${overview.healthy_channel_count}/${overview.active_channel_count}`, overview.active_channel_count && overview.unhealthy_channel_count === 0 ? "正常" : "需检查"],
    ["database", "计费账本", "正常"],
  ].map(([icon, name, value]) => `<div class="status-row"><span class="status-name"><i data-lucide="${icon}"></i>${escapeHtml(name)}</span><span class="badge ${value === "正常" ? "success" : "warning"}">${value}</span></div>`).join("");
  icons();
}

async function loadAccounts() {
  const result = await api("/admin/accounts");
  state.accounts = result.data;
  document.getElementById("accounts-table").innerHTML = result.data.length ? result.data.map((item) => `
    <tr>
      <td><div class="primary-cell"><strong>${escapeHtml(item.name)}</strong><span class="secondary">ID ${item.id}</span></div></td>
      <td class="mono">${escapeHtml(item.external_user_id)}</td>
      <td>${formatNumber(item.api_key_count)}</td>
      <td><strong>${formatMoney(item.balance_micros)}</strong></td>
      <td>${activeBadge(item.active)}</td>
      <td class="align-right"><div class="table-actions">
        ${canOperate() ? `<button class="table-button" data-action="trial-link" data-id="${item.id}" data-name="${escapeHtml(item.name)}">试用链接</button>
        <button class="table-button" data-action="topup" data-id="${item.id}" data-name="${escapeHtml(item.name)}">充值</button>
        <button class="table-button" data-toggle="accounts" data-id="${item.id}" data-active="${!item.active}">${item.active ? "停用" : "启用"}</button>` : '<span class="secondary">只读</span>'}
      </div></td>
    </tr>
  `).join("") : emptyRow(6);
}

async function loadKeys() {
  const result = await api("/admin/api-keys");
  document.getElementById("keys-table").innerHTML = result.data.length ? result.data.map((item) => `
    <tr>
      <td><div class="primary-cell"><strong>${escapeHtml(item.name)}</strong><span class="secondary">ID ${item.id}</span></div></td>
      <td class="mono">${escapeHtml(item.key_prefix)}...</td>
      <td>${escapeHtml(item.account_name)}</td>
      <td>${activeBadge(item.active)}</td>
      <td>${formatDate(item.created_at)}</td>
      <td class="align-right">${canOperate() ? `<button class="table-button" data-toggle="api-keys" data-id="${item.id}" data-active="${!item.active}">${item.active ? "停用" : "启用"}</button>` : '<span class="secondary">只读</span>'}</td>
    </tr>
  `).join("") : emptyRow(6);
}

async function loadModels() {
  const result = await api("/admin/models");
  state.models = result.data;
  const providerSelect = document.getElementById("model-provider-filter");
  const selectedProvider = providerSelect.value;
  const providers = [...new Set(state.models.map((item) => item.catalog_metadata?.provider || "自定义").filter(Boolean))].sort();
  providerSelect.innerHTML = '<option value="">全部服务商</option>' + providers.map((provider) => `<option value="${escapeHtml(provider)}">${escapeHtml(provider)}</option>`).join("");
  providerSelect.value = selectedProvider;
  renderModels();
}

function renderModels() {
  const filters = state.modelFilters;
  const query = filters.query.trim().toLocaleLowerCase();
  document.querySelectorAll("[data-model-type]").forEach((button) => {
    const active = button.dataset.modelType === filters.type;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  document.querySelectorAll("[data-model-type-count]").forEach((counter) => {
    const type = counter.dataset.modelTypeCount;
    counter.textContent = type === "all" ? state.models.length : state.models.filter((item) => modelCategory(item) === type).length;
  });
  const models = state.models.filter((item) => {
    const provider = item.catalog_metadata?.provider || "自定义";
    const haystack = [item.public_name, item.upstream_model, item.catalog_metadata?.display_name, provider].join(" ").toLocaleLowerCase();
    return (!query || haystack.includes(query))
      && (!filters.provider || provider === filters.provider)
      && (!filters.type || modelCategory(item) === filters.type)
      && (!filters.publicationState || item.publication_state === filters.publicationState);
  });
  document.getElementById("model-result-count").textContent = `显示 ${models.length} / 共 ${state.models.length} 个模型`;
  document.getElementById("models-table").innerHTML = models.length ? models.map((item) => {
    const publishBlocked = item.publication_state === "blocked" && !item.active;
    const publishLabel = item.active ? "下架" : publishBlocked ? "完善后上架" : "上架";
    const publishTitle = publishBlocked ? ` title="${escapeHtml(item.publication_reasons.join("；"))}" disabled` : "";
    const apiType = modelApiType(item);
    const chatCompatible = apiType === "chat_completions";
    const provider = item.catalog_metadata?.provider || "自定义";
    return `
    <tr>
      <td><div class="primary-cell"><strong>${escapeHtml(item.catalog_metadata?.display_name || item.public_name)}</strong><span class="secondary mono">${escapeHtml(item.public_name)}</span><span class="secondary mono">上游：${escapeHtml(item.upstream_model)}</span></div></td>
      <td>${escapeHtml(provider)}</td>
      <td>${modelTypeBadge(item)}</td>
      <td>${chatCompatible ? formatTokenPricePerMillion(item.input_price_micros_per_1k) : "按任务计费"}</td>
      <td>${chatCompatible ? formatTokenPricePerMillion(item.output_price_micros_per_1k) : "-"}</td>
      <td>${formatNumber(item.channel_count)}</td>
      <td><span class="channel-health"><strong>${formatNumber(item.healthy_channel_count)}</strong> / ${formatNumber(item.channel_count)}</span></td>
      <td>${modelPublicationBadge(item)}</td>
      <td class="align-right">${canOperate() ? `<div class="table-actions">${isSuperadmin() ? `<button class="table-button" data-action="preflight-model" data-id="${item.id}" data-name="${escapeHtml(item.public_name)}" ${chatCompatible ? "" : 'disabled title="等待统一调用适配器"'}><i data-lucide="flask-conical"></i><span>预检</span></button>` : ""}<button class="table-button" data-action="edit-model-pricing" data-id="${item.id}" data-name="${escapeHtml(item.public_name)}" data-input-price="${item.input_price_micros_per_1k}" data-output-price="${item.output_price_micros_per_1k}" ${chatCompatible ? "" : 'disabled title="非 Token 计费尚未启用"'}><i data-lucide="receipt-text"></i><span>定价</span></button><button class="table-button" data-action="manage-channels" data-id="${item.id}" data-name="${escapeHtml(item.public_name)}"><i data-lucide="route"></i><span>渠道</span></button><button class="table-button" data-toggle="models" data-id="${item.id}" data-active="${!item.active}"${publishTitle}>${publishLabel}</button></div>` : '<span class="secondary">只读</span>'}</td>
    </tr>
  `; }).join("") : emptyRow(9, "没有符合筛选条件的模型");
  icons();
}

async function checkAllChannels() {
  const result = await api("/admin/models/health-check", { method: "POST", body: "{}" });
  toast(`已检测 ${result.checked} 个渠道：${result.healthy} 个健康，${result.unhealthy} 个异常`, result.unhealthy > 0);
  await loadModels();
}

async function loadPayments() {
  const result = await api("/admin/payment-orders");
  const orders = result.data;
  const paid = orders.filter((item) => item.status === "paid");
  const pending = orders.filter((item) => item.status === "pending");
  const refunded = orders.filter((item) => item.status === "refunded");
  renderMetrics("payment-metrics", [
    { label: "订单总数", value: formatNumber(orders.length), icon: "scroll-text" },
    { label: "待支付", value: formatNumber(pending.length), icon: "clock-3", color: "orange" },
    { label: "已入账", value: formatMoney(paid.reduce((sum, item) => sum + item.amount_micros, 0)), icon: "circle-check-big" },
    { label: "已退款", value: formatMoney(refunded.reduce((sum, item) => sum + item.amount_micros, 0)), icon: "rotate-ccw", color: "blue" },
  ]);
  document.getElementById("payments-table").innerHTML = orders.length ? orders.map((item) => `
    <tr>
      <td><div class="primary-cell"><strong class="mono">${escapeHtml(shortId(item.order_no))}</strong><span class="secondary">${escapeHtml(item.provider_order_id || "未生成渠道单号")}</span></div></td>
      <td>${escapeHtml(item.account_name)}</td>
      <td>${escapeHtml(item.provider)}</td>
      <td><strong>${formatMoney(item.amount_micros)}</strong></td>
      <td>${paymentBadge(item.status)}</td>
      <td>${formatDate(item.created_at)}</td>
      <td class="align-right"><div class="table-actions">
        ${item.status === "pending" && canOperate() ? `<button class="table-button" data-action="confirm-payment" data-id="${item.id}">确认支付</button>` : ""}
        ${item.status === "paid" && isSuperadmin() ? `<button class="table-button danger" data-action="refund-payment" data-id="${item.id}">退款</button>` : ""}
        ${item.status === "refunded" ? '<span class="secondary">已完成</span>' : ""}
      </div></td>
    </tr>
  `).join("") : emptyRow(7);
}

function redemptionStatus(item) {
  if (!item.active) return '<span class="badge neutral">已停用</span>';
  if (item.expires_at && new Date(item.expires_at) <= new Date()) return '<span class="badge warning">已过期</span>';
  if (item.redeemed_count >= item.max_redemptions) return '<span class="badge neutral">已领完</span>';
  return '<span class="badge success">可领取</span>';
}

async function loadRedemptions() {
  const result = await api("/admin/redemption-codes");
  document.getElementById("redemptions-table").innerHTML = result.data.length ? result.data.map((item) => `
    <tr>
      <td><div class="primary-cell"><strong>${escapeHtml(item.label)}</strong><span class="secondary">ID ${item.id}</span></div></td>
      <td class="mono">${escapeHtml(item.code_prefix)}...</td>
      <td><strong>${formatMoney(item.amount_micros)}</strong></td>
      <td>${formatNumber(item.redeemed_count)} / ${formatNumber(item.max_redemptions)}</td>
      <td>${item.expires_at ? formatDate(item.expires_at) : "长期有效"}</td>
      <td>${redemptionStatus(item)}</td>
      <td>${formatDate(item.created_at)}</td>
      <td class="align-right">${canOperate() ? `<button class="table-button" data-action="toggle-redemption" data-id="${item.id}" data-active="${!item.active}">${item.active ? "停用" : "启用"}</button>` : '<span class="secondary">只读</span>'}</td>
    </tr>
  `).join("") : emptyRow(8, "尚未创建兑换福利");
}

async function loadAudit() {
  const result = await api("/admin/audit-events");
  document.getElementById("audit-table").innerHTML = result.data.length ? result.data.map((item) => `
    <tr><td>${formatDate(item.created_at)}</td><td><div class="primary-cell"><strong>${escapeHtml(item.actor_type)}</strong><span class="secondary mono">${escapeHtml(item.actor_id)}</span></div></td><td class="mono">${escapeHtml(item.action)}</td><td>${escapeHtml(item.target_type)} · ${escapeHtml(item.target_id)}</td><td class="mono" title="${escapeHtml(JSON.stringify(item.details))}">${escapeHtml(JSON.stringify(item.details))}</td></tr>
  `).join("") : emptyRow(5, "暂无审计记录");
}

async function loadUsage() {
  const [summary, records] = await Promise.all([api("/admin/usage"), api("/admin/usage/records")]);
  renderMetrics("usage-metrics", [
    { label: "请求数", value: formatNumber(summary.request_count), icon: "send" },
    { label: "输入 Token", value: formatNumber(summary.input_tokens), icon: "arrow-down-to-line", color: "blue" },
    { label: "输出 Token", value: formatNumber(summary.output_tokens), icon: "arrow-up-from-line", color: "orange" },
    { label: "消费金额", value: formatMoney(summary.amount_micros), icon: "receipt-text" },
  ]);
  document.getElementById("usage-table").innerHTML = records.data.length ? records.data.map((item) => `
    <tr>
      <td>${formatDate(item.created_at)}</td>
      <td class="mono" title="${escapeHtml(item.request_id)}">${escapeHtml(shortId(item.request_id))}</td>
      <td><div class="primary-cell"><strong>${escapeHtml(item.account_name)}</strong><span class="secondary">${escapeHtml(item.api_key_name)}</span></div></td>
      <td>${escapeHtml(item.model)}</td>
      <td>${formatNumber(item.input_tokens)} / ${formatNumber(item.output_tokens)}</td>
      <td>${formatNumber(item.latency_ms)} ms</td>
      <td>${formatMoney(item.amount_micros)}</td>
      <td>${statusBadge(item.status)}</td>
    </tr>
  `).join("") : emptyRow(8);
}

async function providerBillsDialog() {
  const result = await api("/admin/provider-bills");
  const rows = result.data.map((item) => `<tr><td>${formatDate(item.created_at)}</td><td>${escapeHtml(item.provider)}</td><td>${escapeHtml(item.source_name)}</td><td>${item.matched_count} / ${item.mismatch_count} / ${item.unmatched_count}</td><td>${formatMoney(item.billed_cost_micros)}</td><td>${formatMoney(item.difference_micros)}</td></tr>`).join("") || emptyRow(6, "尚未导入供应商账单");
  openDialog("供应商成本账单核验", `<form id="provider-bill-form"><div class="dialog-body">
    <p class="dialog-copy">导入归一化 JSON，按供应商请求 ID 逐笔核对 Token 与成本。重复文件不会再次入账。</p>
    <div class="field-row"><div class="field"><label for="bill-provider">供应商</label><input id="bill-provider" name="provider" required maxlength="64" placeholder="例如 DeepSeek"></div><div class="field"><label for="bill-source">账单文件名</label><input id="bill-source" name="source_name" required maxlength="255" placeholder="deepseek-2026-08.json"></div></div>
    <div class="field"><label for="bill-lines">归一化账单行 JSON</label><textarea id="bill-lines" name="lines" required rows="8" placeholder='[{"provider_request_id":"...","input_tokens":10,"output_tokens":5,"billed_cost_micros":30}]'></textarea><small class="field-hint">成本单位为微元，1 元 = 1,000,000 微元。</small></div>
    <div class="table-wrap"><table><thead><tr><th>导入时间</th><th>供应商</th><th>来源</th><th>一致 / 差异 / 未匹配</th><th>账单成本</th><th>差额</th></tr></thead><tbody>${rows}</tbody></table></div>
  </div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button><button class="primary-button" type="submit"><i data-lucide="upload"></i><span>导入并核验</span></button></div></form>`);
  document.getElementById("provider-bill-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const lines = JSON.parse(form.get("lines"));
      if (!Array.isArray(lines) || !lines.length) throw new Error("账单行必须是非空 JSON 数组");
      const imported = await api("/admin/provider-bills/import", { method: "POST", body: JSON.stringify({ provider: form.get("provider"), source_name: form.get("source_name"), lines }) });
      const summary = imported.import;
      toast(imported.duplicate ? "该账单已导入，本次未重复处理" : `核验完成：${summary.matched_count} 条一致，${summary.mismatch_count} 条差异，${summary.unmatched_count} 条未匹配`, summary.mismatch_count + summary.unmatched_count > 0);
      await providerBillsDialog();
    } catch (error) { toast(error.message, true); }
  });
}

const loaders = { overview: loadOverview, accounts: loadAccounts, keys: loadKeys, models: loadModels, payments: loadPayments, redemptions: loadRedemptions, usage: loadUsage, audit: loadAudit };

async function switchView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  document.getElementById("page-title").textContent = titles[view];
  document.getElementById("admin-guide-link").hidden = view !== "overview";
  try { await loaders[view](); } catch (error) { toast(error.message, true); }
}

function closeAdminAccountMenu() {
  document.getElementById("admin-account-menu").hidden = true;
  document.getElementById("admin-account-trigger").setAttribute("aria-expanded", "false");
}

function adminPersonalSpaceDialog() {
  const roleLabels = { superadmin: "超级管理员", operator: "运营人员", auditor: "审计员" };
  const identity = state.identity || {};
  openDialog("个人空间", `<div class="dialog-body"><div class="account-profile-grid"><div><span>管理员账号</span><strong>${escapeHtml(identity.login_id || "-")}</strong></div><div><span>当前角色</span><strong>${escapeHtml(roleLabels[identity.role] || identity.role || "-")}</strong></div><div><span>最近登录</span><strong>${formatDate(identity.last_login_at)}</strong></div><div><span>账号状态</span><strong>${identity.active === false ? "已停用" : "正常"}</strong></div></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
}

function openDialog(title, content) {
  document.getElementById("dialog-title").textContent = title;
  document.getElementById("dialog-content").innerHTML = content;
  const dialog = document.getElementById("action-dialog");
  if (!dialog.open) dialog.showModal();
  icons();
}

function closeDialog() {
  document.getElementById("action-dialog").close();
}

function accountDialog() {
  openDialog("新建账户", `
    <form id="dialog-form">
      <div class="dialog-body">
        <div class="field"><label for="account-name">账户名称</label><input id="account-name" name="name" required maxlength="120"></div>
        <div class="field"><label for="external-user-id">loksystem 用户 ID</label><input id="external-user-id" name="external_user_id" required maxlength="120"></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">创建账户</button></div>
    </form>`);
  document.getElementById("dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try { await api("/admin/accounts", { method: "POST", body: JSON.stringify(data) }); closeDialog(); toast("账户已创建"); await loadAccounts(); } catch (error) { toast(error.message, true); }
  });
}

async function keyDialog() {
  if (!state.accounts.length) state.accounts = (await api("/admin/accounts")).data;
  if (!state.accounts.length) { toast("请先创建账户", true); return; }
  openDialog("生成 API Key", `
    <form id="dialog-form">
      <div class="dialog-body">
        <div class="field"><label for="key-name">Key 名称</label><input id="key-name" name="name" required maxlength="120"></div>
        <div class="field"><label for="key-account">所属账户</label><select id="key-account" name="account_id" required>${state.accounts.filter((item) => item.active).map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.external_user_id)}</option>`).join("")}</select></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">生成 Key</button></div>
    </form>`);
  document.getElementById("dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    data.account_id = Number(data.account_id);
    try {
      const result = await api("/admin/api-keys", { method: "POST", body: JSON.stringify(data) });
      openDialog("API Key 已生成", `<div class="dialog-body"><div class="field"><label>密钥</label><div class="secret-box mono" id="new-key-secret">${escapeHtml(result.key)}</div></div><div class="secret-actions"><button class="secondary-button" id="copy-key"><i data-lucide="copy"></i><span>复制</span></button></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
      document.getElementById("copy-key").addEventListener("click", async () => { await navigator.clipboard.writeText(result.key); toast("密钥已复制"); });
      await loadKeys();
    } catch (error) { toast(error.message, true); }
  });
}

function topupDialog(accountId, accountName) {
  openDialog(`充值 · ${accountName}`, `
    <form id="dialog-form">
      <div class="dialog-body">
        <div class="field"><label for="topup-amount">充值金额（元）</label><input id="topup-amount" name="amount" type="number" min="0.000001" step="0.01" required></div>
        <div class="field"><label for="topup-reference">业务流水号</label><input id="topup-reference" name="idempotency_key" value="ui-${Date.now()}" required maxlength="120"></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">确认充值</button></div>
    </form>`);
  document.getElementById("dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = { amount_micros: Math.round(Number(data.amount) * 1_000_000), idempotency_key: data.idempotency_key, description: "console topup" };
    try { await api(`/admin/accounts/${accountId}/balance`, { method: "POST", body: JSON.stringify(payload) }); closeDialog(); toast("充值已入账"); await loadAccounts(); } catch (error) { toast(error.message, true); }
  });
}

async function trialLinkDialog(accountId, accountName) {
  try {
    const result = await api("/admin/trial-links", {
      method: "POST",
      body: JSON.stringify({ account_id: Number(accountId) }),
    });
    openDialog(`试用链接 · ${accountName}`, `
      <div class="dialog-body">
        <div class="field"><label>用户中心链接</label><div class="secret-box mono" id="trial-portal-url">${escapeHtml(result.portal_url)}</div></div>
        <div class="field"><label>有效期至</label><div>${formatDate(result.expires_at)}</div></div>
        <div class="secret-actions"><button class="secondary-button" id="copy-trial-link"><i data-lucide="copy"></i><span>复制链接</span></button></div>
      </div>
      <div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button><a class="primary-button" href="${escapeHtml(result.portal_url)}" target="_blank" rel="noopener">打开用户中心</a></div>`);
    document.getElementById("copy-trial-link").addEventListener("click", async () => {
      await navigator.clipboard.writeText(result.portal_url);
      toast("试用链接已复制");
    });
  } catch (error) {
    toast(error.message, true);
  }
}

function modelDialog() {
  openDialog("添加模型", `
    <form id="dialog-form">
      <div class="dialog-body">
        <div class="field-row"><div class="field"><label for="public-name">公开名称</label><input id="public-name" name="public_name" required></div><div class="field"><label for="upstream-model">上游模型</label><input id="upstream-model" name="upstream_model" required></div></div>
        <div class="field"><label for="provider-url">供应商地址</label><input id="provider-url" name="provider_base_url" placeholder="http://localhost:4000/v1"></div>
        <div class="field-row"><div class="field"><label for="key-env">密钥环境变量</label><input id="key-env" name="provider_api_key_env" pattern="[A-Z][A-Z0-9_]{1,119}" placeholder="OPENAI_API_KEY"></div><div class="field"><label for="provider-key">供应商 API Key</label><input id="provider-key" name="provider_api_key" type="password" autocomplete="new-password" placeholder="可选，服务端加密保存"></div></div>
        <div class="field-row"><div class="field"><label for="input-price">输入价格 / 1M Token（元）</label><input id="input-price" name="input_price" type="number" min="0" step="0.001" value="0"></div><div class="field"><label for="output-price">输出价格 / 1M Token（元）</label><input id="output-price" name="output_price" type="number" min="0" step="0.001" value="0"></div></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">添加模型</button></div>
    </form>`);
  document.getElementById("dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = {
      public_name: data.public_name,
      upstream_model: data.upstream_model,
      provider_base_url: data.provider_base_url || null,
      provider_api_key_env: data.provider_api_key_env || null,
      provider_api_key: data.provider_api_key || null,
      input_price_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.input_price),
      output_price_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.output_price),
    };
    try { await api("/admin/models", { method: "POST", body: JSON.stringify(payload) }); closeDialog(); toast("模型已添加"); await loadModels(); } catch (error) { toast(error.message, true); }
  });
}

async function modelImportDialog() {
  let presets = [];
  try { presets = (await api("/admin/provider-presets")).data || []; } catch (error) { toast(error.message, true); return; }
  openDialog("批量接入模型", `
    <div class="dialog-body">
      <div class="field"><label for="provider-preset">主流服务商模板</label><select id="provider-preset"><option value="">自定义 OpenAI 兼容渠道</option>${presets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select><small class="field-hint">模板只创建停用的候选模型，不包含供应商密钥；DeepSeek V4 会自动带入按汇率换算的人民币参考价。</small></div>
      <form id="model-discovery-form" class="model-discovery-form">
        <div class="field"><label for="import-provider-url">上游兼容 API 地址</label><input id="import-provider-url" name="provider_base_url" required maxlength="500" placeholder="https://api.example.com/v1"></div>
        <div class="field"><label for="import-key-env">服务器密钥环境变量</label><input id="import-key-env" name="provider_api_key_env" maxlength="120" placeholder="OPENAI_API_KEY"><small class="field-hint">只填写已部署在 TOKEN 服务环境中的变量名，密钥不会提交到浏览器。</small></div>
        <div class="table-actions"><button class="secondary-button" type="submit"><i data-lucide="refresh-cw"></i><span>读取上游模型</span></button><button class="secondary-button" id="install-provider-preset" type="button" disabled><i data-lucide="package-plus"></i><span>安装模板候选</span></button></div>
      </form>
      <form id="model-import-form" hidden>
        <div class="field"><label>选择要公开的模型</label><div id="model-import-options" class="model-import-options"></div></div>
        <div class="field-row"><div class="field"><label for="import-name-prefix">公开名称前缀</label><input id="import-name-prefix" name="prefix" maxlength="30" placeholder="可选，例如 lok-"></div><div class="field"><label for="import-input-price">输入价格 / 1M Token（元）</label><input id="import-input-price" name="input_price" type="number" min="0" step="0.001" value="0" required></div></div>
        <div class="field"><label for="import-output-price">输出价格 / 1M Token（元）</label><input id="import-output-price" name="output_price" type="number" min="0" step="0.001" value="0" required></div>
        <p class="dialog-copy">导入后会为每个公开模型创建一个 Primary 渠道。可在渠道管理中继续添加备用上游和故障转移策略。</p>
        <div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="list-plus"></i><span>接入所选模型</span></button></div>
      </form>
    </div>`);
  const presetSelect = document.getElementById("provider-preset");
  const providerUrl = document.getElementById("import-provider-url");
  const providerKeyEnv = document.getElementById("import-key-env");
  const installPresetButton = document.getElementById("install-provider-preset");
  presetSelect.addEventListener("change", () => {
    const preset = presets.find((item) => item.id === presetSelect.value);
    providerUrl.value = preset?.base_url || "";
    providerKeyEnv.value = preset?.api_key_env || "";
    installPresetButton.disabled = !preset;
  });
  installPresetButton.addEventListener("click", async () => {
    const preset = presets.find((item) => item.id === presetSelect.value);
    if (!preset) return;
    try {
      const result = await api(`/admin/provider-presets/${preset.id}/install`, { method: "POST", body: JSON.stringify({ model_ids: preset.model_ids }) });
      closeDialog();
      toast(`已安装 ${result.data.length} 个停用候选，请配置密钥、启用渠道并执行预检`);
      await loadModels();
    } catch (error) { toast(error.message, true); }
  });
  const discoveryForm = document.getElementById("model-discovery-form");
  const importForm = document.getElementById("model-import-form");
  discoveryForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(discoveryForm));
    const params = new URLSearchParams({ provider_base_url: data.provider_base_url });
    if (data.provider_api_key_env) params.set("provider_api_key_env", data.provider_api_key_env);
    try {
      const result = await api(`/admin/upstream-models?${params.toString()}`);
      const options = result.data || [];
      if (!options.length) { toast("上游没有返回可用模型", true); return; }
      document.getElementById("model-import-options").innerHTML = options.map((item) => `<label><input type="checkbox" name="upstream_model" value="${escapeHtml(item.id)}"><span class="mono">${escapeHtml(item.id)}</span></label>`).join("");
      importForm.hidden = false;
      icons();
    } catch (error) { toast(error.message, true); }
  });
  importForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const source = Object.fromEntries(new FormData(discoveryForm));
    const data = new FormData(importForm);
    const upstreamModels = data.getAll("upstream_model");
    if (!upstreamModels.length) { toast("请至少选择一个模型", true); return; }
    const prefix = String(data.get("prefix") || "").trim();
    const inputPrice = yuanPerMillionToMicrosPerThousand(data.get("input_price"));
    const outputPrice = yuanPerMillionToMicrosPerThousand(data.get("output_price"));
    const payload = {
      provider_base_url: source.provider_base_url,
      provider_api_key_env: source.provider_api_key_env || null,
      models: upstreamModels.map((upstream_model) => ({
        public_name: `${prefix}${upstream_model}`,
        upstream_model,
        input_price_micros_per_1k: inputPrice,
        output_price_micros_per_1k: outputPrice,
      })),
    };
    try { const result = await api("/admin/models/batch", { method: "POST", body: JSON.stringify(payload) }); closeDialog(); toast(`已接入 ${result.data.length} 个模型`); await loadModels(); } catch (error) { toast(error.message, true); }
  });
}

function modelPricingDialog(modelId, modelName, inputPriceMicros, outputPriceMicros) {
  const model = state.models.find((item) => item.id === Number(modelId));
  const pricing = model?.official_pricing;
  const reference = pricing?.off_peak ? `
    <div class="field-hint">官网参考价（人民币 / 1M，已按 ${pricing.exchange_rate_usd_to_cny || "部署配置"} 汇率换算）</div>
    <div class="key-detail-grid"><div><span>缓存命中 · 低峰</span><strong>${formatMoney(pricing.off_peak.input_cache_hit_micros)}</strong></div><div><span>未命中 · 低峰</span><strong>${formatMoney(pricing.off_peak.input_cache_miss_micros)}</strong></div><div><span>输出 · 低峰</span><strong>${formatMoney(pricing.off_peak.output_micros)}</strong></div><div><span>来源</span><strong><a href="${escapeHtml(pricing.source_url)}" target="_blank" rel="noreferrer">DeepSeek 官网</a></strong></div></div>` : "";
  openDialog(`模型定价 · ${modelName}`, `
    <form id="model-pricing-form">
      <div class="dialog-body">
        <div class="field-row"><div class="field"><label for="model-input-price">输入价格 / 1M Token（元）</label><input id="model-input-price" name="input_price" type="number" min="0" step="0.001" value="${microsPerThousandToYuanPerMillion(inputPriceMicros)}" required></div><div class="field"><label for="model-output-price">输出价格 / 1M Token（元）</label><input id="model-output-price" name="output_price" type="number" min="0" step="0.001" value="${microsPerThousandToYuanPerMillion(outputPriceMicros)}" required></div></div>
        ${reference}
        <p class="dialog-copy">平台价格使用人民币；新价格会用于后续请求，已结算请求不会被修改。</p>
      </div>
      <div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="save"></i><span>保存定价</span></button></div>
    </form>`);
  document.getElementById("model-pricing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = { input_price_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.input_price), output_price_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.output_price) };
    try { await api(`/admin/models/${modelId}`, { method: "PATCH", body: JSON.stringify(payload) }); closeDialog(); toast("模型定价已更新"); await loadModels(); } catch (error) { toast(error.message, true); }
  });
}

async function channelDialog(modelId, modelName) {
  const result = await api(`/admin/models/${modelId}/channels`);
  state.channels = result.data;
  const rows = result.data.length ? result.data.map((item) => `
    <tr>
      <td><div class="primary-cell"><strong>${escapeHtml(item.name)}</strong><span class="secondary mono">${escapeHtml(item.upstream_model)}</span></div></td>
      <td><div class="primary-cell"><span>${escapeHtml(item.provider_base_url)}</span><span class="secondary">${escapeHtml(channelCredentialLabel(item))} · ${item.credentials_configured ? "密钥已配置" : "密钥未配置"}</span></div></td>
      <td>${item.priority}</td>
      <td>${item.weight}</td>
      <td><div class="primary-cell"><span>输入 ${item.provider_input_cost_micros_per_1k ? formatTokenPricePerMillion(item.provider_input_cost_micros_per_1k) : "未配置"}</span><span class="secondary">输出 ${item.provider_output_cost_micros_per_1k ? formatTokenPricePerMillion(item.provider_output_cost_micros_per_1k) : "未配置"}</span></div></td>
      <td>${channelStatusBadge(item.status)}<span class="secondary block-text">${item.health_source === "provider" ? "真实检测" : item.health_source === "mock" ? "Mock 检测" : "尚未检测"}</span>${item.circuit_open_until ? `<span class="secondary block-text">熔断至 ${formatDate(item.circuit_open_until)}</span>` : ""}</td>
      <td>${item.consecutive_failures}</td>
      <td class="align-right"><div class="table-actions"><button class="table-button" data-action="edit-channel" data-id="${item.id}" data-model-id="${modelId}" data-model-name="${escapeHtml(modelName)}"><i data-lucide="settings-2"></i><span>编辑</span></button><button class="table-button" data-action="check-channel" data-id="${item.id}" data-model-id="${modelId}" data-model-name="${escapeHtml(modelName)}"><i data-lucide="activity"></i><span>检测</span></button><button class="table-button" data-action="toggle-channel" data-id="${item.id}" data-active="${!item.active}" data-model-id="${modelId}" data-model-name="${escapeHtml(modelName)}">${item.active ? "停用" : "启用"}</button></div></td>
    </tr>
  `).join("") : emptyRow(8, "还没有可用渠道");
  openDialog(`渠道管理 · ${modelName}`, `
    <div class="channel-workspace">
      <div class="table-wrap channel-table"><table><thead><tr><th>渠道 / 上游</th><th>供应商地址</th><th>优先级</th><th>权重</th><th>供应商成本 / 1M Token</th><th>健康</th><th>失败</th><th class="align-right">操作</th></tr></thead><tbody>${rows}</tbody></table></div>
      <form id="channel-form" class="channel-form">
        <div class="section-header"><div><h2>新增渠道</h2><p>优先级数值越小越先尝试，同级按权重分配</p></div></div>
        <div class="dialog-body">
          <div class="field-row"><div class="field"><label for="channel-name">渠道名称</label><input id="channel-name" name="name" required maxlength="120" placeholder="例如：华东主线路"></div><div class="field"><label for="channel-upstream">上游模型</label><input id="channel-upstream" name="upstream_model" required maxlength="120"></div></div>
          <div class="field"><label for="channel-url">供应商地址</label><input id="channel-url" name="provider_base_url" required maxlength="500" placeholder="https://api.example.com/v1"></div>
          <div class="field-row"><div class="field"><label for="channel-key-env">密钥环境变量</label><input id="channel-key-env" name="provider_api_key_env" maxlength="120" pattern="[A-Z][A-Z0-9_]{1,119}" placeholder="DEEPSEEK_API_KEY"></div><div class="field"><label for="channel-provider-key">供应商 API Key</label><input id="channel-provider-key" name="provider_api_key" type="password" autocomplete="new-password" placeholder="可选，服务端加密保存"></div></div><small class="field-hint">二选一：填写环境变量名，或直接录入供应商 Key。Key 只在提交时传输，服务端加密保存且不会再次显示。</small>
          <div class="field"><label for="channel-priority">优先级</label><input id="channel-priority" name="priority" type="number" min="0" max="10000" value="100" required></div>
          <div class="field"><label for="channel-weight">同级权重</label><input id="channel-weight" name="weight" type="number" min="1" max="10000" value="100" required></div>
          <div class="field-row"><div class="field"><label for="channel-provider-input-cost">供应商输入成本 / 1M Token（元）</label><input id="channel-provider-input-cost" name="provider_input_cost" type="number" min="0" step="0.001" value="0"></div><div class="field"><label for="channel-provider-output-cost">供应商输出成本 / 1M Token（元）</label><input id="channel-provider-output-cost" name="provider_output_cost" type="number" min="0" step="0.001" value="0"></div></div>
          <small class="field-hint">填写供应商实际成本后，请求记录会计算平台毛利；留空或 0 表示暂不做成本对账。</small>
        </div>
        <div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button><button class="primary-button" type="submit"><i data-lucide="plus"></i><span>新增渠道</span></button></div>
      </form>
    </div>`);
  document.getElementById("channel-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = {
      name: data.name,
      upstream_model: data.upstream_model,
      provider_base_url: data.provider_base_url,
      provider_api_key_env: data.provider_api_key_env || null,
      provider_api_key: data.provider_api_key || null,
      priority: Number(data.priority),
      weight: Number(data.weight),
      provider_input_cost_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.provider_input_cost),
      provider_output_cost_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.provider_output_cost),
      active: true,
    };
    try {
      await api(`/admin/models/${modelId}/channels`, { method: "POST", body: JSON.stringify(payload) });
      toast("渠道已新增");
      await channelDialog(modelId, modelName);
      await loadModels();
    } catch (error) { toast(error.message, true); }
  });
}

function editChannelDialog(channelId, modelId, modelName) {
  const item = state.channels.find((channel) => channel.id === Number(channelId));
  if (!item) { toast("渠道数据已更新，请重新打开", true); return; }
  openDialog(`编辑渠道 · ${item.name}`, `
    <form id="channel-edit-form">
      <div class="dialog-body">
        <div class="field-row"><div class="field"><label for="edit-channel-name">渠道名称</label><input id="edit-channel-name" name="name" required maxlength="120" value="${escapeHtml(item.name)}"></div><div class="field"><label for="edit-channel-upstream">上游模型</label><input id="edit-channel-upstream" name="upstream_model" required maxlength="120" value="${escapeHtml(item.upstream_model)}"></div></div>
        <div class="field"><label for="edit-channel-url">供应商地址</label><input id="edit-channel-url" name="provider_base_url" required maxlength="500" value="${escapeHtml(item.provider_base_url)}"></div>
        <div class="field-row"><div class="field"><label for="edit-channel-key-env">密钥环境变量</label><input id="edit-channel-key-env" name="provider_api_key_env" maxlength="120" pattern="[A-Z][A-Z0-9_]{1,119}" value="${escapeHtml(item.provider_api_key_env || "")}"></div><div class="field"><label for="edit-channel-provider-key">供应商 API Key</label><input id="edit-channel-provider-key" name="provider_api_key" type="password" placeholder="留空则保持当前密钥"></div></div><small class="field-hint">当前密钥不会回显；输入新 Key 会覆盖并加密保存。</small>
        <div class="field"><label for="edit-channel-priority">优先级</label><input id="edit-channel-priority" name="priority" type="number" min="0" max="10000" value="${item.priority}" required></div><label class="check-field"><input name="clear_provider_api_key" type="checkbox"><span>清除控制台托管密钥</span></label>
        <div class="field-row"><div class="field"><label for="edit-channel-weight">同级权重</label><input id="edit-channel-weight" name="weight" type="number" min="1" max="10000" value="${item.weight}" required></div><label class="check-field"><input name="active" type="checkbox" ${item.active ? "checked" : ""}><span>启用此渠道</span></label></div>
        <div class="field-row"><div class="field"><label for="edit-channel-provider-input-cost">供应商输入成本 / 1M Token（元）</label><input id="edit-channel-provider-input-cost" name="provider_input_cost" type="number" min="0" step="0.001" value="${microsPerThousandToYuanPerMillion(item.provider_input_cost_micros_per_1k || 0)}"></div><div class="field"><label for="edit-channel-provider-output-cost">供应商输出成本 / 1M Token（元）</label><input id="edit-channel-provider-output-cost" name="provider_output_cost" type="number" min="0" step="0.001" value="${microsPerThousandToYuanPerMillion(item.provider_output_cost_micros_per_1k || 0)}"></div></div>
      </div>
      <div class="dialog-actions"><button class="secondary-button" type="button" data-action="manage-channels" data-id="${modelId}" data-name="${escapeHtml(modelName)}"><i data-lucide="arrow-left"></i><span>返回</span></button><button class="primary-button" type="submit"><i data-lucide="save"></i><span>保存</span></button></div>
    </form>`);
  document.getElementById("channel-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = {
      name: data.name,
      upstream_model: data.upstream_model,
      provider_base_url: data.provider_base_url,
      provider_api_key_env: data.provider_api_key_env || null,
      provider_api_key: data.provider_api_key || null,
      priority: Number(data.priority),
      weight: Number(data.weight),
      provider_input_cost_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.provider_input_cost),
      provider_output_cost_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.provider_output_cost),
      active: data.active === "on",
      clear_provider_api_key: data.clear_provider_api_key === "on",
    };
    try {
      await api(`/admin/channels/${channelId}`, { method: "PATCH", body: JSON.stringify(payload) });
      toast("渠道配置已保存");
      await channelDialog(modelId, modelName);
      await loadModels();
    } catch (error) { toast(error.message, true); }
  });
}

async function checkChannel(channelId, modelId, modelName) {
  try {
    const result = await api(`/admin/channels/${channelId}/check`, { method: "POST", body: "{}" });
    toast(result.healthy ? `检测通过，${result.latency_ms} ms` : result.detail, !result.healthy);
    await channelDialog(modelId, modelName);
    await loadModels();
  } catch (error) { toast(error.message, true); }
}

async function toggleChannel(channelId, active, modelId, modelName) {
  try {
    await api(`/admin/channels/${channelId}`, { method: "PATCH", body: JSON.stringify({ active }) });
    toast(active ? "渠道已启用" : "渠道已停用");
    await channelDialog(modelId, modelName);
    await loadModels();
  } catch (error) { toast(error.message, true); }
}

async function paymentDialog() {
  const [accountResult, providerResult] = await Promise.all([api("/admin/accounts"), api("/admin/payment-providers")]);
  state.accounts = accountResult.data;
  const activeAccounts = state.accounts.filter((item) => item.active);
  const providers = providerResult.data;
  if (!activeAccounts.length) { toast("请先创建有效账户", true); return; }
  openDialog("创建充值订单", `
    <form id="dialog-form">
      <div class="dialog-body">
        <div class="field"><label for="payment-account">充值账户</label><select id="payment-account" name="account_id" required>${activeAccounts.map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.external_user_id)}</option>`).join("")}</select></div>
        <div class="field-row"><div class="field"><label for="payment-amount">订单金额（元）</label><input id="payment-amount" name="amount" type="number" min="0.01" step="0.01" required></div><div class="field"><label for="payment-provider">支付渠道</label><select id="payment-provider" name="provider">${providers.map((item) => `<option value="${escapeHtml(item.id)}" ${item.available ? "" : "disabled"}>${escapeHtml(item.name)}${item.available ? "" : " · 未接入"}</option>`).join("")}</select></div></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">创建订单</button></div>
    </form>`);
  document.getElementById("dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = { account_id: Number(data.account_id), amount_micros: Math.round(Number(data.amount) * 1_000_000), provider: data.provider };
    try { await api("/admin/payment-orders", { method: "POST", body: JSON.stringify(payload) }); closeDialog(); toast("订单已创建"); await loadPayments(); } catch (error) { toast(error.message, true); }
  });
}

function redemptionDialog() {
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 16);
  openDialog("创建兑换码", `
    <form id="redemption-form">
      <div class="dialog-body">
        <div class="field"><label for="redemption-label">福利名称</label><input id="redemption-label" name="label" required maxlength="120" placeholder="例如：LokSystem 内测福利"></div>
        <div class="field-row"><div class="field"><label for="redemption-amount">额度（元）</label><input id="redemption-amount" name="amount" type="number" min="0.000001" step="0.01" required></div><div class="field"><label for="redemption-max">最大领取次数</label><input id="redemption-max" name="max_redemptions" type="number" min="1" max="100000" value="1" required></div></div>
        <div class="field"><label for="redemption-expiry">过期时间</label><input id="redemption-expiry" name="expires_at" type="datetime-local" min="${tomorrow}"><small class="field-hint">留空表示长期有效。</small></div>
        <div class="field"><label for="redemption-code">自定义兑换码</label><input id="redemption-code" name="code" minlength="8" maxlength="120" placeholder="留空则安全随机生成"><small class="field-hint">完整兑换码只会在创建成功后显示一次。</small></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="gift"></i><span>创建兑换码</span></button></div>
    </form>`);
  document.getElementById("redemption-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = { label: data.label, amount_micros: Math.round(Number(data.amount) * 1_000_000), max_redemptions: Number(data.max_redemptions) };
    if (data.code) payload.code = data.code;
    if (data.expires_at) payload.expires_at = new Date(data.expires_at).toISOString();
    try {
      const result = await api("/admin/redemption-codes", { method: "POST", body: JSON.stringify(payload) });
      openDialog("兑换码创建成功", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="triangle-alert"></i><span>完整兑换码只展示这一次。关闭窗口后无法再次查看。</span></div><div class="field"><label>完整兑换码</label><div class="secret-box mono" id="new-redemption-secret">${escapeHtml(result.code)}</div></div><div class="secret-actions"><button class="secondary-button" id="copy-redemption-code"><i data-lucide="copy"></i><span>复制兑换码</span></button></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>我已保存</button></div>`);
      document.getElementById("copy-redemption-code").addEventListener("click", async () => { await navigator.clipboard.writeText(result.code); toast("兑换码已复制"); });
      await loadRedemptions();
    } catch (error) { toast(error.message, true); }
  });
}

async function toggleRedemption(codeId, active) {
  try { await api(`/admin/redemption-codes/${codeId}`, { method: "PATCH", body: JSON.stringify({ active }) }); toast(active ? "兑换码已启用" : "兑换码已停用"); await loadRedemptions(); } catch (error) { toast(error.message, true); }
}

async function confirmPayment(orderId) {
  if (!window.confirm("确认该订单已完成支付并入账？")) return;
  try { await api(`/admin/payment-orders/${orderId}/confirm`, { method: "POST", body: "{}" }); toast("支付已确认，余额已入账"); await loadPayments(); } catch (error) { toast(error.message, true); }
}

async function refundPayment(orderId) {
  if (!window.confirm("确认全额退款？账户余额将同步扣减。")) return;
  try { await api(`/admin/payment-orders/${orderId}/refund`, { method: "POST", body: "{}" }); toast("订单已退款"); await loadPayments(); } catch (error) { toast(error.message, true); }
}

async function preflightModel(modelId, modelName) {
  openDialog(`模型预发布检查 · ${modelName}`, `
    <form id="preflight-form"><div class="dialog-body">
      <p class="dialog-copy">会先验证渠道健康和定价。调用探针会向上游发送一条短请求，可能产生供应商费用。</p>
      <label class="check-field"><input name="chat_probe" type="checkbox"><span>执行非流式调用探针</span></label>
      <label class="check-field"><input name="stream_probe" type="checkbox"><span>执行流式响应探针</span></label>
    </div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="flask-conical"></i><span>开始检查</span></button></div></form>`);
  document.getElementById("preflight-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const result = await api(`/admin/models/${modelId}/preflight`, { method: "POST", body: JSON.stringify({ chat_probe: form.get("chat_probe") === "on", stream_probe: form.get("stream_probe") === "on" }) });
      const health = result.channel_health.map((item) => `${escapeHtml(item.name)}：${item.healthy ? "健康" : "异常"}`).join("<br>") || "没有启用的渠道";
      const streamUsage = result.stream_probe ? (result.stream_probe.token_usage_reported ? `流式 Token：${result.stream_probe.input_tokens} / ${result.stream_probe.output_tokens}` : "流式 Token：上游未返回 usage，不能作为计费发布依据") : "流式 Token：未执行";
      const blockers = result.publication_reasons?.length ? `<div class="callout warning"><strong>暂不可上架</strong><span>${escapeHtml(result.publication_reasons.join("；"))}</span></div>` : `<div class="callout success"><strong>已满足上架前置条件</strong><span>可以执行上架操作。</span></div>`;
      openDialog(`预检结果 · ${modelName}`, `<div class="dialog-body">${blockers}<div class="key-detail-grid"><div><span>发布状态</span><strong>${escapeHtml(result.publication_state || "未知")}</strong></div><div><span>定价</span><strong>${result.price_configured ? "已配置" : "未配置"}</strong></div><div><span>非流式</span><strong>${result.chat_probe ? (result.chat_probe.ok ? "通过" : "失败") : "未执行"}</strong></div><div><span>流式</span><strong>${result.stream_probe ? (result.stream_probe.ok ? "通过" : "失败") : "未执行"}</strong></div></div><p class="dialog-copy">${escapeHtml(streamUsage)}<br>渠道检查<br>${health}</p></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
    } catch (error) { toast(error.message, true); }
  });
}

async function reconcileLedger() {
  try {
    const result = await api("/admin/reconciliation");
    openDialog("账本对账结果", `<div class="dialog-body"><div class="key-detail-grid"><div><span>对账状态</span><strong>${result.ok ? "一致" : "发现差异"}</strong></div><div><span>余额差异</span><strong>${result.balance_mismatch_count}</strong></div><div><span>订单差异</span><strong>${result.order_issue_count}</strong></div></div><p class="dialog-copy">${result.ok ? "账户余额、订单和账本记录一致。" : "请先处理差异，再执行下一次运营结算。"}</p></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
  } catch (error) { toast(error.message, true); }
}

async function manageAdmins() {
  try {
    const result = await api("/admin/users");
    const rows = result.data.map((item) => `<tr><td>${escapeHtml(item.login_id)}</td><td>${escapeHtml(item.role)}</td><td>${activeBadge(item.active)}</td><td>${formatDate(item.last_login_at)}</td></tr>`).join("") || emptyRow(4);
    openDialog("管理员与角色", `<div class="dialog-body"><p class="dialog-copy">超级管理员拥有全量权限；运营人员可管理模型、账户与订单；审计员仅可查看运营数据。</p><div class="table-wrap"><table><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>最近登录</th></tr></thead><tbody>${rows}</tbody></table></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button><button class="primary-button" type="button" id="create-admin-user"><i data-lucide="user-plus"></i><span>新增管理员</span></button></div>`);
    document.getElementById("create-admin-user").addEventListener("click", () => adminUserDialog());
  } catch (error) { toast(error.message, true); }
}

function adminUserDialog() {
  openDialog("新增管理员", `<form id="admin-user-form"><div class="dialog-body"><div class="field"><label for="new-admin-login">管理员账号</label><input id="new-admin-login" name="login_id" required minlength="3" maxlength="160"></div><div class="field"><label for="new-admin-password">初始密码</label><input id="new-admin-password" name="password" type="password" required minlength="8"></div><div class="field"><label for="new-admin-role">角色</label><select id="new-admin-role" name="role"><option value="operator">运营人员</option><option value="auditor">审计员</option><option value="superadmin">超级管理员</option></select></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-action="manage-admins">返回</button><button class="primary-button" type="submit">创建</button></div></form>`);
  document.getElementById("admin-user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try { await api("/admin/users", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); toast("管理员已创建"); await manageAdmins(); } catch (error) { toast(error.message, true); }
  });
}

async function toggleEntity(kind, id, active) {
  try {
    await api(`/admin/${kind}/${id}`, { method: "PATCH", body: JSON.stringify({ active }) });
    toast(active ? "已启用" : "已停用");
    await loaders[state.view]();
  } catch (error) { toast(error.message, true); }
}

document.addEventListener("DOMContentLoaded", async () => {
  icons();
  document.getElementById("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await fetch("/admin/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
      const data = await result.json();
      if (!result.ok) throw new Error(data.detail || "管理员登录失败");
      state.token = data.access_token;
      setAdminIdentity(data.admin);
      sessionStorage.setItem("token_admin_token", state.token);
      showApp();
      await switchView("overview");
    } catch (error) { showAuth(error.message); }
  });
  document.getElementById("toggle-token").addEventListener("click", () => {
    const input = document.getElementById("admin-password");
    input.type = input.type === "password" ? "text" : "password";
  });
  document.getElementById("admin-account-trigger").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = document.getElementById("admin-account-menu");
    menu.hidden = !menu.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.getElementById("admin-personal-space").addEventListener("click", () => { closeAdminAccountMenu(); adminPersonalSpaceDialog(); });
  document.getElementById("logout-button").addEventListener("click", () => {
    api("/admin/auth/logout", { method: "POST", body: "{}" }).catch(() => {});
    sessionStorage.removeItem("token_admin_token"); sessionStorage.removeItem("token_admin_role"); state.token = ""; state.role = ""; state.identity = null; closeAdminAccountMenu(); document.getElementById("admin-password").value = ""; showAuth();
  });
  document.getElementById("refresh-button").addEventListener("click", () => switchView(state.view));
  document.getElementById("model-search").addEventListener("input", (event) => { state.modelFilters.query = event.target.value; renderModels(); });
  document.getElementById("model-provider-filter").addEventListener("change", (event) => { state.modelFilters.provider = event.target.value; renderModels(); });
  document.querySelectorAll("[data-model-type]").forEach((button) => button.addEventListener("click", () => { state.modelFilters.type = button.dataset.modelType; renderModels(); }));
  document.getElementById("model-state-filter").addEventListener("change", (event) => { state.modelFilters.publicationState = event.target.value; renderModels(); });
  document.getElementById("close-dialog").addEventListener("click", closeDialog);
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));
  document.body.addEventListener("click", (event) => {
    if (!event.target.closest(".account-menu")) closeAdminAccountMenu();
    const target = event.target.closest("button, [data-go]");
    if (!target) return;
    if (target.dataset.close !== undefined) closeDialog();
    if (target.dataset.go) switchView(target.dataset.go);
    if (target.dataset.action === "create-account") accountDialog();
    if (target.dataset.action === "create-key") keyDialog().catch((error) => toast(error.message, true));
    if (target.dataset.action === "create-model") modelDialog();
    if (target.dataset.action === "health-check-all") checkAllChannels().catch((error) => toast(error.message, true));
    if (target.dataset.action === "preflight-model") preflightModel(target.dataset.id, target.dataset.name);
    if (target.dataset.action === "import-models") modelImportDialog().catch((error) => toast(error.message, true));
    if (target.dataset.action === "edit-model-pricing") modelPricingDialog(target.dataset.id, target.dataset.name, target.dataset.inputPrice, target.dataset.outputPrice);
    if (target.dataset.action === "manage-channels") channelDialog(target.dataset.id, target.dataset.name).catch((error) => toast(error.message, true));
    if (target.dataset.action === "edit-channel") editChannelDialog(target.dataset.id, target.dataset.modelId, target.dataset.modelName);
    if (target.dataset.action === "check-channel") checkChannel(target.dataset.id, target.dataset.modelId, target.dataset.modelName);
    if (target.dataset.action === "toggle-channel") toggleChannel(target.dataset.id, target.dataset.active === "true", target.dataset.modelId, target.dataset.modelName);
    if (target.dataset.action === "create-payment") paymentDialog().catch((error) => toast(error.message, true));
    if (target.dataset.action === "create-redemption") redemptionDialog();
    if (target.dataset.action === "toggle-redemption") toggleRedemption(target.dataset.id, target.dataset.active === "true");
    if (target.dataset.action === "confirm-payment") confirmPayment(target.dataset.id);
    if (target.dataset.action === "refund-payment") refundPayment(target.dataset.id);
    if (target.dataset.action === "reconcile-ledger") reconcileLedger();
    if (target.dataset.action === "provider-bills") providerBillsDialog().catch((error) => toast(error.message, true));
    if (target.dataset.action === "manage-admins") manageAdmins();
    if (target.dataset.action === "trial-link") trialLinkDialog(target.dataset.id, target.dataset.name);
    if (target.dataset.action === "topup") topupDialog(target.dataset.id, target.dataset.name);
    if (target.dataset.toggle) toggleEntity(target.dataset.toggle, target.dataset.id, target.dataset.active === "true");
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAdminAccountMenu(); });
  if (state.token) {
    try { const identity = await api("/admin/auth/me"); setAdminIdentity(identity.admin); showApp(); await switchView("overview"); } catch (_) { showAuth("管理员会话已失效，请重新登录"); }
  } else {
    showAuth();
  }
});
