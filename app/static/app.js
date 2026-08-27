const state = {
  token: sessionStorage.getItem("token_admin_token") || "",
  role: sessionStorage.getItem("token_admin_role") || "",
  view: "overview",
  accounts: [],
  models: [],
  providerConnections: [],
  providerPresets: [],
  channels: [],
  identity: null,
  accountAccessTab: "accounts",
  modelFilters: { query: "", provider: "", type: "", publicationState: "" },
  modelProviderDetail: "",
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
    "create-account", "create-key", "health-check-all", "create-model",
    "create-payment", "create-redemption", "trial-link", "resend-invitation", "topup", "confirm-payment",
    "refund-payment", "toggle-redemption", "edit-model-pricing", "manage-channels",
    "preflight-model", "delete-model", "edit-channel", "check-channel", "toggle-channel", "toggle-entity",
  ] : [
    "manage-admins", "refund-payment", "preflight-model", "delete-model", "delete-account",
  ]);
  document.querySelectorAll("[data-action]").forEach((element) => {
    element.hidden = hiddenActions.has(element.dataset.action);
  });
  const roleOnlyControls = {
    "manage-admins": isSuperadmin(),
    "create-account": canOperate(),
    "create-key": canOperate(),
    "health-check-all": canOperate(),
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
  accounts: "账户与访问",
  payments: "订单管理",
  redemptions: "福利管理",
  usage: "用量管理",
  audit: "安全审计",
};

const adminViewFromUrl = () => {
  const view = new URLSearchParams(window.location.search).get("view");
  if (view === "keys") return "accounts";
  return Object.prototype.hasOwnProperty.call(titles, view) ? view : "overview";
};

const accountAccessTabFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get("view") === "keys" || params.get("tab") === "keys" ? "keys" : "accounts";
};

function adminViewUrl(view) {
  const params = new URLSearchParams(window.location.search);
  params.set("view", view);
  if (view === "accounts" && state.accountAccessTab === "keys") params.set("tab", "keys");
  else params.delete("tab");
  return `${window.location.pathname}?${params.toString()}`;
}

function primeAdminHistory(view) {
  if (window.history.state?.app === "admin" && window.history.state.view === view) return;
  const url = adminViewUrl(view);
  window.history.replaceState({ app: "admin", view }, "", url);
  window.history.pushState({ app: "admin", view }, "", url);
}

function updateAdminHistory(view, mode = "push") {
  if (mode === "none") return;
  const current = window.history.state;
  if (mode === "replace" || current?.app !== "admin" || current.view !== view) {
    window.history[mode === "replace" ? "replaceState" : "pushState"]({ app: "admin", view }, "", adminViewUrl(view));
  }
}

function navigateAdminBack() {
  if (state.view === "overview") return;
  if (window.history.state?.app === "admin") window.history.back();
  else switchView("overview");
}
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
  const reference = pricing?.default_reference || (pricing?.off_peak ? {
    input_micros: pricing.off_peak.input_cache_miss_micros,
    output_micros: pricing.off_peak.output_micros,
  } : null);
  if (!reference) return "-";
  const input = reference.input_micros;
  const output = reference.output_micros;
  return `${formatMoney(input)} / ${formatMoney(output)}`;
}

function officialTokenReference(pricing) {
  if (pricing?.default_reference?.input_micros > 0 && pricing?.default_reference?.output_micros > 0) return pricing.default_reference;
  if (pricing?.off_peak?.input_cache_miss_micros > 0 && pricing?.off_peak?.output_micros > 0) {
    return { input_micros: pricing.off_peak.input_cache_miss_micros, output_micros: pricing.off_peak.output_micros };
  }
  return null;
}

