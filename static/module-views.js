globalThis.CARD_HOME = (() => {
  "use strict";
  const assets = "/static/assets/";
  const modules = [
    { id: "library", name: "知识库", icon: "library", tone: "blue", action: "open-library" },
    { id: "document", name: "智能写作", icon: "notebook-pen", tone: "teal", action: "open-documents" },
    { id: "assistant", name: "企业助手", icon: "bot", tone: "amber", action: "open-chat" }
  ];
  function viewBar(direction) {
    return "";
  }
  function brand(icon) { return `<div class="brand"><span class="brand-mark">${icon("panels-top-left")}</span>企业 AI 工作台</div>`; }
  function navigation(state, icon, top = false) {
    const items = [{ id: "home", name: "工作台", icon: "layout-grid", action: "home" }, ...modules];
    return `<nav class="${top ? "card-top-links" : "card-side-links"}" aria-label="主导航">${items.map(item => `<button type="button" class="${(state.activeModule || "home") === item.id ? "is-active" : ""}" data-action="${item.action}" ${(state.activeModule || "home") === item.id ? 'aria-current="page"' : ""}>${icon(item.icon)}<span>${item.name}</span></button>`).join("")}</nav>`;
  }
  function user(ui) { return `<button class="user account-button" data-action="account" aria-label="账号与退出登录"><span class="avatar" aria-hidden="true">${ui.esc(ui.state.user.name.slice(0, 1))}</span><span><span class="user-name">${ui.esc(ui.state.user.name)}</span><small>${ui.state.user.role === "admin" ? "管理员" : "员工"}</small></span></button>`; }
  function sidebar(ui, direction) {
    const { state, icon, button } = ui;
    return `<aside class="card-sidebar">${brand(icon)}<div class="space-label">演示空间 ${icon("chevrons-up-down")}</div>${navigation(state, icon)}<div class="sidebar-section"><h2>协作</h2><button data-action="open-review">${icon("file-check-2")}待我审核<span>${state.reviews.length}</span></button><button data-action="open-tasks">${icon("list-todo")}我的待办<span>${state.tasks.length}</span></button></div><div class="card-sidebar-bottom">${state.user.role === "admin" ? button("管理后台", "settings-2", "open-admin") : ""}<small>虚构资料 / 独立演示库</small>${user(ui)}</div></aside>`;
  }
  function toolbar(ui) {
    const { state, icon, iconButton } = ui;
    const name = state.activeModule === "admin" ? "管理后台" : modules.find(item => item.id === state.activeModule)?.name || "工作台";
    return `<header class="card-toolbar"><div class="breadcrumbs"><span>演示空间</span>${icon("chevron-right")}<strong>${name}</strong></div><div class="topbar-tools"><span class="mode-indicator">离线检索</span>${iconButton("bell", "查看待办", "open-tasks")}${iconButton("user-round", "账号与退出登录", "account")}</div></header>`;
  }
  function header(ui, direction) {
    if (direction !== "tasks") return toolbar(ui);
    return `<header class="card-global-header">${brand(ui.icon)}${navigation(ui.state, ui.icon, true)}<div class="topbar-tools"><span class="mode-indicator">离线样稿</span>${ui.iconButton("settings-2", "管理后台", "open-admin")}${user()}</div></header>`;
  }
  function moduleCount(id, ui) {
    if (id === "library") return `${ui.sources.length} 份资料 / ${globalThis.WORKBENCH_VIEWS.publishedSources(ui).length} 已发布`;
    if (id === "document") return `<span data-module-document-count>${ui.state.documents.length}</span> 份文档 / 2 个模板`;
    return `${globalThis.WORKBENCH_VIEWS.publishedSources(ui).length} 份可问答资料 / 离线检索`;
  }
  function moduleBody(id, ui) {
    const { icon, state, esc } = ui;
    if (id === "library") return `<div class="module-object-lines">${globalThis.WORKBENCH_VIEWS.publishedSources(ui).slice(0, 2).map(item => `<button data-action="source" data-id="${item.id}">${icon("file-text")}<span class="module-object-title">${esc(item.title)}</span><span>${esc(item.label)}</span></button>`).join("") || '<span class="caption">暂无可用已发布资料</span>'}</div>`;
    if (id === "document") return `<div class="module-object-lines">${state.documents.length ? `<button data-action="document" data-id="${state.documents[0].id}">${icon("file-pen-line")}<span class="module-object-title">${esc(state.documents[0].title)}</span><span>${esc(state.documents[0].state)}</span></button>` : '<span class="caption">暂无文档</span>'}<button data-action="new-document" data-template="delivery">${icon("plus")}从交付说明模板起稿</button></div>`;
    return `<div class="module-object-lines"><button data-action="ask" data-question="交付验收需要准备哪些资料？">${icon("message-square")}交付验收需要哪些资料？</button><button data-action="ask" data-question="客户无法访问资料，应该怎么处理？">${icon("message-square")}客户资料访问问题</button></div>`;
  }
  function moduleCards(ui, compact = false) {
    return `<div class="application-grid${compact ? " compact-apps" : ""}">${modules.map(item => `<article class="application-card tone-${item.tone}"><button class="module-entry" data-action="${item.action}"><span class="module-icon">${ui.icon(item.icon)}</span><span><h2>${item.name}</h2><span class="module-count">${moduleCount(item.id, ui)}</span></span>${ui.icon("arrow-up-right")}</button>${!compact ? moduleBody(item.id, ui) + `<div class="module-footer"><span>${item.id === "library" ? "资料目录" : item.id === "document" ? "文档与模板" : "会话与待办"}</span><button class="module-open" data-action="${item.action}">打开${item.name}${ui.icon("arrow-right")}</button></div>` : ""}</article>`).join("")}</div>`;
  }
  function documentGrid(ui, large = false) {
    const tab = ui.state.documentTab;
    const docs = ui.state.documents.filter(item => tab === "mine" ? item.owner_id === ui.state.user.id : tab === "shared" ? item.is_shared : tab === "review" ? ui.state.reviews.some(review => review.artifact_id === item.id) : true);
    return docs.map(item => {
      const index = item.revision === 1 ? ["proposal", "plan", "service"].indexOf(item.id) : -1;
      return `<button class="work-document${large ? " large-document" : ""}" data-action="document" data-id="${item.id}"><div class="work-document-cover ${index === 1 ? "cover-teal" : index === 2 ? "cover-warm" : ""}">${index >= 0 ? `<img src="${assets}document-${index + 1}.png" alt="${ui.esc(item.title)}正文预览" width="520" height="650">` : `<div class="local-draft-cover">${ui.icon("file-pen-line")}<span>${ui.esc(item.title)}</span></div>`}</div><div class="work-document-body"><h3>${ui.esc(item.title)}</h3><div class="work-document-state">${ui.status(item.state)}<span>${item.version}</span></div><p class="document-owner">${ui.icon(item.owner_id === ui.state.user.id ? "file-pen-line" : item.access === "reviewer" ? "file-check-2" : "eye")}<span>${ui.esc(item.owner_id === ui.state.user.id ? "我创建的" : item.owner_name || "同事稿件")} / ${item.owner_id === ui.state.user.id ? "可编辑" : item.access === "reviewer" ? "指定审阅" : "共享只读"}</span></p><p>${ui.esc(item.updated)}</p></div></button>`;
    }).join("") || `<p class="caption empty-documents">${({ mine: "暂无本人稿件", shared: "暂无共享给我的稿件", review: "暂无待我审核稿件" })[tab] || "暂无可见稿件"}</p>`;
  }
  function documentTabs(ui) {
    return `<div class="tabs" role="tablist" aria-label="稿件访问范围">${[["all", "全部"], ["mine", "我的稿件"], ["shared", "共享给我"], ["review", "待我审核"]].map(([id, label]) => `<button type="button" role="tab" aria-selected="${ui.state.documentTab === id}" tabindex="${ui.state.documentTab === id ? 0 : -1}" data-action="document-tab" data-id="${id}">${label}</button>`).join("")}</div>`;
  }
  function taskCards(ui) {
    return ui.state.tasks.filter(item => item.status !== "已完成").map(item => `<button class="work-task" data-action="task" data-id="${item.id}"><span class="task-type">${ui.icon(item.kind === "review" ? "file-check-2" : "list-todo")}${item.kind === "review" ? "文档审核" : "资料补充"}</span><h3>${ui.esc(item.title)}</h3><p>${ui.esc(item.note)}</p><div class="task-card-footer">${ui.status(item.status)}<span>${ui.esc(item.due)}</span>${ui.icon("chevron-right")}</div></button>`).join("") || `<p class="caption empty-documents">暂无待处理事项</p>`;
  }
  function taskBoard(ui) {
    return ["待处理", "进行中", "已完成"].map((name, index) => {
      const tasks = ui.state.tasks.filter(item => item.status === name);
      return `<section class="task-lane"><header class="lane-heading"><span class="lane-dot lane-${index}"></span><h3>${name}</h3><span>${tasks.length}</span></header><div class="lane-items">${tasks.map(item => `<button class="work-task" data-action="task" data-id="${item.id}"><span class="task-type">${ui.icon(item.kind === "review" ? "file-check-2" : "clipboard-list")}${item.kind === "review" ? "文档审核" : "资料补充"}</span><h3>${ui.esc(item.title)}</h3><p>${ui.esc(item.note)}</p><div class="task-card-footer"><span class="avatar">演</span><span>我</span><span class="task-due">${ui.esc(item.due)}</span>${ui.icon("chevron-right")}</div></button>`).join("") || `<p class="lane-empty">暂无事项</p>`}</div></section>`;
    }).join("");
  }
  function homeSearch(ui) { return `<form class="home-search" data-home-search>${ui.icon("search")}<input type="search" name="query" aria-label="搜索知识库资料" placeholder="搜索资料名称或关键词" maxlength="100"><button type="submit" aria-label="搜索资料" data-tooltip="搜索资料">${ui.icon("arrow-right")}</button></form>`; }
  function pageTitle(ui, title, subtitle = "演示空间 / 员工工作台") { return `<div class="card-page-heading"><div><h1 tabindex="-1">${title}</h1><p>${subtitle}</p></div>${ui.button("新建文档", "plus", "new-document", true)}</div>`; }
  function modulesHome(ui) {
    return `${pageTitle(ui, "工作台")}${homeSearch(ui)}<section class="open-section"><div class="section-heading"><h2>功能模块</h2><span class="caption">3 个应用</span></div>${moduleCards(ui)}</section><div class="work-overview"><section class="open-section"><div class="section-heading"><h2>继续工作</h2><button class="text-button" data-action="open-documents">全部文档 ${ui.icon("arrow-up-right")}</button></div><div class="work-document-grid" data-card-documents>${documentGrid(ui)}</div></section><section class="open-section"><div class="section-heading"><h2>我的待办<span class="count" data-task-count>${ui.state.tasks.filter(item => item.status !== "已完成").length}</span></h2><button class="text-button" data-action="open-tasks">全部 ${ui.icon("arrow-up-right")}</button></div><div class="work-task-stack" data-card-tasks>${taskCards(ui)}</div></section></div>`;
  }
  function tasksHome(ui) {
    return `${pageTitle(ui, "我的工作", "演示空间 / 任务视图")}<section class="open-section">${moduleCards(ui, true)}</section><section class="open-section"><div class="section-heading"><h2>我的待办</h2><span class="caption">负责人：我</span></div><div class="task-board" data-card-task-board>${taskBoard(ui)}</div></section><section class="open-section recent-task-documents"><div class="section-heading"><h2>最近文档</h2><button class="text-button" data-action="open-documents">全部文档 ${ui.icon("arrow-up-right")}</button></div><div class="work-document-grid task-document-grid" data-card-documents>${documentGrid(ui)}</div></section>`;
  }
  function documentsHome(ui) {
    return `${pageTitle(ui, "文档空间", "演示空间 / 我的文档")}<section class="open-section">${moduleCards(ui, true)}</section><section class="open-section"><div class="document-space-heading"><div class="section-heading"><h2>我的文档<span class="count" data-document-count>${ui.state.documents.length}</span></h2></div>${documentTabs(ui)}</div><div class="work-document-grid large-grid" data-card-documents data-large-documents>${documentGrid(ui, true)}</div></section><section class="open-section document-tasks-strip"><div class="section-heading"><h2>待我处理</h2><button class="text-button" data-action="open-tasks">全部待办 ${ui.icon("arrow-up-right")}</button></div><div class="work-task-inline" data-card-tasks>${taskCards(ui)}</div></section>`;
  }
  function workspace(ui) {
    const { state, button, icon } = ui;
    const module = modules.find(item => item.id === state.activeModule);
    let content;
    if (module.id === "library") content = `<div class="module-workspace library-workspace">${ui.libraryBody(true)}</div>`;
    else if (module.id === "document") content = `<div class="document-workspace-toolbar">${documentTabs(ui)}${button("从模板新建", "plus", "new-document", true)}</div><div class="work-document-grid large-grid" data-card-documents data-large-documents>${documentGrid(ui, true)}</div>`;
    else content = `<div class="module-workspace assistant-workspace"><section class="embedded-chat"><div class="conversation-heading"><div><h2>${icon("bot")}企业助手</h2><p class="conversation-title">${ui.esc(state.conversation?.title || (state.activeConversation ? "读取会话" : "历史问答"))}</p></div><div class="conversation-tools">${state.conversation ? ui.iconButton("pencil", "重命名会话", "rename-chat") + ui.iconButton(state.conversation.status === "archived" ? "archive-restore" : "archive", state.conversation.status === "archived" ? "恢复会话" : "归档会话", "archive-chat") : ""}${button("新会话", "square-pen", "new-chat")}</div></div><div class="chat-history" data-chat-history aria-live="polite">${ui.conversationHistory()}</div>${ui.composer()}</section><aside class="embedded-sources">${ui.conversationList()}<div class="section-heading reference-heading"><h2>参考资料</h2></div><div data-reference-list>${ui.referenceList()}</div></aside></div>`;
    return `<div class="workspace-title"><button class="back-home" data-action="home">${icon("arrow-left")}工作台</button><h1 tabindex="-1">${module.name}</h1><span class="caption">虚构资料 / 离线样稿</span></div>${content}`;
  }
  function render(ui) {
    const { direction, state } = ui;
    const content = ui.screenOverride || (state.activeModule ? workspace(ui) : modulesHome(ui));
    return `<div class="card-shell">${sidebar(ui, direction)}<div class="card-content-shell">${header(ui, direction)}<main class="card-main ${state.activeModule ? "in-workspace" : ""}" id="main">${content}<footer class="card-page-footer">${ui.icon("shield-check")}虚构资料 / 离线规则 / 数据保存于当前演示库</footer></main></div></div>`;
  }
  return Object.freeze({ render, documentGrid, taskCards, taskBoard, modules });
})();
