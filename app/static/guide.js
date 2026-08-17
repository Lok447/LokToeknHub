function initGuide() {
  const audience = window.location.pathname.endsWith("/admin") ? "admin" : "user";
  const admin = audience === "admin";
  document.title = admin ? "LokToken管理文档" : "LokToken用户文档";
  document.getElementById("guide-eyebrow").textContent = admin ? "LOKTOKEN / ADMIN GUIDE" : "LOKTOKEN / USER GUIDE";
  document.getElementById("guide-title").textContent = admin ? "管理文档" : "用户文档";
  document.getElementById("guide-summary").textContent = admin
    ? "面向平台运营与模型服务管理，覆盖模型、账户、API、额度发放、运营审计和生产上线检查。"
    : "面向模型服务使用者，覆盖注册、模型选择、额度、API Key、统一调用和请求记录。";
  document.getElementById("guide-console-link").href = admin ? "/" : "/portal";
  document.getElementById("guide-console-link").textContent = admin ? "返回管理控制台" : "返回用户中心";
  document.querySelectorAll("[data-guide]").forEach((section) => { section.hidden = section.dataset.guide !== audience; });
  if (window.lucide) window.lucide.createIcons();
}

document.addEventListener("DOMContentLoaded", initGuide);