function formatTokenBound(value) {
  const tokens = Number(value || 0);
  if (tokens >= 1_000_000) return `${tokens / 1_000_000}M`;
  if (tokens >= 1_000) return `${tokens / 1_000}K`;
  return String(tokens);
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

function accountSourceBadge(item) {
  const source = item.account_source || "admin";
  const kind = source === "self_registered" ? "success" : source === "loksystem" || source === "oidc" ? "warning" : "neutral";
  const label = item.account_source_label || ({ self_registered: "用户注册", loksystem: "外部身份接入", oidc: "统一身份接入", admin: "管理员创建" }[source] || "管理员创建");
  return `<span class="badge ${kind}" title="${escapeHtml(source)}">${escapeHtml(label)}</span>`;
}

function accountTypeBadge(item) {
  return `<span class="badge neutral">${escapeHtml(item.access_mode_label || item.account_type || "API 服务账户")}</span>`;
}

function accountAccessBadge(item) {
  const kinds = { api_ready: "success", portal_ready: "success", invitation_pending: "warning", invitation_expired: "error", inactive: "neutral" };
  return `<span class="badge ${kinds[item.access_status] || "warning"}">${escapeHtml(item.access_status_label || "待配置")}</span>`;
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
    unavailable: ["warning", "未开放"],
    pending_adapter: ["neutral", "待适配"],
    misconfigured: ["error", "配置错误"],
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

function modelTypeLabels(item) {
  const type = modelApiType(item);
  const modalities = item.catalog_metadata?.modalities || [];
  if (type === "images_generations") return ["图像生成"];
  if (type === "video_generations") return ["视频生成"];
  if (type === "audio_speech") return ["语音合成"];
  if (type === "audio_transcriptions") return ["语音识别"];
  if (type.startsWith("audio_") || modalities.includes("audio")) return ["语音"];
  const labels = [];
  if (modalities.includes("text") || !modalities.length) labels.push("文本");
  if (modalities.includes("image")) labels.push("图像");
  if (modalities.includes("video")) labels.push("视频");
  return labels.length ? labels : ["文本"];
}

function modelPriceText(value) {
  return Number(value || 0) > 0 ? formatTokenPricePerMillion(value) : "待核价";
}

function formatMaxOutputTokens(value, fallback = "按上游配置") {
  const tokens = Number(value || 0);
  if (!tokens) return fallback;
  if (tokens >= 1_000_000) return `${tokens / 1_000_000}M`;
  const kiloTokens = tokens / 1_000;
  return `${Number.isInteger(kiloTokens) ? kiloTokens : kiloTokens.toFixed(1)}K`;
}

function modelCardIcon(item) {
  if (modelApiType(item) === "images_generations" || (item.catalog_metadata?.modalities || []).includes("image")) return "image";
  if (modelApiType(item) === "video_generations" || (item.catalog_metadata?.modalities || []).includes("video")) return "video";
  if ((item.catalog_metadata?.capabilities || []).some((value) => String(value).includes("推理"))) return "brain-circuit";
  return "message-square-text";
}

async function modelAdminDetailDialog(modelId) {
  const item = state.models.find((model) => String(model.id) === String(modelId));
  if (!item) return;
  let history = [];
  let channels = [];
  try {
    const [historyResult, channelResult] = await Promise.all([api(`/admin/models/${item.id}/history`), api(`/admin/models/${item.id}/channels`)]);
    history = historyResult.data || [];
    channels = channelResult.data || [];
  } catch (_) { /* detail view remains useful when history is unavailable */ }
  const metadata = item.catalog_metadata || {};
  const pricing = item.official_pricing || {};
  const profile = metadata.gateway_profile || {};
  const parameterTags = (metadata.supported_parameters || []).map((value) => `<span>${escapeHtml(value)}</span>`).join("") || '<span class="empty">按上游配置</span>';
  const capabilityTags = (metadata.capabilities || []).map((value) => `<span>${escapeHtml(value)}</span>`).join("") || '<span class="empty">待补充</span>';
  const healthRows = channels.map((channel) => `<div class="admin-model-health-row"><strong>${escapeHtml(channel.name)}</strong><span>${escapeHtml(channel.status)} · ${formatNumber(channel.last_latency_ms || 0)} ms</span><small>${escapeHtml(channel.last_error || channel.last_checked_at || "尚未检测")}</small></div>`).join("") || '<p class="field-hint">暂无渠道明细</p>';
  const historyRows = history.slice(0, 8).map((record) => `<div class="admin-model-history-row"><strong>${escapeHtml(record.change_type)}</strong><span>${escapeHtml((record.changed_fields || []).join("、") || "状态")}</span><small>${escapeHtml(record.created_at || "")}</small></div>`).join("") || '<p class="field-hint">暂无变更记录</p>';
  const priceRows = pricing.off_peak || pricing.peak ? `
    <div class="admin-model-price-table"><div><span>价格时段</span><span>缓存命中输入</span><span>未命中输入</span><span>输出</span></div>
      ${["off_peak", "peak"].filter((period) => pricing[period]).map((period) => `<div><strong>${period === "off_peak" ? "低峰" : "高峰"}</strong><span>${modelPriceText(pricing[period].input_cache_hit_micros ? Math.round(pricing[period].input_cache_hit_micros / 1000) : 0)}</span><span>${modelPriceText(pricing[period].input_cache_miss_micros ? Math.round(pricing[period].input_cache_miss_micros / 1000) : 0)}</span><span>${modelPriceText(pricing[period].output_micros ? Math.round(pricing[period].output_micros / 1000) : 0)}</span></div>`).join("")}
    </div>` : '<p class="field-hint">暂无已核验的官方价格阶梯，当前平台价格需要人工确认。</p>';
  openDialog(`模型详情 · ${metadata.display_name || item.public_name}`, `
    <div class="dialog-body admin-model-detail-dialog">
      <div class="admin-model-detail-heading"><div><span class="eyebrow">${escapeHtml(metadata.provider || "自定义")}</span><h3>${escapeHtml(metadata.display_name || item.public_name)}</h3><code>${escapeHtml(item.public_name)}</code></div>${modelPublicationBadge(item)}</div>
      <p class="dialog-copy">${escapeHtml(metadata.summary || "暂无模型说明")}</p>
      <div class="admin-model-detail-grid"><div><span>模型类型</span><strong>${escapeHtml(modelTypeLabels(item).join(" · "))}</strong></div><div><span>上下文长度</span><strong>${escapeHtml(metadata.context_window || "按上游配置")}</strong></div><div><span>最大输出</span><strong>${escapeHtml(formatMaxOutputTokens(metadata.max_output_tokens))}</strong></div><div><span>渠道健康</span><strong>${formatNumber(item.healthy_channel_count)} / ${formatNumber(item.channel_count)}</strong></div><div><span>调用协议</span><strong>${escapeHtml(profile.protocol || modelApiType(item))}</strong></div><div><span>上游模型</span><strong class="mono">${escapeHtml(item.upstream_model)}</strong></div></div>
      <section class="admin-model-detail-section"><h4>能力</h4><div class="admin-model-tags">${capabilityTags}</div></section>
      <section class="admin-model-detail-section"><h4>支持参数</h4><div class="admin-model-tags">${parameterTags}</div></section>
      <section class="admin-model-detail-section"><h4>官方价格参考</h4>${pricing.source_url ? `<a class="admin-model-source" href="${escapeHtml(pricing.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(pricing.source || "查看官方价格")}</a>` : ""}${priceRows}</section>
      <section class="admin-model-detail-section"><h4>渠道健康明细</h4><div class="admin-model-health-list">${healthRows}</div></section>
      <section class="admin-model-detail-section"><h4>价格与配置变更</h4><div class="admin-model-history-list">${historyRows}</div></section>
      ${item.publication_reasons?.length ? `<div class="callout warning"><strong>发布检查</strong><span>${escapeHtml(item.publication_reasons.join("；"))}</span></div>` : ""}
    </div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>关闭</button></div>
  `);
}

function emptyRow(columns, label = "暂无数据") {
  return `<tr><td class="empty-row" colspan="${columns}">${escapeHtml(label)}</td></tr>`;
}

function apiErrorMessage(detail, fallback) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      const location = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
      const message = item?.msg || "请求参数无效";
      return location ? `${location}: ${message}` : message;
    }).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  return fallback;
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
    throw new Error(apiErrorMessage(data.detail, `请求失败 (${response.status})`));
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

function completeAdminAuth(data) {
  state.token = data.access_token;
  setAdminIdentity(data.admin);
  sessionStorage.setItem("token_admin_token", state.token);
}

async function loadAdminAuthMode() {
  const response = await fetch("/admin/auth/bootstrap-status");
  const data = await response.json();
  if (!response.ok) throw new Error(apiErrorMessage(data.detail, "无法读取管理员初始化状态"));
  const bootstrapAvailable = data.bootstrap_available;
  document.getElementById("auth-form").hidden = bootstrapAvailable;
  document.getElementById("bootstrap-form").hidden = !bootstrapAvailable;
  document.getElementById("auth-kicker").textContent = bootstrapAvailable ? "LOKTOKEN / SETUP" : "LOKTOKEN / ADMIN";
  document.getElementById("auth-title").textContent = bootstrapAvailable ? "初始化管理控制台" : "管理控制台";
  document.getElementById("auth-description").textContent = bootstrapAvailable ? "创建首个超级管理员后即可进入控制台。" : "使用管理员账号进入服务运营与配置界面。";
  return bootstrapAvailable;
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
      <td>${accountTypeBadge(item)}</td>
      <td><div class="primary-cell">${accountAccessBadge(item)}<span class="secondary">${formatNumber(item.project_count)} 个项目 · ${formatNumber(item.active_api_key_count)} 个有效 Key</span></div></td>
      <td><strong>${formatMoney(item.balance_micros)}</strong></td>
      <td>${formatMoney(item.recent_spend_micros)}</td>
      <td class="secondary">${formatDate(item.last_activity_at)}</td>
      <td>${activeBadge(item.active)}</td>
      <td class="align-right"><button class="table-button" data-action="account-detail" data-id="${item.id}"><i data-lucide="arrow-up-right"></i><span>详情</span></button></td>
    </tr>
  `).join("") : emptyRow(8);
  icons();
}

function accountDetailDialog(accountId) {
  const item = state.accounts.find((account) => String(account.id) === String(accountId));
  if (!item) { toast("账户信息已更新，请刷新后重试", true); return; }
  const safeName = escapeHtml(item.name);
  const operations = canOperate() ? `
    <button class="secondary-button" type="button" data-action="create-key" data-account-id="${item.id}" data-close><i data-lucide="key-round"></i><span>生成 Key</span></button>
    ${item.access_mode === "portal" && !item.readiness_checks?.portal_login_ready ? `<button class="secondary-button" type="button" data-action="resend-invitation" data-id="${item.id}" data-close><i data-lucide="send"></i><span>重新发送邀请</span></button>` : ""}
    <button class="secondary-button" type="button" data-action="trial-link" data-id="${item.id}" data-name="${safeName}" data-close><i data-lucide="link"></i><span>试用链接</span></button>
    <button class="secondary-button" type="button" data-action="topup" data-id="${item.id}" data-name="${safeName}" data-close><i data-lucide="wallet-cards"></i><span>充值</span></button>
    <button class="secondary-button" type="button" data-toggle="accounts" data-id="${item.id}" data-active="${!item.active}" data-close><i data-lucide="${item.active ? "pause" : "play"}"></i><span>${item.active ? "停用" : "启用"}</span></button>
    ${isSuperadmin() && !item.active ? `<button class="danger-button" type="button" data-action="delete-account" data-id="${item.id}" data-name="${safeName}" data-close><i data-lucide="trash-2"></i><span>删除账户</span></button>` : ""}
  ` : '<span class="secondary">当前角色仅可查看账户信息</span>';
  const readinessLabels = item.access_mode === "portal" ? [
    ["account_active", "账户已启用"], ["portal_login_ready", "用户中心登录"], ["project_ready", "默认项目"], ["api_key_ready", "API Key"], ["balance_ready", "账户余额"], ["model_ready", "可用模型"],
  ] : [
    ["account_active", "账户已启用"], ["project_ready", "默认项目"], ["api_key_ready", "API Key"], ["balance_ready", "账户余额"], ["model_ready", "可用模型"],
  ];
  const readiness = readinessLabels.map(([key, label]) => `<span class="readiness-item ${item.readiness_checks?.[key] ? "ready" : "pending"}"><i data-lucide="${item.readiness_checks?.[key] ? "circle-check" : "circle-dashed"}"></i>${label}</span>`).join("");
  openDialog(`账户详情 · ${safeName}`, `
    <div class="dialog-body account-detail-dialog">
      <div class="account-detail-heading"><div><strong>${safeName}</strong><span>账户 ID ${item.id}</span></div>${activeBadge(item.active)}</div>
      <div class="account-detail-stats">
        <div><span>项目数</span><strong>${formatNumber(item.project_count)}</strong></div>
        <div><span>API Key</span><strong>${formatNumber(item.api_key_count)}</strong></div>
        <div><span>账户余额</span><strong>${formatMoney(item.balance_micros)}</strong></div>
        <div><span>近 30 天消费</span><strong>${formatMoney(item.recent_spend_micros)}</strong></div>
      </div>
      <div class="account-readiness"><div><span>访问状态</span>${accountAccessBadge(item)}</div><div class="readiness-list">${readiness}</div></div>
      <div class="account-profile-grid">
        <div><span>账户类型</span><strong>${escapeHtml(item.access_mode_label || item.account_type || "API 服务账户")}</strong></div>
        <div><span>账户来源</span><strong>${accountSourceBadge(item)}</strong></div>
        <div><span>外部用户 ID</span><strong class="mono" title="${escapeHtml(item.external_user_id)}">${escapeHtml(item.external_user_id || "-")}</strong></div>
        <div><span>登录标识</span><strong>${escapeHtml(item.login_id || "未绑定用户中心登录")}</strong></div>
        <div><span>安全联系方式</span><strong>${escapeHtml(item.security_contact || "未绑定")}</strong></div>
        ${item.invitation_expires_at && !item.readiness_checks?.portal_login_ready ? `<div><span>邀请有效期</span><strong>${formatDate(item.invitation_expires_at)}</strong></div>` : ""}
        <div><span>最近活动</span><strong>${formatDate(item.last_activity_at)}</strong></div>
        <div><span>创建时间</span><strong>${formatDate(item.created_at)}</strong></div>
      </div>
    </div>
    <div class="dialog-actions">${operations}<button class="primary-button" type="button" data-close>完成</button></div>
  `);
}

async function loadKeys() {
  const result = await api("/admin/api-keys");
  document.getElementById("keys-table").innerHTML = result.data.length ? result.data.map((item) => `
    <tr>
      <td><div class="primary-cell"><strong>${escapeHtml(item.name)}</strong><span class="secondary">ID ${item.id}</span></div></td>
      <td class="mono">${escapeHtml(item.key_prefix)}...</td>
      <td>${escapeHtml(item.account_name)}</td>
      <td>${item.revoked_at ? '<span class="badge error">已撤销</span>' : activeBadge(item.active)}</td>
      <td>${item.expires_at ? formatDate(item.expires_at) : "长期有效"}</td>
      <td>${item.spending_limit_micros == null ? "不限" : `${formatMoney(item.spent_micros)} / ${formatMoney(item.spending_limit_micros)}`}</td>
      <td>${formatDate(item.created_at)}</td>
      <td class="align-right">${canOperate() && !item.revoked_at ? `<button class="table-button" data-rotate-admin-key="${item.id}">轮换</button><button class="table-button" data-toggle="api-keys" data-id="${item.id}" data-active="${!item.active}">${item.active ? "停用" : "启用"}</button><button class="table-button danger" data-revoke-admin-key="${item.id}">撤销</button>` : '<span class="secondary">只读</span>'}</td>
    </tr>
  `).join("") : emptyRow(8);
}

function renderAccountAccessTab() {
  const tab = state.accountAccessTab === "keys" ? "keys" : "accounts";
  document.querySelectorAll("[data-account-access-tab]").forEach((button) => {
    const active = button.dataset.accountAccessTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.getElementById("account-access-accounts").hidden = tab !== "accounts";
  document.getElementById("account-access-keys").hidden = tab !== "keys";
}

async function loadAccountAccess() {
  renderAccountAccessTab();
  if (state.accountAccessTab === "keys") await loadKeys();
  else await loadAccounts();
}

function setAccountAccessTab(tab, { historyMode = "push" } = {}) {
  state.accountAccessTab = tab === "keys" ? "keys" : "accounts";
  renderAccountAccessTab();
  if (state.view !== "accounts") return;
  updateAdminHistory("accounts", historyMode);
  renderAdminPageActions("accounts");
  loadAccountAccess().catch((error) => toast(error.message, true));
}

async function loadModels() {
  const [result, connections, presets] = await Promise.all([
    api("/admin/models"),
    api("/admin/provider-connections"),
    api("/admin/provider-presets"),
  ]);
  state.models = result.data;
  state.providerConnections = connections.data || [];
  state.providerPresets = presets.data || [];
  const providerSelect = document.getElementById("model-provider-filter");
  const selectedProvider = providerSelect.value;
  const presetProviders = state.providerPresets.map(providerPresetDisplayName).filter(Boolean);
  const modelProviders = state.models.map((item) => item.catalog_metadata?.provider || "自定义").filter(Boolean);
  const providers = [...new Set([...presetProviders, ...modelProviders])].sort((a, b) => featuredProviderRank(a) - featuredProviderRank(b) || a.localeCompare(b));
  providerSelect.innerHTML = '<option value="">全部服务商</option>' + providers.map((provider) => `<option value="${escapeHtml(provider)}">${escapeHtml(provider)}</option>`).join("");
  providerSelect.value = providers.includes(selectedProvider) ? selectedProvider : "";
  if (state.modelProviderDetail && state.modelProviderDetail !== "__more__" && !providers.includes(state.modelProviderDetail)) state.modelProviderDetail = "";
  renderModels();
}

function providerInitial(provider) {
  return String(provider || "自定义").slice(0, 1).toUpperCase();
}

function providerLogoSlug(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  if (name.includes("deepseek")) return "deepseek";
  if (name.includes("qwen") || name.includes("通义")) return "qwen";
  if (name.includes("智谱") || name.includes("zhipu") || name.includes("glm")) return "glm-local";
  if (name.includes("kimi")) return "kimi-local";
  if (name.includes("moonshot")) return "moonshotai";
  if (name.includes("minimax")) return "minimax";
  if (name.includes("doubao") || name.includes("豆包")) return "doubao-local";
  if (name.includes("字节")) return "bytedance";
  return "";
}

function providerLogoColor(provider) {
  const slug = providerLogoSlug(provider);
  return { deepseek: "4D6BFE", qwen: "6155F5", moonshotai: "4C8BF5", minimax: "FF5B7F", bytedance: "2A5CAA" }[slug] || "59636D";
}

function providerLogo(provider, className = "provider-logo-image") {
  const slug = providerLogoSlug(provider);
  const source = { "glm-local": "/static/provider-logos/glm.png", "kimi-local": "/static/provider-logos/kimi.ico", "doubao-local": "/static/provider-logos/doubao.png" }[slug] || `https://cdn.simpleicons.org/${slug}/${providerLogoColor(provider)}`;
  return slug ? `<img class="${className}" src="${source}" alt="${escapeHtml(provider)} Logo" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span hidden>${escapeHtml(providerInitial(provider))}</span>` : escapeHtml(providerInitial(provider));
}

const featuredProviders = ["deepseek", "qwen", "智谱", "zhipu", "glm", "kimi", "minimax", "doubao", "字节"];
const featuredProviderOrder = ["deepseek", "qwen", "glm", "zhipu", "智谱", "kimi", "minimax", "doubao", "字节"];
function isFeaturedProvider(provider) { return featuredProviders.some((name) => String(provider || "").toLocaleLowerCase().includes(name)); }
function featuredProviderRank(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  const index = featuredProviderOrder.findIndex((item) => name.includes(item));
  return index < 0 ? Number.MAX_SAFE_INTEGER : index;
}
function providerDescription(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  if (name.includes("deepseek")) return "面向高强度推理与复杂应用的通用大模型系列，适合研发、开发与智能体场景。";
  if (name.includes("qwen")) return "通义大模型体系，覆盖文本、多模态与视觉能力，适合企业级 AI 应用落地。";
  if (name.includes("智谱") || name.includes("zhipu") || name.includes("glm")) return "GLM 通用大模型系列，覆盖中文理解、生成、对话与代码等企业应用能力。";
  if (name.includes("kimi")) return "Moonshot AI 通用大模型体系，聚焦长上下文理解、高质量推理与知识整合。";
  if (name.includes("minimax")) return "MiniMax 文本、多模态与内容生成模型，适合 Agent、创作与交互场景。";
  if (name.includes("doubao") || name.includes("豆包") || name.includes("字节")) return "字节跳动模型服务，覆盖文本、视觉、图像与视频生成能力。";
  return "来自生态合作伙伴的模型系列，按统一接口接入并由平台集中管理。";
}

function providerPresetId(provider) {
  const name = String(provider || "").toLocaleLowerCase();
  if (name.includes("deepseek")) return "deepseek";
  if (name.includes("qwen") || name.includes("通义")) return "qwen";
  if (name.includes("智谱") || name.includes("zhipu") || name.includes("glm")) return "glm";
  if (name.includes("kimi") || name.includes("moonshot")) return "kimi";
  if (name.includes("minimax")) return "minimax";
  if (name.includes("doubao") || name.includes("豆包") || name.includes("字节")) return "doubao";
  return "";
}

function providerPresetDisplayName(preset) {
  return preset?.models?.[0]?.catalog_metadata?.provider || preset?.name || preset?.id || "";
}

function providerConnection(provider) {
  const presetId = providerPresetId(provider);
  return state.providerConnections.find((item) => item.preset_id === presetId) || null;
}

function providerConnectionBadge(connection) {
  if (!connection || !connection.credentials_configured) return '<span class="badge neutral">未配置服务商</span>';
  if (connection.status === "healthy") return `<span class="badge success">已连接 · ${formatNumber(connection.callable_model_count)} 个可调用</span>`;
  if (connection.status === "degraded") return `<span class="badge warning">已连接 · ${formatNumber(connection.callable_model_count)} 个可调用</span>`;
  if (connection.status === "misconfigured") return '<span class="badge error">配置错误</span>';
  return '<span class="badge error">连接异常</span>';
}

function providerBalanceSummary(connection) {
  if (!connection || connection.balance_status === "unknown") return '<span class="provider-balance-summary neutral">余额未查询</span>';
  if (connection.balance_status === "unsupported") return '<span class="provider-balance-summary neutral">余额需控制台查询</span>';
  if (connection.balance_status === "error") return '<span class="provider-balance-summary error">余额查询失败</span>';
  const amount = connection.balance_micros == null ? "—" : formatMoney(connection.balance_micros);
  const low = connection.balance_alert_threshold_micros > 0 && connection.balance_micros != null && connection.balance_micros <= connection.balance_alert_threshold_micros;
  return `<span class="provider-balance-summary ${low ? "warning" : "success"}">上游余额 ${escapeHtml(amount)} ${escapeHtml(connection.balance_currency || "CNY")}</span>`;
}

function emptyProviderPresetVisible(preset) {
  const filters = state.modelFilters;
  const query = filters.query.trim().toLocaleLowerCase();
  const displayName = providerPresetDisplayName(preset);
  const presetText = [displayName, preset.name, preset.id, providerDescription(displayName)].join(" ").toLocaleLowerCase();
  const providerMatches = !filters.provider || providerPresetId(filters.provider) === preset.id;
  return providerMatches && (!query || presetText.includes(query));
}

function adminProviderCardData(models) {
  const groups = new Map();
  models.forEach((item) => {
    const provider = item.catalog_metadata?.provider || "自定义";
    if (!groups.has(provider)) groups.set(provider, []);
    groups.get(provider).push(item);
  });

  const featured = [];
  state.providerPresets.filter((preset) => isFeaturedProvider(providerPresetDisplayName(preset))).forEach((preset) => {
    const matchingProviders = [...groups.keys()].filter((provider) => providerPresetId(provider) === preset.id);
    const items = matchingProviders.flatMap((provider) => groups.get(provider) || []);
    matchingProviders.forEach((provider) => groups.delete(provider));
    const hasStoredModels = state.models.some((item) => providerPresetId(item.catalog_metadata?.provider || "") === preset.id);
    if (items.length || (!hasStoredModels && emptyProviderPresetVisible(preset))) {
      featured.push({ provider: providerPresetDisplayName(preset), presetId: preset.id, items });
    }
  });

  groups.forEach((items, provider) => {
    if (isFeaturedProvider(provider)) featured.push({ provider, presetId: providerPresetId(provider), items });
  });
  featured.sort((a, b) => featuredProviderRank(a.provider) - featuredProviderRank(b.provider));
  const otherModels = [...groups.entries()].filter(([provider]) => !isFeaturedProvider(provider)).flatMap(([, items]) => items);
  return { featured, otherModels };
}

function renderAdminProviderCards(models) {
  const grid = document.getElementById("admin-provider-grid");
  const list = document.getElementById("admin-model-list");
  const overview = document.getElementById("model-provider-overview");
  const pageBack = document.getElementById("admin-model-page-back");
  if (state.modelProviderDetail) {
    grid.hidden = true;
    list.hidden = false;
    overview.hidden = false;
    pageBack.hidden = false;
    return;
  }
  list.hidden = true;
  grid.hidden = false;
  overview.hidden = true;
  pageBack.hidden = true;
  const { featured, otherModels } = adminProviderCardData(models);
  grid.innerHTML = featured.length || otherModels.length ? featured.map(({ provider, presetId, items }, index) => {
    const typeCounts = [...new Set(items.map(modelCategory))].map((type) => `${type === "text" ? "文本" : type === "image" ? "图像" : type === "video" ? "视频" : "语音"} ${items.filter((item) => modelCategory(item) === type).length}`).join(" · ");
    const healthy = items.reduce((sum, item) => sum + Number(item.healthy_channel_count || 0), 0);
    const channels = items.reduce((sum, item) => sum + Number(item.channel_count || 0), 0);
    const chips = items.length ? items.slice(0, 3).map((item) => `<span>${escapeHtml(item.catalog_metadata?.display_name || item.public_name)}</span>`).join("") : "<span>尚未同步模型</span>";
    const connection = providerConnection(provider);
    return `<article class="admin-provider-card tone-${index % 5}"><span class="admin-provider-logo">${providerLogo(provider)}</span><span class="admin-provider-copy"><span class="provider-card-title"><strong>${escapeHtml(provider)}</strong>${providerConnectionBadge(connection)}</span><p>${escapeHtml(providerDescription(provider))}</p><span class="provider-model-chips">${chips}</span><small>${items.length} 个模型 · ${typeCounts || (items.length ? "待分类" : "待同步")} · ${healthy}/${channels} 渠道健康</small>${providerBalanceSummary(connection)}<span class="provider-card-actions"><button class="table-button" type="button" data-model-provider="${escapeHtml(provider)}"><i data-lucide="list"></i><span>查看系列</span></button>${canOperate() && presetId ? `<button class="primary-button compact-provider-button" type="button" data-action="configure-provider" data-provider-id="${escapeHtml(presetId)}"><i data-lucide="plug-zap"></i><span>${connection?.credentials_configured ? "管理接入" : "配置接入"}</span></button>` : ""}</span></span></article>`;
  }).join("") + `<button class="admin-provider-card provider-more-card" type="button" data-model-provider-more><span class="admin-provider-more-icon"><i data-lucide="search"></i></span><span class="admin-provider-copy"><strong>更多系列 / 厂商查询</strong><p>${otherModels.length ? "浏览其他第三方及新增模型" : "接入更多第三方模型"}</p><b>进入查询 <i data-lucide="arrow-right"></i></b></span></button>` : '<div class="empty-state compact"><i data-lucide="boxes"></i><span>没有符合筛选条件的供应商</span></div>';
  icons();
  return featured.length + new Set(otherModels.map((item) => item.catalog_metadata?.provider || "自定义")).size;
}

function providerConnectionDialog(presetId) {
  const preset = state.providerPresets.find((item) => item.id === presetId);
  const connection = state.providerConnections.find((item) => item.preset_id === presetId);
  if (!preset) { toast("没有找到供应商模板", true); return; }
  const balanceText = connection?.balance_status === "available" ? `${formatMoney(connection.balance_micros)} ${connection.balance_currency || "CNY"}` : connection?.balance_status === "unsupported" ? "该供应商需在控制台查看" : connection?.balance_status === "error" ? "上次查询失败" : "尚未查询";
  const hasStoredCredential = connection?.credential_source === "stored";
  const credentialSource = hasStoredCredential ? "api_key" : "environment";
  openDialog(`服务商接入 · ${preset.name}`, `
    <form id="provider-connection-form">
      <div class="dialog-body">
        <div class="provider-connection-summary"><span class="admin-provider-logo">${providerLogo(preset.name)}</span><div><strong>一次配置，统一同步 ${preset.models.length} 个系列模型</strong><p>${escapeHtml(preset.note)}</p></div></div>
        <section class="provider-form-section"><div class="provider-form-section-heading"><strong>连接配置</strong><span>用于访问服务商模型目录和 API</span></div><div class="field"><label for="provider-connection-url">供应商 API 地址</label><input id="provider-connection-url" name="provider_base_url" required maxlength="500" value="${escapeHtml(connection?.provider_base_url || preset.base_url)}"></div></section>
        <section class="provider-form-section"><div class="provider-form-section-heading"><strong>访问凭证</strong><span>选择一种凭证来源，服务端不会回显已保存的密钥</span></div><div class="field"><label for="provider-credential-source">凭证来源</label><select id="provider-credential-source" name="credential_source"><option value="environment"${credentialSource === "environment" ? " selected" : ""}>服务器环境变量</option><option value="api_key"${credentialSource === "api_key" ? " selected" : ""}>控制台 API Key</option></select></div><div class="field" id="provider-env-field"><label for="provider-connection-env">服务器密钥环境变量</label><input id="provider-connection-env" name="provider_api_key_env" maxlength="120" value="${escapeHtml(connection?.provider_api_key_env || preset.api_key_env || "")}" placeholder="例如 DEEPSEEK_API_KEY"><small class="field-hint">填写部署在 TOKEN 服务环境中的变量名，不要填写密钥内容。</small></div><div class="field" id="provider-key-field" hidden><label for="provider-connection-key">供应商 API Key</label><input id="provider-connection-key" name="provider_api_key" type="password" autocomplete="new-password" placeholder="${hasStoredCredential ? "已保存；留空表示继续使用当前密钥" : "输入服务商控制台发放的 API Key"}"><small class="field-hint">${hasStoredCredential ? "当前已有托管密钥；填写新值即可替换。" : "密钥将由服务端加密保存。"}</small></div><p id="provider-credential-note" class="field-hint"></p></section>
        <section class="provider-form-section"><div class="provider-form-section-heading"><strong>采购监控</strong><span>仅用于余额记录和预警，不影响用户额度</span></div><div class="field"><label for="provider-balance-threshold">上游余额预警阈值（元，可选）</label><input id="provider-balance-threshold" name="balance_alert_threshold" type="number" min="0" step="0.01" value="${connection?.balance_alert_threshold_micros ? (connection.balance_alert_threshold_micros / 1000000).toFixed(2) : "0"}"><small class="field-hint">余额低于此金额时标记采购预警；填 0 表示不设置阈值。</small></div><div class="provider-balance-panel"><div><strong>上游账户余额</strong><span>${escapeHtml(balanceText)}</span><small>${connection?.balance_checked_at ? `最近查询：${formatDate(connection.balance_checked_at)}` : "余额查询不会通过模型健康检查推断"}</small></div><div class="provider-card-actions"><button class="table-button" type="button" data-action="refresh-provider-balance" data-provider-id="${escapeHtml(presetId)}"><i data-lucide="refresh-cw"></i><span>刷新余额</span></button><button class="secondary-button compact-provider-button" type="button" data-action="manual-provider-balance" data-provider-id="${escapeHtml(presetId)}"><i data-lucide="pencil"></i><span>手工录入</span></button></div></div></section>
      <p class="dialog-copy">保存后会读取服务商模型目录并同步系列模型。文本模型使用聊天接口；图像模型可通过图像生成接口调用，视频模型通过异步任务创建和结果查询调用。只有尚未通过目录核验、未配置任务价格或渠道不可用的模型会保留为候选。</p>
      </div>
      <div class="dialog-actions provider-dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="secondary-button" type="button" data-action="test-provider" data-provider-id="${escapeHtml(presetId)}"><i data-lucide="plug-zap"></i><span>测试连接</span></button><button class="primary-button" type="submit"><i data-lucide="refresh-cw"></i><span>${connection ? "保存并同步模型" : "配置并同步模型"}</span></button></div>
    </form>`);
  const credentialSourceInput = document.getElementById("provider-credential-source");
  const envField = document.getElementById("provider-env-field");
  const keyField = document.getElementById("provider-key-field");
  const envInput = document.getElementById("provider-connection-env");
  const keyInput = document.getElementById("provider-connection-key");
  const credentialNote = document.getElementById("provider-credential-note");
  const updateCredentialFields = () => {
    const usesApiKey = credentialSourceInput.value === "api_key";
    envField.hidden = usesApiKey;
    keyField.hidden = !usesApiKey;
    envInput.required = !usesApiKey;
    keyInput.required = usesApiKey && !hasStoredCredential;
    credentialNote.textContent = usesApiKey ? "控制台 API Key 会加密保存在服务端，仅用于该服务商连接。" : hasStoredCredential ? "切换到环境变量后，原托管密钥将不再作为该连接的凭证。" : "环境变量由服务进程读取，密钥内容不会进入管理控制台。";
  };
  credentialSourceInput.addEventListener("change", updateCredentialFields);
  updateCredentialFields();
  const credentialPayload = (form) => {
    const source = String(form.get("credential_source") || "environment");
    const rawKey = source === "api_key" ? String(form.get("provider_api_key") || "").trim() : "";
    return { provider_base_url: String(form.get("provider_base_url") || "").trim(), credential_source: source === "api_key" ? "console" : "environment", provider_api_key_env: source === "environment" ? String(form.get("provider_api_key_env") || "").trim() || null : null, provider_api_key: rawKey || null, clear_provider_api_key: source === "environment" && hasStoredCredential };
  };
  document.getElementById("provider-connection-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const credentials = credentialPayload(form);
    const payload = {
      ...credentials,
      balance_alert_threshold_micros: Math.round(Number(form.get("balance_alert_threshold") || 0) * 1000000),
    };
    if (!payload.provider_api_key) delete payload.provider_api_key;
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const result = await api(`/admin/provider-connections/${presetId}`, { method: "PUT", body: JSON.stringify(payload) });
      closeDialog();
      const pricedCount = result.models.filter((item) => item.input_price_micros_per_1k > 0 && item.output_price_micros_per_1k > 0).length;
      const pendingPriceCount = result.models.length - pricedCount;
      toast(`${preset.name} 已同步 ${result.models.length} 个模型，其中 ${pricedCount} 个已配置单模型价格、${result.connection.callable_model_count} 个可调用${pendingPriceCount ? `，${pendingPriceCount} 个待核价` : ""}`);
      await loadModels();
    } catch (error) {
      submit.disabled = false;
      toast(error.message, true);
    }
  });
  document.querySelector('[data-action="test-provider"]').addEventListener("click", async (event) => {
    const form = new FormData(document.getElementById("provider-connection-form"));
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await api(`/admin/provider-connections/${presetId}/test`, { method: "POST", body: JSON.stringify(credentialPayload(form)) });
      toast(`连接成功：发现 ${result.discovered_model_count} 个模型，耗时 ${result.latency_ms} ms`);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  });
  icons();
}

function manualProviderBalanceDialog(presetId) {
  const preset = state.providerPresets.find((item) => item.id === presetId);
  if (!preset) return;
  openDialog(`手工记录余额 · ${preset.name}`, `<form id="manual-provider-balance-form"><div class="dialog-body"><p class="dialog-copy">当供应商没有公开余额 API 时，可从供应商控制台读取当前可用余额并记录。该记录会进入审计和采购预警，不会修改用户额度。</p><div class="field-row"><div class="field"><label for="manual-provider-balance-amount">可用余额</label><input id="manual-provider-balance-amount" name="amount" type="number" min="0" step="0.01" required></div><div class="field"><label for="manual-provider-balance-currency">币种</label><input id="manual-provider-balance-currency" name="currency" value="CNY" maxlength="12" required></div></div><div class="field"><label for="manual-provider-balance-note">备注（可选）</label><input id="manual-provider-balance-note" name="note" maxlength="255" placeholder="例如：2026-08-21 供应商控制台截图核验"></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="save"></i><span>保存记录</span></button></div></form>`);
  document.getElementById("manual-provider-balance-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await api(`/admin/provider-connections/${presetId}/balance/manual`, { method: "POST", body: JSON.stringify({ amount: Number(form.get("amount")), currency: String(form.get("currency") || "CNY").trim(), note: String(form.get("note") || "").trim() || null }) });
      closeDialog();
      toast(`${preset.name} 上游余额记录已更新`);
      await loadModels();
    } catch (error) { submit.disabled = false; toast(error.message, true); }
  });
  icons();
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
  const detailModels = state.modelProviderDetail === "__more__" ? models.filter((item) => !isFeaturedProvider(item.catalog_metadata?.provider || "")) : state.modelProviderDetail ? models.filter((item) => (item.catalog_metadata?.provider || "自定义") === state.modelProviderDetail) : models;
  const providerCount = renderAdminProviderCards(models);
  document.getElementById("model-result-count").textContent = state.modelProviderDetail ? `${state.modelProviderDetail === "__more__" ? "更多系列 / 厂商查询" : state.modelProviderDetail} · ${detailModels.length} 个模型` : `${providerCount} 家供应商 · ${models.length} 个模型`;
  if (!state.modelProviderDetail) return;
  const detailProvider = state.modelProviderDetail === "__more__" ? "更多系列 / 厂商查询" : state.modelProviderDetail;
  const detailPresetId = providerPresetId(detailProvider);
  const detailPreset = state.providerPresets.find((preset) => preset.id === detailPresetId);
  const providerActions = canOperate() && detailPreset ? `<div class="model-provider-overview-actions"><button class="primary-button compact-provider-button" type="button" data-action="health-check-provider" data-provider-id="${escapeHtml(detailPreset.id)}"><i data-lucide="activity"></i><span>检测全部渠道</span></button><button class="primary-button compact-provider-button" type="button" data-action="create-provider-model" data-provider-id="${escapeHtml(detailPreset.id)}"><i data-lucide="plus"></i><span>添加模型服务</span></button></div>` : "";
  document.getElementById("model-provider-overview").innerHTML = `<div class="model-provider-overview-main"><div class="model-provider-overview-title"><span class="admin-provider-logo">${providerLogo(detailProvider)}</span><div><span class="eyebrow">MODEL & PRICING</span><h3>${escapeHtml(detailProvider)}</h3><p>${escapeHtml(providerDescription(detailProvider))}</p></div></div>${providerActions}</div>`;
  document.getElementById("models-table").innerHTML = detailModels.length ? `<div class="admin-model-card-grid">${detailModels.map((item) => {
    const publishBlocked = item.publication_state === "blocked" && !item.active;
    const publishLabel = item.active ? "下架" : publishBlocked ? "完善后上架" : "上架";
    const publishTitle = publishBlocked ? ` title="${escapeHtml(item.publication_reasons.join("；"))}" disabled` : "";
    const apiType = modelApiType(item);
    const chatCompatible = apiType === "chat_completions";
    const provider = item.catalog_metadata?.provider || "自定义";
    const channelButton = `<button class="table-button" data-action="manage-channels" data-id="${item.id}" data-name="${escapeHtml(item.public_name)}"><i data-lucide="route"></i><span>渠道</span></button>`;
    const pricingButton = `<button class="table-button" data-action="edit-model-pricing" data-id="${item.id}" data-name="${escapeHtml(item.public_name)}" data-input-price="${item.input_price_micros_per_1k}" data-output-price="${item.output_price_micros_per_1k}" data-task-price="${item.task_price_micros || 0}"><i data-lucide="receipt-text"></i><span>定价</span></button>`;
    const preflightButton = isSuperadmin() ? `<button class="table-button" data-action="preflight-model" data-id="${item.id}" data-name="${escapeHtml(item.public_name)}" ${chatCompatible ? "" : 'disabled title="等待统一调用适配器"'}><i data-lucide="flask-conical"></i><span>预检</span></button>` : "";
    const publishButton = `<button class="table-button" data-toggle="models" data-id="${item.id}" data-active="${!item.active}"${publishTitle}>${publishLabel}</button>`;
    const deleteButton = isSuperadmin() ? `<button class="table-button danger" data-action="delete-model" data-id="${item.id}" data-name="${escapeHtml(item.catalog_metadata?.display_name || item.public_name)}"><i data-lucide="trash-2"></i><span>删除</span></button>` : "";
    const metadata = item.catalog_metadata || {};
    const capabilities = (metadata.capabilities || []).slice(0, 4).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
    const parameters = (metadata.supported_parameters || []).slice(0, 3).map((value) => escapeHtml(value)).join(" · ");
    return `
      <article class="admin-model-card">
        <div class="admin-model-card-header"><span class="admin-model-icon">${providerLogo(provider, "admin-model-logo-image")}</span><div class="admin-model-card-title"><div><h3>${escapeHtml(metadata.display_name || item.public_name)}</h3><div class="admin-model-version"><span>模型版本</span><strong>${escapeHtml(metadata.model_version || "待上游确认")}</strong></div><code>调用 ID：${escapeHtml(item.public_name)}</code></div><div>${modelPublicationBadge(item)}</div></div></div>
        <div class="admin-model-card-meta">${modelTypeBadge(item)}<div class="admin-model-tags inline">${capabilities || '<span class="empty">能力待补充</span>'}</div><span class="admin-model-health"><i data-lucide="activity"></i>${formatNumber(item.healthy_channel_count)} / ${formatNumber(item.channel_count)} 健康</span></div>
        <div class="admin-model-card-stats"><div><span>上下文</span><strong>${escapeHtml(metadata.context_window || "按上游")}</strong></div><div><span>最大输出</span><strong>${escapeHtml(formatMaxOutputTokens(metadata.max_output_tokens, "按上游"))}</strong></div><div><span>参数</span><strong title="${escapeHtml(parameters)}">${escapeHtml(parameters || "待补充")}</strong></div></div>
        <div class="admin-model-card-pricing"><div><span>平台输入 / 1M</span><strong>${chatCompatible ? modelPriceText(item.input_price_micros_per_1k) : "按任务计费"}</strong></div><div><span>平台输出 / 1M</span><strong>${chatCompatible ? modelPriceText(item.output_price_micros_per_1k) : "按任务计费"}</strong></div></div>
        <div class="admin-model-card-footer"><button class="text-button" type="button" data-action="model-detail-admin" data-id="${item.id}"><i data-lucide="file-text"></i><span>完整参数</span></button><div class="admin-model-actions">${canOperate() ? `${channelButton}${pricingButton}${preflightButton}${publishButton}${deleteButton}` : '<span class="secondary">只读</span>'}</div></div>
      </article>
    `; }).join("")}</div>` : '<div class="model-catalog-empty"><i data-lucide="search-x"></i><strong>没有符合筛选条件的模型</strong><span>尝试调整搜索词或筛选条件</span></div>';
  icons();
}

async function checkAllChannels(providerPresetId = "") {
  const query = providerPresetId ? `?provider_preset_id=${encodeURIComponent(providerPresetId)}` : "";
  const result = await api(`/admin/models/health-check${query}`, { method: "POST", body: "{}" });
  const details = [`${result.healthy} 个健康`];
  if (result.unavailable) details.push(`${result.unavailable} 个未开放`);
  if (result.pending_adapter) details.push(`${result.pending_adapter} 个待适配`);
  if (result.misconfigured) details.push(`${result.misconfigured} 个配置错误`);
  if (result.unhealthy) details.push(`${result.unhealthy} 个异常`);
  const provider = providerPresetId ? providerPresetDisplayName(state.providerPresets.find((item) => item.id === providerPresetId)) : "全部服务商";
  toast(`${provider}已检测 ${result.checked} 个渠道：${details.join("，")}`, result.unhealthy + result.misconfigured > 0);
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

const loaders = { overview: loadOverview, accounts: loadAccountAccess, models: loadModels, payments: loadPayments, redemptions: loadRedemptions, usage: loadUsage, audit: loadAudit };

function renderAdminPageActions(view) {
  const target = document.getElementById(`admin-page-actions-${view}`);
  if (!target) return;
  const pageActions = {
    accounts: '<button class="secondary-button content-page-button" type="button" data-action="create-key"><i data-lucide="key-round"></i><span>生成 Key</span></button><button class="primary-button content-page-button" type="button" data-action="create-account"><i data-lucide="plus"></i><span>新建账户</span></button>',
    models: '<button class="icon-button model-page-back" id="admin-model-page-back" type="button" data-model-page-back hidden title="返回模型管理" aria-label="返回模型管理"><i data-lucide="arrow-left"></i></button>',
    payments: '<button class="secondary-button content-page-button" type="button" data-action="reconcile-ledger"><i data-lucide="scale"></i><span>账本对账</span></button><button class="primary-button content-page-button" type="button" data-action="create-payment"><i data-lucide="plus"></i><span>创建订单</span></button>',
    redemptions: '<button class="primary-button content-page-button" type="button" data-action="create-redemption"><i data-lucide="plus"></i><span>创建兑换码</span></button>',
    usage: '<button class="secondary-button content-page-button" type="button" data-action="provider-bills"><i data-lucide="file-check-2"></i><span>供应商账单</span></button>',
    audit: '<button class="secondary-button content-page-button" type="button" data-action="manage-admins"><i data-lucide="users-round"></i><span>管理员与角色</span></button>',
  };
  const globalActions = `${view === "overview" ? '<a class="secondary-button content-page-button" href="/guide/admin" target="_blank" rel="noopener"><i data-lucide="book-open"></i><span>管理文档</span></a><span class="environment" id="environment-badge"><span class="status-dot"></span>读取环境</span>' : ""}<button class="icon-button" type="button" data-action="admin-back" title="返回上一页" aria-label="返回上一页" ${view === "overview" ? "disabled" : ""}><i data-lucide="arrow-left"></i></button><button class="icon-button" type="button" data-action="admin-refresh" title="刷新" aria-label="刷新"><i data-lucide="refresh-cw"></i></button>`;
  target.innerHTML = `${pageActions[view] || ""}${globalActions}`;
  icons();
  applyRoleUi();
}

async function switchView(view, { historyMode = "push" } = {}) {
  if (!Object.prototype.hasOwnProperty.call(titles, view)) view = "overview";
  if (view === "accounts") state.accountAccessTab = accountAccessTabFromUrl();
  state.view = view;
  updateAdminHistory(view, historyMode);
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  renderAdminPageActions(view);
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
        <div class="field"><label for="account-name">账户名称</label><input id="account-name" name="name" required maxlength="120" placeholder="例如：研发团队或 API 应用"></div>
        <div class="account-mode-options" role="radiogroup" aria-label="账户类型">
          <label><input type="radio" name="access_mode" value="api" checked><i data-lucide="braces"></i><span><strong>API 服务账户</strong><small>供应用或团队通过 API Key 调用</small></span></label>
          <label><input type="radio" name="access_mode" value="portal"><i data-lucide="user-round"></i><span><strong>用户中心账户</strong><small>邀请用户登录并自行管理访问</small></span></label>
        </div>
        <div class="account-mode-fields" id="api-account-fields">
          <div class="field"><label for="external-user-id">外部用户 ID（可选）</label><input id="external-user-id" name="external_user_id" maxlength="120" placeholder="例如：service-001"><small class="field-hint">可留空，系统会自动生成内部标识。</small></div>
          <label class="form-toggle-row" for="provision-api-key"><input id="provision-api-key" name="create_api_key" type="checkbox"><span><strong>同时创建 API Key</strong><small>为该账户配置受限的模型访问凭证</small></span></label>
          <div class="provision-access-config" id="provision-access-config" hidden>
            <div class="field"><label for="provision-key-name">Key 名称</label><input id="provision-key-name" name="api_key_name" maxlength="120" placeholder="例如：production" disabled></div>
            <div class="field-row"><div class="field"><label for="provision-key-expires">有效期（天）</label><input id="provision-key-expires" name="api_key_expires_in_days" type="number" min="1" max="3650" value="30" disabled><small class="field-hint">建议使用有限有效期。</small></div><div class="field"><label for="provision-key-limit">消费额度（元）</label><input id="provision-key-limit" name="api_key_spending_limit" type="number" min="0.01" step="0.01" placeholder="不限" disabled></div></div>
            <div class="field-row"><div class="field"><label for="provision-key-rate">限流次数</label><input id="provision-key-rate" name="api_key_rate_limit_requests" type="number" min="1" max="100000" placeholder="使用平台默认" disabled></div><div class="field"><label for="provision-key-rate-window">限流窗口（秒）</label><input id="provision-key-rate-window" name="api_key_rate_limit_window_seconds" type="number" min="1" max="86400" placeholder="使用平台默认" disabled></div></div>
          </div>
        </div>
        <div class="account-mode-fields" id="portal-account-fields" hidden>
          <div class="field-row"><div class="field"><label for="account-login-id">登录账号</label><input id="account-login-id" name="login_id" minlength="3" maxlength="160" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{2,159}" autocomplete="off" placeholder="例如：zhangsan" disabled></div><div class="field"><label for="account-security-contact">安全联系方式</label><input id="account-security-contact" name="security_contact" minlength="3" maxlength="160" placeholder="邮箱或手机号" disabled></div></div>
          <div class="invitation-note"><i data-lucide="shield-check"></i><span><strong>由用户设置密码</strong><small>系统发送一次性邀请，管理员不会接触用户密码。</small></span></div>
        </div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">创建账户</button></div>
    </form>`);
  const form = document.getElementById("dialog-form");
  const createKey = document.getElementById("provision-api-key");
  const accessConfig = document.getElementById("provision-access-config");
  const accessInputs = [...accessConfig.querySelectorAll("input")];
  const apiFields = document.getElementById("api-account-fields");
  const portalFields = document.getElementById("portal-account-fields");
  const portalInputs = [...portalFields.querySelectorAll("input")];
  const syncAccountMode = () => {
    const portalMode = form.elements.access_mode.value === "portal";
    apiFields.hidden = portalMode;
    portalFields.hidden = !portalMode;
    document.getElementById("external-user-id").disabled = portalMode;
    createKey.disabled = portalMode;
    accessConfig.hidden = portalMode || !createKey.checked;
    accessInputs.forEach((input) => { input.disabled = portalMode || !createKey.checked; });
    document.getElementById("provision-key-name").required = !portalMode && createKey.checked;
    portalInputs.forEach((input) => { input.disabled = !portalMode; input.required = portalMode; });
  };
  form.querySelectorAll('input[name="access_mode"]').forEach((input) => input.addEventListener("change", syncAccountMode));
  createKey.addEventListener("change", syncAccountMode);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const rawData = Object.fromEntries(new FormData(event.currentTarget));
    const data = { name: rawData.name.trim(), access_mode: rawData.access_mode };
    if (!data.name) { toast("请填写账户名称", true); return; }
    if (data.access_mode === "portal") {
      data.login_id = rawData.login_id.trim();
      data.security_contact = rawData.security_contact.trim();
    } else {
      data.external_user_id = rawData.external_user_id.trim() || null;
    }
    if (data.access_mode === "api" && createKey.checked) {
      data.api_key = { name: rawData.api_key_name.trim() };
      if (rawData.api_key_expires_in_days) data.api_key.expires_in_days = Number(rawData.api_key_expires_in_days);
      if (rawData.api_key_spending_limit) data.api_key.spending_limit_micros = Math.round(Number(rawData.api_key_spending_limit) * 1_000_000);
      if (rawData.api_key_rate_limit_requests) data.api_key.rate_limit_requests = Number(rawData.api_key_rate_limit_requests);
      if (rawData.api_key_rate_limit_window_seconds) data.api_key.rate_limit_window_seconds = Number(rawData.api_key_rate_limit_window_seconds);
    }
    try {
      const result = await api("/admin/accounts/provision", { method: "POST", body: JSON.stringify(data) });
      closeDialog();
      if (result.invitation) {
        await loadAccounts();
        if (result.invitation.setup_url) {
          const setupUrl = result.invitation.setup_url;
          openDialog("用户中心邀请已创建", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="clock-3"></i><span>邀请将在 ${escapeHtml(formatDate(result.invitation.expires_at))} 失效，且只能使用一次。</span></div><div class="secret-box mono">${escapeHtml(setupUrl)}</div><div class="secret-actions"><button class="secondary-button" id="copy-invitation"><i data-lucide="copy"></i><span>复制邀请链接</span></button><a class="primary-button" href="${escapeHtml(setupUrl)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i><span>打开邀请</span></a></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
          document.getElementById("copy-invitation").addEventListener("click", async () => { await navigator.clipboard.writeText(setupUrl); toast("邀请链接已复制"); });
        } else {
          toast("用户中心账户已创建，邀请已发送");
        }
      } else if (result.api_key) {
        state.accountAccessTab = "keys";
        renderAccountAccessTab();
        updateAdminHistory("accounts", "replace");
        renderAdminPageActions("accounts");
        await loadKeys();
        openDialog("账户与 API Key 已创建", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="triangle-alert"></i><span>请立即保存该 Key。关闭后无法再次查看明文。</span></div><div class="secret-box mono">${escapeHtml(result.api_key.key)}</div><div class="secret-actions"><button class="secondary-button" id="copy-key"><i data-lucide="copy"></i><span>复制</span></button></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
        document.getElementById("copy-key").addEventListener("click", async () => { await navigator.clipboard.writeText(result.api_key.key); toast("密钥已复制"); });
      } else {
        toast("API 服务账户已创建，可随时生成 Key");
        await loadAccounts();
      }
    } catch (error) { toast(error.message, true); }
  });
}

async function keyDialog(accountId = null) {
  if (!state.accounts.length) state.accounts = (await api("/admin/accounts")).data;
  if (!state.accounts.length) { toast("请先创建账户", true); return; }
  openDialog("生成 API Key", `
    <form id="dialog-form">
      <div class="dialog-body">
        <div class="field"><label for="key-name">Key 名称</label><input id="key-name" name="name" required maxlength="120"></div>
        <div class="field"><label for="key-account">所属账户</label><select id="key-account" name="account_id" required>${state.accounts.filter((item) => item.active).map((item) => `<option value="${item.id}"${String(item.id) === String(accountId) ? " selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.external_user_id)}</option>`).join("")}</select></div>
        <div class="field-row"><div class="field"><label for="key-expires">有效期（天）</label><input id="key-expires" name="expires_in_days" type="number" min="1" max="3650" placeholder="长期有效"></div><div class="field"><label for="key-limit">消费额度（元）</label><input id="key-limit" name="spending_limit" type="number" min="0.01" step="0.01" placeholder="不限"></div></div>
        <div class="field-row"><div class="field"><label for="key-rate">限流次数</label><input id="key-rate" name="rate_limit_requests" type="number" min="1" max="100000" placeholder="使用平台默认"></div><div class="field"><label for="key-rate-window">限流窗口（秒）</label><input id="key-rate-window" name="rate_limit_window_seconds" type="number" min="1" max="86400" placeholder="使用平台默认"></div></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">生成 Key</button></div>
    </form>`);
  document.getElementById("dialog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    data.account_id = Number(data.account_id);
    if (data.expires_in_days) data.expires_in_days = Number(data.expires_in_days); else delete data.expires_in_days;
    if (data.spending_limit) { data.spending_limit_micros = Math.round(Number(data.spending_limit) * 1000000); } delete data.spending_limit;
    if (data.rate_limit_requests) data.rate_limit_requests = Number(data.rate_limit_requests); else delete data.rate_limit_requests;
    if (data.rate_limit_window_seconds) data.rate_limit_window_seconds = Number(data.rate_limit_window_seconds); else delete data.rate_limit_window_seconds;
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

function modelDialog(providerPresetId = "") {
  const preset = providerPresetId ? state.providerPresets.find((item) => item.id === providerPresetId) : null;
  const connection = preset ? state.providerConnections.find((item) => item.preset_id === preset.id) : null;
  const provider = preset ? providerPresetDisplayName(preset) : "自定义";
  const providerBaseUrl = connection?.provider_base_url || preset?.base_url || "http://localhost:4000/v1";
  const providerKeyEnv = connection?.provider_api_key_env || preset?.api_key_env || "";
  const providerModels = preset?.models || [];
  const existingModelNames = new Set(state.models.map((item) => item.public_name));
  const availableProviderModels = providerModels.filter((item) => !existingModelNames.has(item.public_name));
  const providerModelOptions = providerModels.map((item) => {
    const installed = existingModelNames.has(item.public_name);
    return `<option value="${escapeHtml(item.model_id)}"${installed ? " disabled" : ""}>${escapeHtml(item.display_name)}${installed ? "（已接入）" : ""}</option>`;
  }).join("");
  const providerFormFields = preset
    ? `<div class="field"><label for="provider-model-id">模型名称</label><select id="provider-model-id" name="upstream_model" required${availableProviderModels.length ? "" : " disabled"}><option value="">${availableProviderModels.length ? "请选择已核验的服务商模型" : "该服务商目录中的模型均已接入"}</option>${providerModelOptions}</select><small class="field-hint">模型版本、能力、上下文和价格将按已核验目录自动带入。</small></div><section id="provider-model-auto-preview" class="provider-model-auto-preview" hidden></section>`
    : `<div class="field-row"><div class="field"><label for="public-name">公开名称</label><input id="public-name" name="public_name" required></div><div class="field"><label for="upstream-model">上游模型</label><input id="upstream-model" name="upstream_model" required></div></div><div class="field"><label for="provider-url">供应商地址</label><input id="provider-url" name="provider_base_url" placeholder="http://localhost:4000/v1"></div><div class="field-row"><div class="field"><label for="key-env">密钥环境变量</label><input id="key-env" name="provider_api_key_env" pattern="[A-Z][A-Z0-9_]{1,119}" placeholder="OPENAI_API_KEY"></div><div class="field"><label for="provider-key">供应商 API Key</label><input id="provider-key" name="provider_api_key" type="password" autocomplete="new-password" placeholder="可选，服务端加密保存"></div></div><div class="field-row"><div class="field"><label for="input-price">输入价格 / 1M Token（元）</label><input id="input-price" name="input_price" type="number" min="0" step="0.001" value="0"></div><div class="field"><label for="output-price">输出价格 / 1M Token（元）</label><input id="output-price" name="output_price" type="number" min="0" step="0.001" value="0"></div></div>`;
  openDialog(preset ? `添加 ${provider} 模型` : "添加模型", `
    <form id="dialog-form">
      <div class="dialog-body">
        ${preset ? `<div class="provider-model-create-summary"><span class="admin-provider-logo">${providerLogo(provider)}</span><div><strong>${escapeHtml(provider)} 服务商接入</strong><span>模型将复用该服务商的地址与凭证配置，并归入当前服务商系列。</span></div></div><input name="provider_preset_id" type="hidden" value="${escapeHtml(preset.id)}"><input name="provider_base_url" type="hidden" value="${escapeHtml(providerBaseUrl)}"><input name="provider_api_key_env" type="hidden" value="${escapeHtml(providerKeyEnv)}">` : ""}
        ${providerFormFields}
      </div>
      <div class="dialog-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit"${preset && !availableProviderModels.length ? " disabled" : ""}>添加模型</button></div>
    </form>`);
  const form = document.getElementById("dialog-form");
  if (preset) {
    const selector = document.getElementById("provider-model-id");
    const preview = document.getElementById("provider-model-auto-preview");
    const updatePreview = () => {
      const model = providerModels.find((item) => item.model_id === selector.value);
      if (!model) { preview.hidden = true; preview.innerHTML = ""; return; }
      const metadata = model.catalog_metadata || {};
      const capabilities = (metadata.capabilities || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("") || "<span>按服务商配置</span>";
      const modalities = (metadata.modalities || []).join(" · ") || "文本";
      preview.innerHTML = `<div class="provider-model-auto-preview-heading"><span>自动带入参数</span><code>${escapeHtml(model.model_id)}</code></div><div class="provider-model-auto-preview-grid"><div><span>模型版本</span><strong>${escapeHtml(model.model_version || metadata.model_version || "待上游确认")}</strong></div><div><span>上下文</span><strong>${escapeHtml(model.context_window || metadata.context_window || "按上游配置")}</strong></div><div><span>最大输出</span><strong>${escapeHtml(formatMaxOutputTokens(model.max_output_tokens || metadata.max_output_tokens))}</strong></div><div><span>模态</span><strong>${escapeHtml(modalities)}</strong></div><div><span>平台输入 / 1M</span><strong>${formatTokenPricePerMillion(model.platform_input_price_micros_per_1k)}</strong></div><div><span>平台输出 / 1M</span><strong>${formatTokenPricePerMillion(model.platform_output_price_micros_per_1k)}</strong></div></div><div class="provider-model-auto-preview-capabilities"><span>能力</span><div>${capabilities}</div></div>`;
      preview.hidden = false;
    };
    selector.addEventListener("change", updatePreview);
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const payload = preset ? {
      provider_preset_id: preset.id,
      upstream_model: data.upstream_model,
    } : {
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
  if (model && modelApiType(model) !== "chat_completions") {
    const unit = modelApiType(model) === "video_generations" ? "次视频生成" : "张图片生成";
    openDialog(`任务定价 · ${modelName}`, `<form id="task-pricing-form"><div class="dialog-body"><div class="field"><label for="task-price">平台售价 / ${unit}（元）</label><input id="task-price" name="task_price" type="number" min="0.001" step="0.001" value="${(Number(model.task_price_micros || 0) / 1000000).toFixed(3)}" required><small class="field-hint">该模型按任务计费，不使用 Token 输入或输出价格。</small></div></div><div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="save"></i><span>保存定价</span></button></div></form>`);
    document.getElementById("task-pricing-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const taskPriceMicros = Math.round(Number(new FormData(event.currentTarget).get("task_price") || 0) * 1000000);
      try { await api(`/admin/models/${modelId}`, { method: "PATCH", body: JSON.stringify({ task_price_micros: taskPriceMicros }) }); closeDialog(); toast("任务定价已保存"); await loadModels(); } catch (error) { toast(error.message, true); }
    });
    return;
  }
  const pricing = model?.official_pricing;
  const officialReference = officialTokenReference(pricing);
  const hasOfficialPrice = Boolean(officialReference);
  const configuredMargin = Number(model?.pricing_margin_bps || 0) / 100;
  const currentInputPerMillion = Number(inputPriceMicros || 0) * 1000;
  const inferredMargin = hasOfficialPrice && currentInputPerMillion > 0
    ? Math.max(0.01, Math.min(99, (1 - Number(officialReference.input_micros) / currentInputPerMillion) * 100))
    : 20;
  const initialMargin = configuredMargin > 0 ? configuredMargin : Number(inferredMargin.toFixed(2));
  const sourceLabel = pricing?.source || "服务商官方价格";
  const tierRows = Array.isArray(pricing?.tiers) ? pricing.tiers.map((tier) => `<div><span>${formatTokenBound(tier.min_input_tokens_exclusive)} &lt; 输入 ≤ ${formatTokenBound(tier.max_input_tokens_inclusive)}</span><strong>输入 ${formatMoney(tier.input_micros)} · 输出 ${formatMoney(tier.output_micros)}</strong></div>`).join("") : "";
  const reference = pricing?.off_peak ? `
    <section class="pricing-reference" aria-label="官方成本参考">
      <div class="pricing-reference-heading"><div><span>官方成本参考</span><strong>人民币 / 1M Token</strong></div><div class="pricing-reference-actions"><span>低峰时段</span><a href="${escapeHtml(pricing.source_url)}" target="_blank" rel="noreferrer">查看官网价格<i data-lucide="external-link"></i></a></div></div>
      <div class="pricing-reference-grid"><div><span>缓存命中输入</span><strong>${formatMoney(pricing.off_peak.input_cache_hit_micros)}</strong></div><div><span>缓存未命中输入</span><strong>${formatMoney(pricing.off_peak.input_cache_miss_micros)}</strong></div><div><span>输出价格</span><strong>${formatMoney(pricing.off_peak.output_micros)}</strong></div></div>
    </section>` : hasOfficialPrice ? `
    <div class="field-hint">官方标准成本价（人民币 / 1M Token，不含活动优惠）</div>
    <div class="key-detail-grid pricing-tier-grid">${tierRows}<div><span>来源 · ${escapeHtml(pricing.region || "中国")}</span><strong><a href="${escapeHtml(pricing.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(sourceLabel)}</a></strong></div></div>` : pricing?.unit === "per_image" ? `
    <div class="field-hint">官方图像成本价（按张计费，等待图像任务适配器）</div>
    <div class="key-detail-grid"><div><span>输入图片</span><strong>${formatMoney(pricing.input_per_image_micros)} / 张</strong></div>${(pricing.output_prices || []).map((item) => `<div><span>输出 · ${escapeHtml(item.resolution)}</span><strong>${formatMoney(item.output_per_image_micros)} / 张</strong></div>`).join("")}<div><span>来源</span><strong><a href="${escapeHtml(pricing.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(sourceLabel)}</a></strong></div></div>` : pricing?.source_url ? `
    <div class="field-hint">${escapeHtml(pricing.note || "该模型尚未录入经核验的官方价格")}</div>
    <div class="key-detail-grid"><div><span>价格状态</span><strong>待核验</strong></div><div><span>计费单位</span><strong>${escapeHtml(pricing.unit || "服务商定义")}</strong></div><div><span>服务商</span><strong>${escapeHtml(pricing.provider || "-")}</strong></div><div><span>官方来源</span><strong><a href="${escapeHtml(pricing.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(sourceLabel)}</a></strong></div></div>` : "";
  openDialog(`模型定价 · ${modelName}`, `
    <form id="model-pricing-form">
      <div class="dialog-body">
        ${reference}
        ${hasOfficialPrice ? `<div class="field"><label for="model-pricing-mode">定价方式</label><select id="model-pricing-mode" name="pricing_mode"><option value="margin" selected>按官方价格和利润率自动定价</option><option value="manual">手工设置平台售价</option></select></div><div class="field" id="model-margin-field"><label for="model-margin">目标利润率（%）</label><input id="model-margin" name="margin" type="number" min="0.01" max="99" step="0.01" value="${initialMargin}" required><small class="field-hint">按毛利率计算：平台售价 = 官方价格 ÷（1 - 利润率）。阶梯模型以第一档标准原价生成基础售价，实际结算需按请求输入 Token 命中对应成本阶梯。</small></div>` : pricing?.unit === "per_image" ? '<p class="dialog-copy">该模型按张计费，图像任务适配器和独立计价字段启用前不能发布；此处不写入 Token 售价。</p>' : `<p class="dialog-copy">该模型尚无可核验的官方价格，请打开上方官方来源核对当前模型价格后，再手工设置 LokToken 平台售价。</p>`}
        <div class="field-row"><div class="field"><label for="model-input-price">平台输入售价 / 1M Token（元）</label><input id="model-input-price" name="input_price" type="number" min="0" step="0.001" value="${microsPerThousandToYuanPerMillion(inputPriceMicros)}" required></div><div class="field"><label for="model-output-price">平台输出售价 / 1M Token（元）</label><input id="model-output-price" name="output_price" type="number" min="0" step="0.001" value="${microsPerThousandToYuanPerMillion(outputPriceMicros)}" required></div></div>
        <p class="dialog-copy">设置 LokToken 面向用户的公开售价，按人民币 / 1M Token 计费。供应商采购成本请在“渠道”中维护；新价格仅对后续请求生效，不影响已结算记录。</p>
      </div>
      <div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="save"></i><span>保存定价</span></button></div>
    </form>`);
  const modeInput = document.getElementById("model-pricing-mode");
  const marginInput = document.getElementById("model-margin");
  const inputPrice = document.getElementById("model-input-price");
  const outputPrice = document.getElementById("model-output-price");
  const updatePricePreview = () => {
    const automatic = hasOfficialPrice && modeInput.value === "margin";
    document.getElementById("model-margin-field").hidden = !automatic;
    inputPrice.readOnly = automatic;
    outputPrice.readOnly = automatic;
    if (!automatic) return;
    const margin = Math.min(99, Math.max(0.01, Number(marginInput.value || 0))) / 100;
    inputPrice.value = (Number(officialReference.input_micros) / 1_000_000 / (1 - margin)).toFixed(3);
    outputPrice.value = (Number(officialReference.output_micros) / 1_000_000 / (1 - margin)).toFixed(3);
  };
  if (hasOfficialPrice) {
    modeInput.addEventListener("change", updatePricePreview);
    marginInput.addEventListener("input", updatePricePreview);
    updatePricePreview();
  }
  document.getElementById("model-pricing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const automatic = hasOfficialPrice && data.pricing_mode === "margin";
    const payload = automatic
      ? { pricing_margin_bps: Math.round(Number(data.margin) * 100) }
      : { pricing_margin_bps: 0, input_price_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.input_price), output_price_micros_per_1k: yuanPerMillionToMicrosPerThousand(data.output_price) };
    try { await api(`/admin/models/${modelId}`, { method: "PATCH", body: JSON.stringify(payload) }); closeDialog(); toast("模型定价已更新"); await loadModels(); } catch (error) { toast(error.message, true); }
  });
}

async function channelDialog(modelId, modelName) {
  const result = await api(`/admin/models/${modelId}/channels`);
  state.channels = result.data;
  const rows = result.data.length ? result.data.map((item) => `
    <article class="channel-record">
      <div class="channel-record-header"><div><strong>${escapeHtml(item.name)}</strong><code>${escapeHtml(item.upstream_model)}</code></div><div class="channel-record-state">${activeBadge(item.active)}${channelStatusBadge(item.status)}</div></div>
      <div class="channel-record-grid">
        <div><span>供应商地址</span><strong title="${escapeHtml(item.provider_base_url)}">${escapeHtml(item.provider_base_url)}</strong><small>${escapeHtml(channelCredentialLabel(item))} · ${item.credentials_configured ? "密钥已配置" : "密钥未配置"}</small></div>
        <div><span>路由策略</span><strong>优先级 ${formatNumber(item.priority)}</strong><small>同级权重 ${formatNumber(item.weight)}</small></div>
        <div><span>供应商成本 / 1M</span><strong>输入 ${item.provider_input_cost_micros_per_1k ? formatTokenPricePerMillion(item.provider_input_cost_micros_per_1k) : "未配置"}</strong><small>输出 ${item.provider_output_cost_micros_per_1k ? formatTokenPricePerMillion(item.provider_output_cost_micros_per_1k) : "未配置"}</small></div>
        <div><span>检测状态</span><strong>${item.health_source === "provider" ? "真实检测" : item.health_source === "mock" ? "Mock 检测" : item.health_source === "catalogue" ? "目录状态" : "尚未检测"}</strong><small>连续失败 ${formatNumber(item.consecutive_failures)} 次${item.circuit_open_until ? ` · 熔断至 ${formatDate(item.circuit_open_until)}` : ""}</small></div>
      </div>
      <div class="channel-record-footer"><span>${item.circuit_open_until ? "该渠道当前处于熔断保护期" : "可通过检测确认当前上游可用性"}</span><div class="table-actions"><button class="table-button" data-action="edit-channel" data-id="${item.id}" data-model-id="${modelId}" data-model-name="${escapeHtml(modelName)}"><i data-lucide="settings-2"></i><span>编辑</span></button><button class="table-button" data-action="check-channel" data-id="${item.id}" data-model-id="${modelId}" data-model-name="${escapeHtml(modelName)}"><i data-lucide="activity"></i><span>检测</span></button><button class="table-button" data-action="toggle-channel" data-id="${item.id}" data-active="${!item.active}" data-model-id="${modelId}" data-model-name="${escapeHtml(modelName)}">${item.active ? "停用" : "启用"}</button></div></div>
    </article>
  `).join("") : '<div class="channel-empty"><i data-lucide="route"></i><strong>尚未接入渠道</strong><span>新增一个上游连接后，该模型才可用于真实调用。</span></div>';
  openDialog(`渠道管理 · ${modelName}`, `
    <div class="channel-workspace">
      <section class="channel-overview"><div class="channel-overview-heading"><div><span>已接入渠道</span><p>按优先级路由；同优先级渠道按权重分配流量。</p></div><strong>${formatNumber(result.data.length)} 个</strong></div><div class="channel-record-list">${rows}</div></section>
      <details class="channel-create-panel" ${result.data.length ? "" : "open"}>
        <summary><div><strong>新增备用渠道</strong><span>添加可用于容灾或分流的上游连接</span></div><i data-lucide="chevron-down"></i></summary>
        <form id="channel-form" class="channel-form">
          <div class="channel-form-body">
            <section class="channel-form-section"><div class="channel-form-section-heading"><div><strong>基础路由</strong><span>定义该连接指向的上游服务与模型。</span></div></div><div class="field-row"><div class="field"><label for="channel-name">渠道名称</label><input id="channel-name" name="name" required maxlength="120" placeholder="例如：华东备用线路"></div><div class="field"><label for="channel-upstream">上游模型</label><input id="channel-upstream" name="upstream_model" required maxlength="120" placeholder="例如：deepseek-v4-flash"></div></div><div class="field"><label for="channel-url">供应商地址</label><input id="channel-url" name="provider_base_url" required maxlength="500" placeholder="https://api.example.com/v1"></div></section>
            <section class="channel-form-section"><div class="channel-form-section-heading"><div><strong>凭证配置</strong><span>选择一种凭证来源；密钥明文只在提交时传输。</span></div></div><div class="channel-credential-options"><label class="channel-credential-option"><input type="radio" name="credential_mode" value="environment" checked><span><strong>使用环境变量</strong><small>由部署环境注入，适合生产服务。</small></span></label><label class="channel-credential-option"><input type="radio" name="credential_mode" value="console"><span><strong>保存 API Key</strong><small>由控制台加密保管，不会再次显示。</small></span></label></div><div id="channel-env-credential" class="field"><label for="channel-key-env">密钥环境变量</label><input id="channel-key-env" name="provider_api_key_env" maxlength="120" pattern="[A-Z][A-Z0-9_]{1,119}" placeholder="DEEPSEEK_API_KEY" required></div><div id="channel-console-credential" class="field" hidden><label for="channel-provider-key">供应商 API Key</label><input id="channel-provider-key" name="provider_api_key" type="password" autocomplete="new-password" placeholder="输入后将由服务端加密保存"></div></section>
            <section class="channel-form-section"><div class="channel-form-section-heading"><div><strong>调度策略</strong><span>数字越小越优先；仅同级渠道参与权重分流。</span></div></div><div class="field-row"><div class="field"><label for="channel-priority">优先级</label><input id="channel-priority" name="priority" type="number" min="0" max="10000" value="100" required></div><div class="field"><label for="channel-weight">同级权重</label><input id="channel-weight" name="weight" type="number" min="1" max="10000" value="100" required></div></div></section>
            <section class="channel-form-section"><div class="channel-form-section-heading"><div><strong>采购成本</strong><span>用于毛利与供应商账单核对，不等同于面向用户的平台售价。</span></div></div><div class="field-row"><div class="field"><label for="channel-provider-input-cost">输入成本 / 1M Token（元）</label><input id="channel-provider-input-cost" name="provider_input_cost" type="number" min="0" step="0.001" value="0"></div><div class="field"><label for="channel-provider-output-cost">输出成本 / 1M Token（元）</label><input id="channel-provider-output-cost" name="provider_output_cost" type="number" min="0" step="0.001" value="0"></div></div></section>
          </div>
          <div class="dialog-actions"><button class="secondary-button" type="button" data-close>取消</button><button class="primary-button" type="submit"><i data-lucide="plus"></i><span>新增渠道</span></button></div>
        </form>
      </details>
    </div>`);
  const channelForm = document.getElementById("channel-form");
  const credentialModeInputs = channelForm.querySelectorAll('input[name="credential_mode"]');
  const environmentCredential = document.getElementById("channel-env-credential");
  const consoleCredential = document.getElementById("channel-console-credential");
  const environmentInput = document.getElementById("channel-key-env");
  const consoleInput = document.getElementById("channel-provider-key");
  const syncCredentialMode = () => {
    const useEnvironment = channelForm.querySelector('input[name="credential_mode"]:checked').value === "environment";
    environmentCredential.hidden = !useEnvironment;
    consoleCredential.hidden = useEnvironment;
    environmentInput.required = useEnvironment;
    consoleInput.required = !useEnvironment;
  };
  credentialModeInputs.forEach((input) => input.addEventListener("change", syncCredentialMode));
  syncCredentialMode();
  channelForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const useEnvironment = data.credential_mode === "environment";
    const payload = {
      name: data.name,
      upstream_model: data.upstream_model,
      provider_base_url: data.provider_base_url,
      provider_api_key_env: useEnvironment ? data.provider_api_key_env : null,
      provider_api_key: useEnvironment ? null : data.provider_api_key,
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
    toast(result.healthy ? `检测通过，${result.latency_ms} ms` : result.detail, result.status !== "unavailable");
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
        <div class="field"><label for="redemption-label">福利名称</label><input id="redemption-label" name="label" required maxlength="120" placeholder="例如：新用户体验福利"></div>
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

async function deleteModel(modelId, modelName) {
  if (!window.confirm(`删除“${modelName}”？删除后模型、渠道配置和候选目录记录将移除，且不可恢复。已有调用记录的模型无法删除。`)) return;
  try {
    await api(`/admin/models/${modelId}`, { method: "DELETE" });
    toast(`模型“${modelName}”已删除`);
    await loadModels();
  } catch (error) { toast(error.message, true); }
}

async function deleteAccount(accountId, accountName) {
  if (!window.confirm(`删除“${accountName}”？删除后账户及其访问凭证将移除，且不可恢复。已有余额、调用或账务记录的账户无法删除。`)) return;
  try {
    await api(`/admin/accounts/${accountId}`, { method: "DELETE" });
    toast(`账户“${accountName}”已删除`);
    await loadAccounts();
  } catch (error) { toast(error.message, true); }
}

document.addEventListener("DOMContentLoaded", async () => {
  icons();
  document.getElementById("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await fetch("/admin/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
      const data = await result.json();
      if (!result.ok) throw new Error(apiErrorMessage(data.detail, "管理员登录失败"));
      completeAdminAuth(data);
      showApp();
      const view = adminViewFromUrl();
      primeAdminHistory(view);
      await switchView(view, { historyMode: "none" });
    } catch (error) { showAuth(error.message); }
  });
  document.getElementById("bootstrap-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("password_confirm")) {
      showAuth("两次输入的密码不一致");
      return;
    }
    try {
      const result = await fetch("/admin/auth/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Token": form.get("bootstrap_token") },
        body: JSON.stringify({ login_id: form.get("login_id"), password: form.get("password") }),
      });
      const data = await result.json();
      if (!result.ok) {
        if (result.status === 409) await loadAdminAuthMode();
        throw new Error(apiErrorMessage(data.detail, "管理员初始化失败"));
      }
      event.currentTarget.reset();
      completeAdminAuth(data);
      showApp();
      const view = adminViewFromUrl();
      primeAdminHistory(view);
      await switchView(view, { historyMode: "none" });
    } catch (error) { showAuth(error.message); }
  });
  document.querySelectorAll("[data-password-toggle]").forEach((button) => button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.passwordToggle);
    input.type = input.type === "password" ? "text" : "password";
  }));
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
  document.getElementById("model-search").addEventListener("input", (event) => { state.modelFilters.query = event.target.value; renderModels(); });
  document.querySelectorAll("[data-account-access-tab]").forEach((button) => button.addEventListener("click", () => setAccountAccessTab(button.dataset.accountAccessTab)));
  document.getElementById("model-provider-filter").addEventListener("change", (event) => { state.modelFilters.provider = event.target.value; state.modelProviderDetail = ""; renderModels(); });
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
    if (target.dataset.action === "admin-back") navigateAdminBack();
    if (target.dataset.action === "admin-refresh") switchView(state.view, { historyMode: "none" });
    if (target.dataset.modelPageBack !== undefined) {
      state.modelProviderDetail = "";
      state.modelFilters.provider = "";
      document.getElementById("model-provider-filter").value = "";
      renderModels();
      return;
    }
    if (target.dataset.modelProvider) { state.modelProviderDetail = target.dataset.modelProvider; state.modelFilters.provider = target.dataset.modelProvider; document.getElementById("model-provider-filter").value = target.dataset.modelProvider; renderModels(); }
    if (target.dataset.modelProviderMore !== undefined) { state.modelProviderDetail = "__more__"; state.modelFilters.provider = ""; document.getElementById("model-provider-filter").value = ""; renderModels(); }
    if (target.dataset.modelProviderBack !== undefined) { state.modelProviderDetail = ""; state.modelFilters.provider = ""; document.getElementById("model-provider-filter").value = ""; renderModels(); }
    if (target.dataset.action === "create-account") accountDialog();
    if (target.dataset.action === "account-detail") accountDetailDialog(target.dataset.id);
    if (target.dataset.action === "create-key") keyDialog(target.dataset.accountId || null).catch((error) => toast(error.message, true));
    if (target.dataset.action === "create-model") modelDialog();
    if (target.dataset.action === "create-provider-model") modelDialog(target.dataset.providerId);
    if (target.dataset.action === "model-detail-admin") modelAdminDetailDialog(target.dataset.id);
    if (target.dataset.action === "health-check-all") checkAllChannels().catch((error) => toast(error.message, true));
    if (target.dataset.action === "health-check-provider") checkAllChannels(target.dataset.providerId).catch((error) => toast(error.message, true));
    if (target.dataset.action === "preflight-model") preflightModel(target.dataset.id, target.dataset.name);
    if (target.dataset.action === "configure-provider") providerConnectionDialog(target.dataset.providerId);
    if (target.dataset.action === "manual-provider-balance") manualProviderBalanceDialog(target.dataset.providerId);
    if (target.dataset.action === "refresh-provider-balance") {
      const providerId = target.dataset.providerId;
      target.disabled = true;
      api(`/admin/provider-connections/${providerId}/balance/refresh`, { method: "POST", body: "{}" }).then(async (result) => {
        state.providerConnections = state.providerConnections.map((item) => item.preset_id === providerId ? result.connection : item);
        closeDialog();
        toast(result.connection.balance_status === "available" ? "上游余额已更新" : (result.connection.balance_error || "该供应商暂不支持自动余额查询"));
        await loadModels();
      }).catch((error) => { target.disabled = false; toast(error.message, true); });
    }
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
    if (target.dataset.action === "delete-model") deleteModel(target.dataset.id, target.dataset.name);
    if (target.dataset.action === "delete-account") deleteAccount(target.dataset.id, target.dataset.name);
    if (target.dataset.action === "trial-link") trialLinkDialog(target.dataset.id, target.dataset.name);
    if (target.dataset.action === "resend-invitation") {
      api(`/admin/accounts/${target.dataset.id}/invitation`, { method: "POST", body: "{}" }).then(async (result) => {
        closeDialog();
        await loadAccounts();
        const setupUrl = result.invitation?.setup_url;
        if (setupUrl) {
          openDialog("用户中心邀请已重新发送", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="send"></i><span>旧邀请已失效，请将这条新链接发送给用户。</span></div><div class="secret-box mono">${escapeHtml(setupUrl)}</div><div class="secret-actions"><button class="secondary-button" id="copy-invitation"><i data-lucide="copy"></i><span>复制邀请链接</span></button></div></div><div class="dialog-actions"><button class="primary-button" type="button" data-close>完成</button></div>`);
          document.getElementById("copy-invitation").addEventListener("click", async () => { await navigator.clipboard.writeText(setupUrl); toast("邀请链接已复制"); });
        } else toast("邀请已重新发送");
      }).catch((error) => toast(error.message, true));
    }
    if (target.dataset.action === "topup") topupDialog(target.dataset.id, target.dataset.name);
    if (target.dataset.rotateAdminKey) {
      if (!window.confirm("轮换后旧 Key 将立即失效。是否继续？")) return;
      api(`/admin/api-keys/${target.dataset.rotateAdminKey}/rotate`, { method: "POST", body: "{}" }).then(async (result) => {
        openDialog("API Key 已轮换", `<div class="dialog-body"><div class="key-secret-alert"><i data-lucide="triangle-alert"></i><span>新 Key 只展示一次，旧 Key 已失效。</span></div><div class="secret-box mono">${escapeHtml(result.key)}</div><div class="secret-actions"><button class="secondary-button" id="copy-key">复制</button></div></div><div class="dialog-actions"><button class="primary-button" data-close>完成</button></div>`);
        document.getElementById("copy-key").addEventListener("click", () => navigator.clipboard.writeText(result.key).then(() => toast("密钥已复制")));
        await loadKeys();
      }).catch((error) => toast(error.message, true));
    }
    if (target.dataset.revokeAdminKey) {
      const reason = window.prompt("请输入撤销原因（可选）：");
      if (reason === null || !window.confirm("撤销后不可重新启用。是否继续？")) return;
      api(`/admin/api-keys/${target.dataset.revokeAdminKey}`, { method: "PATCH", body: JSON.stringify({ active: false, revoke: true, revoke_reason: reason }) }).then(() => loadKeys()).then(() => toast("Key 已撤销")).catch((error) => toast(error.message, true));
    }
    if (target.dataset.toggle) toggleEntity(target.dataset.toggle, target.dataset.id, target.dataset.active === "true");
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAdminAccountMenu(); });
  window.addEventListener("popstate", (event) => {
    if (!state.token) return;
    if (event.state?.app === "admin") switchView(event.state.view, { historyMode: "none" });
    else {
      primeAdminHistory(state.view);
      switchView(state.view, { historyMode: "none" });
    }
  });
  if (state.token) {
    try { const identity = await api("/admin/auth/me"); setAdminIdentity(identity.admin); showApp(); const view = adminViewFromUrl(); primeAdminHistory(view); await switchView(view, { historyMode: "none" }); } catch (_) { showAuth("管理员会话已失效，请重新登录"); await loadAdminAuthMode(); }
  } else {
    showAuth();
    try { await loadAdminAuthMode(); } catch (error) { showAuth(error.message); }
  }
});
