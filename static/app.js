(() => {
  "use strict";
  const V = globalThis.WORKBENCH_VIEWS;
  const W = globalThis.WORKBENCH_WRITING_VIEWS;
  const A = globalThis.WORKBENCH_MANAGEMENT_VIEWS;
  const R = globalThis.WORKBENCH_RICH_VIEWS;
  const root = document.getElementById("app");
  const dialog = document.getElementById("workspace-dialog");
  const state = {
    user: null, loginOptions: { demo: false, accounts: [] }, loginUsername: "", sources: [], imports: [], importTargets: {}, importUsage: null, documents: [], reviews: [], tasks: [], messages: [], users: [],
    activeModule: null, page: "home", documentTab: "all", sourceQuery: "", sourceFilter: "all",
    editor: null, dirty: false, saving: false, conflict: false, historyMode: false, autosave: null, recovery: null,
    editorPanel: "body", undo: [], baseline: "", currentRevision: 0, newTemplate: "solution",
    adminTab: "sources", adminData: null, publications: [], adminFeedback: [], feedbackFilter: "open",
    templates: [], adminTemplates: [], newDocumentDraft: null, selectedBlock: null,
    categories: [], sourceCategory: "all", adminCategories: [], adminUsers: [], catalogTab: "templates",
    accountQuery: "", accountFilter: "all", adminUsersError: "", adminCategoriesError: "",
    conversations: [], legacyMessages: [], activeConversation: null, conversation: null,
    nextChatCursor: null, currentChatCursor: null, chatLoading: false, chatError: "",
    chatDrafts: {}, pendingChats: {}, chatSending: null, chatCreating: false
  };
  const names = { draft: "草稿", pending: "待审核", approved: "已通过", rejected: "已退回", superseded: "已失效", historical: "历史修订", in_progress: "进行中", completed: "已完成", canceled: "已取消" };
  const keys = new Map();
  let toastTimer, focusBeforeDialog, pendingNavigation, currentSource, pendingPreview, currentPublication, lastInput;
  let importTimer, currentImport, importGeneration = 0;
  let templateDraft, currentSuggestion;
  let chatGeneration = 0, chatEpoch = 0, createChatKey;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const icon = name => `<i data-lucide="${esc(name)}" aria-hidden="true"></i>`;
  const button = (text, symbol, action, primary = false, attributes = "") => `<button type="button" class="button${primary ? " primary" : ""}" data-action="${action}" ${attributes}>${icon(symbol)}${esc(text)}</button>`;
  const iconButton = (symbol, title, action, attributes = "") => `<button type="button" class="icon-button" data-action="${action}" data-tooltip="${esc(title)}" aria-label="${esc(title)}" ${attributes}>${icon(symbol)}</button>`;
  const status = value => `<span class="status ${["待审核", "待处理", "已退回", "未发布"].includes(value) ? "pending" : ["草稿", "已撤回", "已取消", "已失效", "已停用"].includes(value) ? "draft" : ""}">${esc(value)}</span>`;
  const label = value => names[value] || value;
  const formatDate = value => value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) : "";
  const clone = value => JSON.parse(JSON.stringify(value));
  const autosaver = globalThis.WORKBENCH_AUTOSAVE.create({
    write: (id, payload, key) => api(`/artifacts/${id}/draft`, payload, { key }),
    onState: value => {
      state.autosave = value;
      if (R.editable(state.editor, state.historyMode) && (value.hasDraft || ["waiting", "saving", "error", "conflict", "expired"].includes(value.phase))) state.dirty = true;
      if (value.phase === "conflict") state.conflict = true;
      updateSaveState();
    }
  });
  function icons() { globalThis.lucide?.createIcons({ attrs: { "stroke-width": 1.7 } }); }
  function toast(message) {
    const element = document.getElementById("toast");
    element.textContent = message;
    element.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => element.classList.remove("visible"), 5000);
  }
  function ui() {
    const context = { direction: "modules", state, sources: state.sources, esc, icon, iconButton, button, status, label, formatDate };
    context.libraryBody = () => V.library(context);
    context.conversationHistory = () => V.conversation(context);
    context.composer = () => V.composer(context);
    context.referenceList = () => V.references(context);
    context.conversationList = () => V.conversationList(context);
    if (state.page === "editor") context.screenOverride = W.editorInfo(context) + V.editor(context);
    if (state.page === "reviews") context.screenOverride = V.queue(context);
    if (state.page === "admin") context.screenOverride = V.admin(context);
    if (state.page === "tasks") context.screenOverride = `<div class="workspace-title"><button class="back-home" data-action="home">${icon("arrow-left")}工作台</button><h1 tabindex="-1">我的待办</h1>${button("新建待办", "plus", "new-task", true)}</div><div class="task-list-grid">${state.tasks.map(task => `<button class="work-task" data-action="task" data-id="${task.id}"><span class="task-type">${icon(task.kind === "review" ? "file-check-2" : "list-todo")}${task.kind === "review" ? "文档审核" : esc(task.assignee_name)}</span><h3>${esc(task.title)}</h3><p>${esc(task.note)}</p><div class="task-card-footer">${status(task.status)}<span>${esc(task.due)}</span></div></button>`).join("") || '<p class="empty">暂无待办</p>'}</div>`;
    return context;
  }
  function render(focus = false) {
    root.innerHTML = state.user ? CARD_HOME.render(ui()) : V.login(ui());
    icons();
    if (state.saving) root.querySelectorAll("input,textarea,select,button").forEach(item => { item.disabled = true; });
    if (focus) {
      const heading = root.querySelector("h1");
      heading?.setAttribute("tabindex", "-1");
      heading?.focus({ preventScroll: true });
      window.scrollTo({ top: 0 });
    }
  }
  async function api(path, body, options = {}) {
    const route = "/api/workbench" + path;
    const request = { method: options.method || (body === undefined ? "GET" : "POST"), credentials: "same-origin", headers: {} };
    const operation = body === undefined || options.key || options.multipart || options.sensitive || path === "/session" || path === "/session/logout" ? "" : request.method + route + JSON.stringify(body);
    if (body !== undefined) {
      if (operation && !keys.has(operation)) keys.set(operation, crypto.randomUUID());
      request.headers = { "X-CSRF-Token": state.user?.csrf || "", "Idempotency-Key": options.key || keys.get(operation) || crypto.randomUUID() };
      if (options.multipart && options.parent) Object.assign(request.headers, { "X-Source-Parent": options.parent.id, "X-Source-Version": String(options.parent.version) });
      if (!options.multipart) request.headers["Content-Type"] = "application/json";
      request.body = options.multipart ? body : JSON.stringify(body);
    }
    let response;
    try { response = await fetch(route, request); }
    catch { throw new Error("网络连接失败，本页修改仍保留。请确认连接后重试同一操作。"); }
    let result;
    try { result = await response.json(); }
    catch { throw new Error("服务返回异常，本页修改仍保留。"); }
    if (operation && response.status < 500) keys.delete(operation);
    if (!response.ok) {
      const error = new Error(result.error?.message || "请求失败，请稍后重试。");
      error.code = result.error?.code;
      error.status = response.status;
      if (response.status === 401 && state.user && !options.login) reauthenticate();
      throw error;
    }
    return result.data;
  }
  async function records() {
    const [sources, docs, reviews, tasks, messages, users, imports, usage, templates, categories, conversations] = await Promise.all(["/sources", "/artifacts", "/reviews", "/tasks", "/messages", "/users", "/imports", "/imports/usage", "/templates", "/categories", "/conversations"].map(path => api(path)));
    state.sources = sources.map(item => ({ ...item, status: V.publication[item.publication] }));
    state.documents = docs.map(item => ({ ...item, state: label(item.status), version: "r" + item.revision, updated: formatDate(item.updated_at) }));
    state.reviews = reviews;
    state.tasks = [...reviews.map(item => ({ ...item, title: "审核 " + item.title, kind: "review", status: "待处理", note: item.author_name + " / r" + item.revision, due: "待审核" })), ...tasks.map(item => ({ ...item, kind: "task", rawStatus: item.status, status: item.status === "pending" ? "待处理" : label(item.status), due: item.due_date || "未设期限" }))];
    state.legacyMessages = messages;
    if (!state.activeConversation) state.messages = messages;
    state.conversations = conversations;
    state.users = users;
    state.imports = imports;
    state.importUsage = usage;
    state.templates = templates;
    state.categories = categories;
  }
  function stopImportPoll() { clearTimeout(importTimer); currentImport = null; importGeneration++; }
  function modal(title, body, footer = "", options = {}) {
    stopImportPoll();
    if (!dialog.open) focusBeforeDialog = document.activeElement;
    dialog.dataset.locked = options.locked ? "true" : "false";
    dialog.innerHTML = `<header class="dialog-header"><h2 id="dialog-title">${esc(title)}</h2>${options.locked ? "" : iconButton("x", "关闭", "close-dialog")}</header><div class="dialog-body">${body}<p class="form-error" data-dialog-error role="alert"></p></div><footer class="dialog-footer">${footer || button("关闭", "x", "close-dialog")}</footer>`;
    if (!dialog.open) dialog.showModal();
    icons();
    (dialog.querySelector('input:not([type="hidden"]),textarea,select') || dialog.querySelector("button"))?.focus();
  }
  function closeDialog() {
    if (dialog.dataset.locked === "true") return;
    dialog.close();
    stopImportPoll();
    currentSource = null;
    currentPublication = null;
    currentSuggestion = null;
    templateDraft = null;
    if (focusBeforeDialog?.isConnected) focusBeforeDialog.focus();
  }
  function formButton(text, symbol, id, attributes = "") { return `<button type="submit" form="${id}" class="button primary" ${attributes}>${icon(symbol)}${esc(text)}</button>`; }
  function dialogError(error) { const field = dialog.querySelector("[data-dialog-error]"); if (field) field.textContent = error.message; }
  async function finishNavigation(callback) {
    await callback();
    if (state.page !== "editor") autosaver.reset(null);
  }
  async function navigate(callback) {
    if (state.saving) return;
    if (state.dirty && R.editable(state.editor, state.historyMode)) {
      try { await autosaver.flush(); }
      catch { /* The confirmation below preserves the last acknowledged server draft. */ }
      if (autosaver.safe()) return finishNavigation(callback);
    }
    if (state.dirty) {
      pendingNavigation = callback;
      autosaver.pause(true);
      modal("尚有未同步修改", "<p>本页有尚未确认同步的修改。离开会放弃这些本页内容；此前已同步的工作草稿和正式修订仍保留。</p>", button("继续编辑", "arrow-left", "close-dialog") + button("放弃本页并离开", "log-out", "confirm-navigation", true));
      return;
    }
    await finishNavigation(callback);
  }
  async function page(name, module = null) {
    await navigate(async () => {
      await records();
      state.page = name;
      state.activeModule = module;
      state.editor = null;
      state.dirty = false;
      if (module === "assistant" && state.activeConversation) return loadConversation(state.activeConversation);
      render(true);
    });
  }
  function snapshot() { return JSON.stringify({ title: state.editor.title, content_json: state.editor.content_json, citations: state.editor.citations, reference_details: state.editor.reference_details, fields: state.editor.fields || {} }); }
  function draftPayload() { return clone({ title: state.editor.title, content_json: state.editor.content_json, citations: state.editor.citations, fields: state.editor.fields || {} }); }
  function setBaseline() {
    state.baseline = snapshot(); state.dirty = false; state.undo = []; state.conflict = false; state.recovery = null; lastInput = null;
    autosaver.reset(R.editable(state.editor, state.historyMode) ? { id: state.editor.id, baseRevision: state.editor.revision, version: state.editor.draft_version || 0, snapshot: draftPayload(), hasDraft: false } : null);
  }
  function pushUndo() { state.undo.push(snapshot()); if (state.undo.length > 40) state.undo.shift(); lastInput = null; }
  function updateSaveState() {
    if (!state.editor || state.page !== "editor") return;
    const indicator = root.querySelector(".editor-save-state");
    if (indicator) { indicator.textContent = V.saveLabel(ui()); indicator.classList.toggle("is-dirty", state.dirty); }
    const notice = root.querySelector("[data-autosave-notice]");
    if (notice) { notice.innerHTML = V.autosaveNotice(ui()); icons(); }
    const exportButton = root.querySelector('[data-action="export-document"]');
    if (exportButton) exportButton.disabled = !state.editor.can_export || state.dirty || state.historyMode;
    const info = root.querySelector(".writing-info");
    if (info) { info.outerHTML = W.editorInfo(ui()); icons(); }
  }
  function markDirty() {
    state.dirty = snapshot() !== state.baseline || Boolean(state.autosave?.hasDraft);
    if (R.editable(state.editor, state.historyMode)) autosaver.change(draftPayload());
    updateSaveState();
  }
  async function openDocument(id, revision) {
    await navigate(async () => {
      let doc;
      try { doc = await api(`/artifacts/${id}${revision ? "/revisions/" + revision : ""}`); }
      catch (error) {
        if (error.status === 404) {
          state.documents = state.documents.filter(item => item.id !== id);
          if (state.editor?.id === id) {
            state.editor = null; state.dirty = false; state.baseline = ""; state.undo = [];
            state.page = "module"; state.activeModule = "document"; closeDialog();
          }
          render();
        }
        throw error;
      }
      state.currentRevision = doc.current_revision || doc.revision;
      state.editor = doc;
      state.page = "editor";
      state.activeModule = "document";
      state.historyMode = Boolean(revision && revision !== state.currentRevision);
      state.editorPanel = "body";
      state.selectedBlock = doc.content_json.blocks.find(item => item.type === "paragraph")?.id || doc.content_json.blocks[0]?.id;
      setBaseline();
      if (R.editable(doc, state.historyMode)) {
        let draft;
        try {
          draft = await api(`/artifacts/${id}/draft`);
          if (draft.current_revision !== doc.revision) throw Object.assign(new Error("正式修订在读取时发生变化，请重新打开文档；工作草稿尚未恢复。"), { code: "REVISION_CONFLICT" });
        }
        catch (error) { autosaver.pause(true); state.conflict = true; render(true); throw error; }
        if (draft.has_draft && !draft.stale && draft.draft.content_json.schema_version === 1) {
          Object.assign(doc, draft.draft, { reference_details: draft.reference_details });
          state.dirty = true;
          state.recovery = { ...draft, restored: true };
          autosaver.reset({ id, baseRevision: doc.revision, version: draft.draft_version, snapshot: draftPayload(), hasDraft: true });
        } else if (draft.has_draft) {
          state.recovery = draft; state.conflict = true;
          autosaver.reset({ id, baseRevision: doc.revision, version: draft.draft_version, snapshot: draftPayload(), hasDraft: true });
          autosaver.pause(true);
        } else autosaver.reset({ id, baseRevision: doc.revision, version: draft.draft_version, snapshot: draftPayload(), hasDraft: false });
      }
      render(true);
    });
  }
  async function saveDocument() {
    if (!R.editable(state.editor, state.historyMode)) { toast("当前稿件只读或编辑器不支持此格式，原结构未覆盖。"); return; }
    if (autosaver.state().composing) { toast("请先完成当前文字输入，再保存修订。"); return; }
    if (!state.dirty) { toast("当前修改已保存"); return; }
    const doc = state.editor;
    state.saving = true;
    render();
    try {
      await autosaver.flush({ retry: true });
      const payload = { ...draftPayload(), base_revision: doc.revision, base_draft_version: autosaver.state().version };
      await api(`/artifacts/${doc.id}/revisions`, payload);
      state.editor = await api(`/artifacts/${doc.id}`);
      state.currentRevision = state.editor.revision;
      setBaseline();
      await records();
      toast("修订已保存到演示库");
    } catch (error) {
      if (["REVISION_CONFLICT", "DRAFT_CONFLICT"].includes(error.code)) state.conflict = true;
      toast(error.message);
    } finally { state.saving = false; render(); }
  }
  async function source(id) {
    currentSource = await api("/sources/" + id);
    let footer = button("关闭", "x", "close-dialog");
    if (currentSource.can_share) footer += button("共享范围", "users", "source-sharing");
    if (currentSource.can_add_version) footer += button("文本新版本", "file-plus-2", "source-version-text") + button("上传新版本", "upload", "source-version-file");
    if (currentSource.owner_id === state.user.id && currentSource.publication !== "published") footer += button("申请发布", "send", "publication-request", true);
    if (state.user.role === "admin" && currentSource.publication === "published") footer += button("撤回发布", "archive-x", "withdraw-source", false, `data-id="${id}"`);
    modal(currentSource.title, V.sourceDetail(ui(), currentSource), footer);
  }
  async function evidence(mid, eid) {
    const item = await api(`/messages/${mid}/evidence/${eid}`);
    currentSource = null;
    modal(item.title, V.evidenceDetail(ui(), item), button("关闭", "x", "close-dialog") + button("查看资料全篇", "file-text", "source", false, `data-id="${item.source_id}"`));
    dialog.querySelector("[data-evidence-target]")?.focus();
  }
  async function refreshConversation() {
    if (state.activeConversation) return loadConversation(state.activeConversation, state.currentChatCursor);
    const generation = ++chatGeneration, epoch = chatEpoch;
    const messages = await api("/messages");
    if (generation !== chatGeneration || epoch !== chatEpoch) return;
    state.legacyMessages = messages; state.messages = messages;
    render();
  }
  function chatDraft() {
    const id = state.activeConversation || "legacy";
    return state.chatDrafts[id] || (state.chatDrafts[id] = { text: "", scope: "all", newQuestion: false });
  }
  async function loadConversation(id, cursor = null) {
    const generation = ++chatGeneration, epoch = chatEpoch;
    Object.assign(state, { activeConversation: id || null, conversation: null, messages: [], chatError: "", chatLoading: true, nextChatCursor: null, currentChatCursor: cursor });
    render();
    try {
      const result = await api(id ? `/conversations/${id}/messages${cursor ? "?cursor=" + encodeURIComponent(cursor) : ""}` : "/messages");
      if (generation !== chatGeneration || epoch !== chatEpoch) return;
      if (id) {
        Object.assign(state, { conversation: result.conversation, messages: result.messages, nextChatCursor: result.next_cursor });
        state.conversations = state.conversations.map(item => item.id === id ? result.conversation : item);
      } else { state.legacyMessages = result; state.messages = result; }
    } catch (error) {
      if (generation !== chatGeneration || epoch !== chatEpoch) return;
      state.chatError = error.message;
    } finally {
      if (generation === chatGeneration && epoch === chatEpoch) { state.chatLoading = false; if (state.activeModule === "assistant") render(); }
    }
  }
  async function createChat() {
    if (state.chatCreating) throw new Error("正在创建会话，请稍候。");
    state.chatCreating = true;
    const generation = chatGeneration, epoch = chatEpoch;
    const key = createChatKey || (createChatKey = crypto.randomUUID());
    let result;
    try { result = await api("/conversations", { title: "新会话" }, { key }); }
    catch (error) { if (error.status && error.status < 500 && error.status !== 401) createChatKey = null; throw error; }
    finally { if (epoch === chatEpoch) state.chatCreating = false; }
    if (epoch !== chatEpoch) return null;
    createChatKey = null;
    state.conversations = [result, ...state.conversations.filter(item => item.id !== result.id)].slice(0, 50);
    if (generation === chatGeneration && state.page === "module" && state.activeModule === "assistant") await loadConversation(result.id);
    return result;
  }
  async function sendChat(id) {
    const intent = state.pendingChats[id];
    if (!intent || state.chatSending) return;
    const epoch = chatEpoch, generation = chatGeneration;
    state.chatSending = id; render();
    let result;
    try { result = await api(`/conversations/${id}/messages`, intent.payload, { key: intent.key }); }
    catch (error) {
      if (epoch !== chatEpoch) return;
      if (error.status && error.status < 500 && error.status !== 401) delete state.pendingChats[id];
      if (error.status === 409 && state.activeConversation === id && generation === chatGeneration) await loadConversation(id);
      throw error;
    } finally {
      if (epoch === chatEpoch) { state.chatSending = null; if (state.activeModule === "assistant") render(); }
    }
    if (epoch !== chatEpoch) return;
    delete state.pendingChats[id];
    const draft = state.chatDrafts[id];
    if (draft?.text === intent.payload.text) state.chatDrafts[id] = { text: "", scope: draft.scope, newQuestion: false };
    state.conversations = [result.conversation, ...state.conversations.filter(item => item.id !== id)];
    if (generation === chatGeneration && state.activeConversation === id && state.page === "module" && state.activeModule === "assistant") {
      await loadConversation(id);
      root.querySelector("[data-chat-history]")?.lastElementChild?.scrollIntoView({ block: "nearest" });
    } else if (state.activeModule === "assistant") render();
  }
  function showImport(job) {
    let footer = button("关闭", "x", "close-dialog");
    if (["queued", "running", "canceling"].includes(job.status)) footer += button("刷新状态", "refresh-cw", "import-job", false, `data-id="${job.id}"`);
    if (["queued", "running", "preview"].includes(job.status)) footer += button("取消导入", "circle-x", "cancel-import", false, `data-id="${job.id}"`);
    if (["queued", "preview", "failed", "interrupted", "canceled"].includes(job.status)) footer += button("删除文件", "trash-2", "delete-import", false, `data-id="${job.id}" data-destructive`);
    if (["failed", "interrupted", "canceled"].includes(job.status) && job.attempts < 3) footer += button(job.status === "canceled" ? "恢复解析" : "重新解析", "refresh-cw", "retry-import", true, `data-id="${job.id}"`);
    if (job.status === "preview") footer += formButton("确认私有入库", "check", "import-confirm-form");
    if (job.status === "committed") footer += button("查看资料", "file-text", "source", true, `data-id="${job.source_id}"`);
    modal("文件导入 / " + V.importNames[job.status], V.importPreview(ui(), job), footer);
    currentImport = job.id;
    scheduleImportPoll(job);
  }
  function scheduleImportPoll(job) {
    if (!["queued", "running", "canceling"].includes(job.status)) return;
    const generation = importGeneration;
    importTimer = setTimeout(async () => {
      try {
        const latest = await api("/imports/" + job.id);
        if (generation !== importGeneration || currentImport !== job.id || !dialog.open) return;
        state.imports = state.imports.map(item => item.id === latest.id ? latest : item);
        if (latest.status !== job.status || latest.updated_at !== job.updated_at) {
          const usage = await api("/imports/usage");
          if (generation !== importGeneration || currentImport !== job.id || !dialog.open) return;
          state.importUsage = usage;
          render(); showImport(latest);
        }
        else scheduleImportPoll(latest);
      } catch (error) {
        if (generation === importGeneration && currentImport === job.id) dialogError(error);
      }
    }, 1000);
  }
  async function openImport(id) {
    stopImportPoll();
    const generation = importGeneration;
    const [job, usage] = await Promise.all([api("/imports/" + id), api("/imports/usage")]);
    if (generation === importGeneration) { state.importUsage = usage; render(); showImport(job); }
  }
  async function manageImport(id, kind) {
    stopImportPoll();
    const generation = importGeneration;
    const job = await api("/imports/" + id);
    if (generation !== importGeneration) return;
    if (job.source_id || job.status === "committed") { showImport(job); return toast("文件已入库，原件受保护，不能在导入记录中删除或取消。"); }
    if (kind === "delete" && ["running", "canceling"].includes(job.status)) { showImport(job); return toast("请先取消导入，等待后台确认结束后再删除。"); }
    const cancel = kind === "cancel";
    modal(cancel ? "取消本次导入" : "删除未入库文件", cancel ? V.importCancel(ui(), job) : V.importDelete(ui(), job), button("返回记录", "arrow-left", "import-job", false, `data-id="${id}"`) + formButton(cancel ? "确认取消" : "确认删除", cancel ? "circle-x" : "trash-2", cancel ? "import-cancel-form" : "import-delete-form", cancel ? "" : "data-destructive"));
  }
  function publicationForm() {
    const admins = state.users.filter(item => item.role === "admin");
    modal("申请发布资料", `<form id="publication-form" data-form="publication"><p class="notice">${esc(currentSource.title)} / ${esc(currentSource.label)}</p><label class="form-field">审核管理员<select name="admin_id">${admins.map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join("")}</select></label><label class="form-field">发布范围<select name="scope"><option value="internal">全体员工</option><option value="selected">指定同事</option><option value="private">仅自己</option></select></label><fieldset class="source-picker"><legend>指定同事（仅在该范围生效）</legend>${state.users.filter(item => item.id !== state.user.id).map(item => `<label><input type="checkbox" name="shared_user_ids" value="${item.id}"><span>${esc(item.name)}</span></label>`).join("")}</fieldset><p class="notice">申请提交后，所选管理员可以查看本份资料，供发布审阅。发布后范围同步到全部历史发布版，未发布草稿保持私有或其原范围；新检索切换到本版。</p></form>`, button("取消", "x", "close-dialog") + formButton("提交申请", "send", "publication-form"));
  }
  function reviewForm(id) {
    modal("审核当前修订", `<form id="review-decision-form" data-form="review-decision" data-id="${id}"><p class="notice">${esc(state.editor.title)} / r${state.editor.revision}</p><label class="form-field">审核结果<select name="decision"><option value="approved">通过</option><option value="rejected">退回修改</option></select></label><label class="form-field">审核意见<textarea name="note" maxlength="2000" rows="4"></textarea></label></form>`, button("取消", "x", "close-dialog") + formButton("确认审核结果", "check", "review-decision-form"));
  }
  function newTask() {
    pendingPreview = null;
    modal("新建待办", `<form id="task-form" data-form="task"><label class="form-field">任务标题<input name="title" maxlength="120" required></label><label class="form-field">补充说明<textarea name="note" maxlength="2000" rows="3"></textarea></label><div class="field-pair"><label class="form-field">负责人<select name="assignee_id">${state.users.map(item => `<option value="${item.id}" ${item.id === state.user.id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label><label class="form-field">截止日期<input name="due_date" type="date"></label></div></form>`, button("取消", "x", "close-dialog") + formButton("预览任务", "scan-eye", "task-form"));
  }
  function taskDialog(id) {
    const task = state.tasks.find(item => item.id === id);
    if (!task) throw new Error("待办已变化，请刷新工作台。");
    if (task.kind === "review") return openDocument(task.artifact_id);
    let footer = button("关闭", "x", "close-dialog");
    if (!["completed", "canceled"].includes(task.rawStatus)) {
      if (task.assignee_id === state.user.id) {
        if (task.rawStatus === "pending") footer += button("开始处理", "play", "task-status", true, `data-id="${id}" data-status="in_progress"`);
        footer += button("标记完成", "check", "task-status", false, `data-id="${id}" data-status="completed"`);
      }
      if (task.creator_id === state.user.id) footer += button("取消任务", "circle-x", "task-status", false, `data-id="${id}" data-status="canceled"`);
    }
    modal(task.title, `<dl class="task-details"><dt>状态</dt><dd>${status(task.status)}</dd><dt>负责人</dt><dd>${esc(task.assignee_name)}</dd><dt>截止日期</dt><dd>${esc(task.due)}</dd><dt>补充说明</dt><dd>${esc(task.note || "无")}</dd><dt>记录版本</dt><dd>${task.version}</dd></dl>`, footer);
  }
  async function admin() {
    await navigate(async () => {
      const [summary, publications, feedback, templates, users, categories] = await Promise.all([api("/admin"), api("/admin/publications"), api("/admin/feedback"), api("/admin/templates"), api("/admin/users"), api("/admin/categories")]);
      await records();
      state.adminData = summary;
      state.publications = publications;
      state.adminFeedback = feedback;
      state.adminTemplates = templates;
      state.adminUsers = users; state.adminCategories = categories;
      state.adminUsersError = ""; state.adminCategoriesError = "";
      state.page = "admin";
      state.activeModule = "admin";
      state.editor = null;
      state.dirty = false;
      render(true);
    });
  }
  async function refreshManagement(kind) {
    const field = kind === "users" ? "adminUsers" : "adminCategories";
    try {
      const rows = await api("/admin/" + kind);
      await records();
      state[field] = rows; state[field + "Error"] = "";
    } catch (error) { state[field + "Error"] = error.message; render(); throw error; }
    render();
  }
  async function openAccount(id, password = false) {
    await refreshManagement("users");
    const item = state.adminUsers.find(item => item.id === id);
    if (!item) throw new Error("账号已变化，请刷新后重试。");
    modal(password ? "重置密码" : "修改账号", password ? A.passwordForm(ui(), item) : A.accountForm(ui(), item), button("取消", "x", "close-dialog") + formButton(password ? "确认重置" : "保存账号", password ? "key-round" : "check", password ? "account-password-form" : "account-edit-form"));
  }
  function leaveManagedSession() {
    autosaver.reset(null);
    keys.clear();
    chatEpoch++; chatGeneration++; createChatKey = null;
    Object.assign(state, { conversations: [], legacyMessages: [], activeConversation: null, conversation: null, chatDrafts: {}, pendingChats: {}, chatSending: null, chatCreating: false, chatError: "", chatLoading: false, currentChatCursor: null, nextChatCursor: null });
    Object.assign(state, { user: null, sources: [], imports: [], importTargets: {}, importUsage: null, documents: [], reviews: [], tasks: [], messages: [], users: [], categories: [], templates: [], adminData: null, adminUsers: [], adminCategories: [], adminFeedback: [], adminTemplates: [], publications: [], editor: null, dirty: false, baseline: "", undo: [], recovery: null, newDocumentDraft: null, page: "home", activeModule: null });
    render(true);
  }
  async function managementSaved(result, message, kind, form) {
    form.querySelectorAll('[name="password"],[name="password_confirm"]').forEach(input => { input.value = ""; });
    closeDialog();
    if (result.reauthenticate) { leaveManagedSession(); return toast(message + "，当前会话已退出，请重新登录。"); }
    try { await refreshManagement(kind); toast(message); }
    catch { toast(message + "；列表暂未回读成功，请刷新后继续。"); }
  }
  async function refreshCategoryChoice(form) {
    state.categories = await api("/categories");
    const select = form.querySelector('[name="category_id"]');
    if (select) { select.innerHTML = A.categoryOptions(ui(), true); select.value = ""; }
  }
  function reauthenticate() {
    if (dialog.dataset.locked === "true" && dialog.open) return;
    modal("会话已失效", `<form id="reauth-form" data-form="reauth"><p class="notice warning">本页最新修改尚未确认同步，仍保留在内存中。请使用同一账号重新登录后手动重试；已停用账号不能登录。</p><label class="form-field">用户名<input name="username" value="${esc(state.user.username)}" autocomplete="username" readonly></label><label class="form-field">密码<input name="password" type="password" maxlength="200" autocomplete="current-password" required></label></form>`, button("放弃本页并返回登录", "log-out", "leave-managed-login") + formButton("重新登录", "log-in", "reauth-form"), { locked: true });
  }
  async function ask(question, scope = "all") {
    question = String(question || "").trim();
    if (!question) return;
    if (state.dirty) return navigate(async () => { await page("module", "assistant"); await ask(question, scope); });
    if (state.page !== "module" || state.activeModule !== "assistant") {
      await page("module", "assistant");
      if (state.activeModule !== "assistant") return;
    }
    if (state.chatSending || state.chatCreating) throw new Error("正在发送，请稍候。");
    if (state.chatLoading || state.chatError || state.currentChatCursor || state.conversation?.status === "archived") throw new Error("请先打开可用会话的最新消息。");
    if (state.pendingChats[state.activeConversation]) throw new Error("上次发送尚未确认，请先重试原消息。");
    const epoch = chatEpoch;
    let meta = state.conversation, draft = chatDraft();
    Object.assign(draft, { text: question, scope });
    if (!meta) {
      meta = await createChat();
      if (!meta || epoch !== chatEpoch) return;
      state.chatDrafts[meta.id] = { ...draft };
    }
    if (state.pendingChats[meta.id]) throw new Error("上次发送尚未确认，请先重试原消息。");
    const payload = { text: question, scope, base_version: meta.version };
    if (meta.state === "waiting_for_info" && !draft.newQuestion) payload.reply_to = meta.last_message_id;
    state.pendingChats[meta.id] = { key: crypto.randomUUID(), payload };
    return sendChat(meta.id);
  }
  function fieldsFromData(data, template) {
    return Object.fromEntries((template?.fields || []).map(field => [field.key, data.get("field_" + field.key) || ""]));
  }
  function showNewDocument() {
    modal("从模板起草", W.newDocument(ui()), button("取消", "x", "close-dialog") + formButton("创建草稿", "file-plus-2", "new-document-form", state.templates.length ? "" : "disabled"));
  }
  function readTemplateDraft(form) {
    const data = new FormData(form);
    const fields = templateDraft.fields.map((_, index) => ({ key: data.get("key_" + index) || "", label: data.get("label_" + index) || "", required: data.has("required_" + index), multiline: data.has("multiline_" + index) }));
    return { ...templateDraft, name: data.get("name") || "", active: data.has("active"), fields, sections: templateDraft.sections.map((_, index) => ({ key: data.get("section_key_" + index) || "", title: data.get("section_title_" + index) || "", field_keys: data.getAll("section_field_" + index).map(value => fields[Number(value)]?.key).filter(Boolean) })) };
  }
  function showTemplateDraft() {
    modal("编辑模板 / 新版本", W.templateForm(ui(), templateDraft), button("取消", "x", "close-dialog") + formButton("保存新版本", "save", "template-edit-form"));
  }
  async function action(name, element) {
    const id = element.dataset.id;
    if (state.saving) return;
    if (name === "home") return page("home");
    if (name === "open-library") return page("module", "library");
    if (name === "open-documents") return page("module", "document");
    if (name === "open-chat") return page("module", "assistant");
    if (name === "open-review") return page("reviews");
    if (name === "open-tasks") return page("tasks");
    if (name === "open-admin") return admin();
    if (name === "close-dialog") { autosaver.pause(false); pendingNavigation = null; return closeDialog(); }
    if (name === "confirm-navigation") { const next = pendingNavigation; pendingNavigation = null; await autosaver.settled(); autosaver.reset(null); closeDialog(); state.dirty = false; return next?.(); }
    if (name === "account") return modal("当前账号", `<dl class="task-details"><dt>姓名</dt><dd>${esc(state.user.name)}</dd><dt>账号</dt><dd>${esc(state.user.username)}</dd><dt>角色</dt><dd>${state.user.role === "admin" ? "管理员" : "员工"}</dd></dl>`, button("关闭", "x", "close-dialog") + (state.user.role === "admin" ? button("修改密码", "key-round", "reset-account-password", false, `data-id="${esc(state.user.id)}"`) : "") + button("退出登录", "log-out", "logout"));
    if (name === "logout") { closeDialog(); return navigate(async () => { await api("/session/logout", {}); location.reload(); }); }
    if (name === "leave-managed-login") { dialog.dataset.locked = "false"; closeDialog(); leaveManagedSession(); return toast("已返回登录，本页未保存修改已放弃。"); }
    if (name === "toggle-password") { const input = element.parentElement.querySelector("input"); input.type = input.type === "password" ? "text" : "password"; return; }
    if (name === "document") return openDocument(id);
    if (name === "source") return source(id);
    if (["source-sharing", "source-version-text", "source-version-file"].includes(name)) {
      if (!currentSource) return;
      const item = await api("/sources/" + currentSource.id);
      currentSource = item;
      if (name === "source-sharing") return modal("共享范围", V.sourceSharing(ui(), item), button("取消", "x", "close-dialog") + formButton("保存范围", "check", "source-sharing-form"));
      if (!item.can_add_version) return toast("请从本人最新版本新建；当前内容不会被覆盖。");
      return name === "source-version-text" ? modal("新建文本版本", V.newSource(ui(), item), button("取消", "x", "close-dialog") + formButton("另存私有草稿", "save", "new-source-form")) : modal("上传新版本", V.importForm(ui(), item), button("取消", "x", "close-dialog") + formButton("上传并解析", "upload", "import-upload-form"));
    }
    if (name === "evidence") return evidence(element.dataset.message, id);
    if (name === "answer-feedback") {
      const message = state.messages.find(item => item.id === id);
      if (!message || message.feedback) { await refreshConversation(); return toast("请查看最新回答反馈状态。"); }
      const rating = element.dataset.rating === "helpful" ? "helpful" : "unhelpful";
      return modal("回答反馈", V.feedbackForm(ui(), message, rating), button("取消", "x", "close-dialog") + formButton("提交反馈", "send", "answer-feedback-form"));
    }
    if (name === "refresh-feedback") return refreshConversation();
    if (name === "refresh-admin-feedback") { state.adminFeedback = await api("/admin/feedback"); return render(); }
    if (name === "refresh-admin-users") return refreshManagement("users");
    if (name === "refresh-admin-categories") return refreshManagement("categories");
    if (name === "new-account") return modal("新建账号", A.accountForm(ui()), button("取消", "x", "close-dialog") + formButton("创建账号", "user-plus", "account-edit-form"));
    if (name === "edit-account") return openAccount(id);
    if (name === "reset-account-password") return openAccount(id, true);
    if (name === "new-category") return modal("新增分类", A.categoryForm(ui()), button("取消", "x", "close-dialog") + formButton("创建分类", "folder-plus", "category-edit-form"));
    if (name === "edit-category") {
      await refreshManagement("categories");
      const item = state.adminCategories.find(item => item.id === id);
      if (!item || item.id === "cat_other") throw new Error("默认分类不可修改，或此分类已变化。");
      return modal("修改分类", A.categoryForm(ui(), item), button("取消", "x", "close-dialog") + formButton("保存分类", "check", "category-edit-form"));
    }
    if (name === "catalog-tab") { state.catalogTab = id; return render(); }
    if (name === "triage-feedback") {
      state.adminFeedback = await api("/admin/feedback");
      const item = state.adminFeedback.find(item => item.id === id);
      if (!item) throw new Error("反馈记录已变化，请刷新列表。");
      return modal("反馈处理", V.feedbackTriage(ui(), item), button("关闭", "x", "close-dialog") + (item.status === "closed" ? "" : formButton("保存处理结果", "check", "feedback-triage-form")));
    }
    if (name === "import-file") return modal("导入文件", V.importForm(ui()), button("取消", "x", "close-dialog") + formButton("上传并解析", "upload", "import-upload-form"));
    if (name === "import-job") return openImport(id);
    if (name === "cancel-import") return manageImport(id, "cancel");
    if (name === "delete-import") return manageImport(id, "delete");
    if (name === "retry-import") { await api(`/imports/${id}/retry`, {}); await records(); render(); return openImport(id); }
    if (name === "download-source") {
      const download = document.createElement("a");
      download.href = `/api/workbench/sources/${id}/file`;
      download.download = currentSource?.attachment?.filename || "source-file";
      root.append(download); download.click(); download.remove();
      return toast("浏览器将请求下载原件，服务端会再次检查资料权限。");
    }
    if (name === "task") return taskDialog(id);
    if (name === "document-tab") { state.documentTab = id; return render(); }
    if (name === "new-source") return modal("新增文本资料", V.newSource(ui()), button("取消", "x", "close-dialog") + formButton("保存私有草稿", "save", "new-source-form"));
    if (name === "new-document") { state.newTemplate = element.dataset.template || "solution"; state.newDocumentDraft = null; return showNewDocument(); }
    if (name === "document-fields") {
      if (!state.editor?.template_binding) return;
      if (!R.editable(state.editor, state.historyMode)) return modal("文档信息 / 只读", W.documentFields(ui(), true));
      return modal("文档信息", W.documentFields(ui()), button("取消", "x", "close-dialog") + formButton("应用到当前草稿", "check", "document-fields-form"));
    }
    if (name === "refresh-document") return openDocument(state.editor.id, state.historyMode ? state.editor.revision : undefined);
    if (name === "artifact-sharing") {
      if (!state.editor?.can_share || state.historyMode) return;
      if (state.dirty) return toast("请先保存本页正文，再确认共享范围。");
      const aid = state.editor.id;
      const [latest, users] = await Promise.all([api(`/artifacts/${aid}`), api("/users")]);
      if (state.editor?.id !== aid || state.dirty) return toast("本页稿件已变化，请保存后重新打开共享。");
      if (!latest.can_share) throw new Error("当前账号不能调整此稿件的共享。");
      state.editor = latest; state.users = users; setBaseline(); render();
      return modal("稿件只读共享", W.artifactSharing(ui(), latest), button("取消", "x", "close-dialog") + button("读取最新范围", "refresh-cw", "artifact-sharing") + formButton("保存共享范围", "check", "artifact-sharing-form"));
    }
    if (name === "writing-checks") {
      const checks = state.dirty || state.historyMode ? W.localChecks(state.editor) : await api(`/artifacts/${state.editor.id}/checks`);
      return modal("写作检查", W.checksDetail(ui(), checks));
    }
    if (name === "edit-template") {
      state.adminTemplates = await api("/admin/templates");
      const item = state.adminTemplates.find(item => item.template_id === id);
      if (!item) throw new Error("模板已变化，请刷新后台。");
      templateDraft = clone(item);
      return showTemplateDraft();
    }
    if (name === "template-history") { const item = await api("/templates/versions/" + id); return modal("模板历史版本", W.templateVersion(ui(), item)); }
    if (["add-template-field", "delete-template-field", "add-template-section", "delete-template-section", "move-template-section"].includes(name)) {
      const form = dialog.querySelector("#template-edit-form");
      if (!form || !templateDraft) return;
      templateDraft = readTemplateDraft(form);
      const index = Number(element.dataset.index);
      if (name === "add-template-field" && templateDraft.fields.length < 12) templateDraft.fields.push({ key: "", label: "", required: false, multiline: false });
      if (name === "delete-template-field" && templateDraft.fields.length > 1) { const removed = templateDraft.fields.splice(index, 1)[0]; templateDraft.sections.forEach(section => { section.field_keys = section.field_keys.filter(key => key !== removed.key); }); }
      if (name === "add-template-section" && templateDraft.sections.length < 8) templateDraft.sections.push({ key: "", title: "", field_keys: [] });
      if (name === "delete-template-section" && templateDraft.sections.length > 1) templateDraft.sections.splice(index, 1);
      if (name === "move-template-section") { const next = index + Number(element.dataset.delta); if (next >= 0 && next < templateDraft.sections.length) [templateDraft.sections[index], templateDraft.sections[next]] = [templateDraft.sections[next], templateDraft.sections[index]]; }
      return showTemplateDraft();
    }
    if (name === "save-document") return saveDocument();
    if (name === "retry-autosave") { await autosaver.flush({ retry: true }); return toast("工作草稿已同步，正式修订尚未生成。"); }
    if (name === "show-working-copy") {
      const copy = await api(`/artifacts/${state.editor.id}/draft`);
      return modal("服务端工作草稿", copy.has_draft ? `<h3>${esc(copy.draft.title)}</h3><p class="caption">草稿版本 ${copy.draft_version} / 基于 r${copy.base_revision}</p>${copy.draft.content_json.schema_version === 2 ? R.body(ui(), copy.draft.content_json) : copy.draft.content_json.blocks.map(block => `<p class="latest-block">${esc(block.text)}</p>`).join("")}` : "<p>目前没有未提交的工作草稿。</p>");
    }
    if (name === "submit-review") {
      if (!state.editor?.can_edit || state.historyMode) return;
      if (state.dirty) return toast("请先保存当前修改，再提交审核。");
      if (W.localChecks(state.editor).missing_fields.length) return toast("请先补齐并保存模板必填信息。");
      return modal("提交同事审核", `<form id="review-submit-form" data-form="review-submit"><p class="notice">${esc(state.editor.title)} / r${state.editor.revision}</p><label class="form-field">审核同事<select name="reviewer_id">${state.users.filter(item => item.id !== state.user.id).map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join("")}</select></label><p class="notice">审核人将获得本修订的阅读权限。引用资料也需对审核人可见。</p></form>`, button("取消", "x", "close-dialog") + formButton("提交审核", "send", "review-submit-form"));
    }
    if (name === "review-decision") return reviewForm(id);
    if (name === "export-document") {
      if (!state.editor?.can_export || state.dirty || state.historyMode) return toast("当前稿件未具备导出权限、有效审核或来源授权。");
      const output = await api(`/artifacts/${state.editor.id}/exports`, { revision: state.editor.revision });
      const download = document.createElement("a");
      download.href = `/api/workbench/exports/${output.id}/download`;
      download.download = state.editor.title + ".docx";
      root.append(download); download.click(); download.remove();
      return toast("导出记录已生成，浏览器将请求下载。下载前仍会检查权限与引用状态。");
    }
    if (name === "insert-excerpt") {
      if (!currentSource || !R.editable(state.editor, state.historyMode)) return;
      if (state.dirty) return toast("请先保存本页修改，再预览摘录。");
      const index = Number(element.dataset.section);
      if (!currentSource.sections[index]) return;
      return modal("选择采纳位置", W.suggestionTarget(ui(), currentSource, index), button("取消", "x", "close-dialog") + formButton("预览采纳结果", "scan-eye", "suggestion-target-form"));
    }
    if (name === "apply-suggestion") {
      const preview = currentSuggestion;
      if (!preview) throw new Error("建议已关闭，请重新预览。");
      if (state.dirty || state.editor.id !== preview.artifact_id || state.editor.revision !== preview.base_revision || snapshot() !== preview.localSnapshot) throw new Error("本页正文或文档信息已变化，请保存后重新预览。");
      await api(`/artifacts/${preview.artifact_id}/suggestions/${preview.id}/apply`, { base_revision: preview.base_revision });
      const [latest] = await Promise.all([api(`/artifacts/${preview.artifact_id}`), records()]);
      if (currentSuggestion !== preview || state.editor?.id !== preview.artifact_id || snapshot() !== preview.localSnapshot) return toast("采纳已保存，请重新打开文档读取新修订。");
      state.editor = latest; state.currentRevision = latest.revision; setBaseline(); closeDialog(); render();
      return toast("摘录已采纳并保存为新修订，需重新审核。");
    }
    if (name === "cancel-suggestion") {
      if (currentSuggestion) await api(`/artifacts/${currentSuggestion.artifact_id}/suggestions/${currentSuggestion.id}/cancel`, {});
      closeDialog(); return toast("建议已取消，正文未变更。");
    }
    if (name === "undo-suggestion") {
      if (state.dirty || !state.editor.undo_suggestion) return toast("请先处理未保存修改，或重新读取可撤销的采纳记录。");
      const aid = state.editor.id, before = snapshot();
      await api(`/artifacts/${aid}/suggestions/${id}/undo`, { base_revision: state.editor.revision });
      const [latest] = await Promise.all([api(`/artifacts/${aid}`), records()]);
      if (state.editor?.id !== aid || snapshot() !== before) return toast("撤销已保存，请重新打开文档读取新修订。");
      state.editor = latest; state.currentRevision = latest.revision; setBaseline(); render();
      return toast("已用新修订撤销采纳，历史记录保留。");
    }
    if (["add-block", "delete-block", "move-block", "remove-citation", "undo-edit"].includes(name)) {
      if (!R.editable(state.editor, state.historyMode)) return;
      if (name === "undo-edit") { const previous = state.undo.pop(); if (previous) Object.assign(state.editor, JSON.parse(previous)); }
      else {
        const blocks = state.editor.content_json.blocks;
        if (name === "add-block" && blocks.length >= 100) return toast("最多 100 个文本块。");
        pushUndo();
        if (name === "add-block") blocks.push({ id: "b_" + crypto.randomUUID(), type: "paragraph", text: "" });
        if (name === "delete-block" && blocks.length > 1) state.editor.content_json.blocks = blocks.filter(block => block.id !== id);
        if (name === "move-block") { const from = blocks.findIndex(block => block.id === id); const to = from + Number(element.dataset.delta); if (from >= 0 && to >= 0 && to < blocks.length) [blocks[from], blocks[to]] = [blocks[to], blocks[from]]; }
        if (name === "remove-citation") { state.editor.citations = state.editor.citations.filter(ref => ref.source_id !== id); state.editor.reference_details = state.editor.reference_details.filter(ref => ref.source_id !== id); toast("引用已移除；正文中的摘录标号需人工核对。"); }
      }
      markDirty(); return render();
    }
    if (name === "focus-block") { const block = [...root.querySelectorAll("[data-block]")].find(item => item.dataset.block === id); block?.scrollIntoView({ behavior: "smooth", block: "center" }); block?.querySelector("textarea")?.focus({ preventScroll: true }); return; }
    if (name === "editor-panel") { state.editorPanel = id; return render(); }
    if (name === "revision") return openDocument(state.editor.id, Number(element.dataset.revision));
    if (name === "show-latest") {
      const latest = await api("/artifacts/" + state.editor.id);
      return modal("最新修订 r" + latest.revision, `<h3>${esc(latest.title)}</h3>${latest.content_json.blocks.map(block => `<p class="latest-block">${esc(block.text)}</p>`).join("")}`, button("返回本页继续整理", "arrow-left", "close-dialog"));
    }
    if (name === "discard-reload") {
      autosaver.pause(true);
      return modal("放弃工作草稿", "<p>确认放弃本页修改和已知版本的工作草稿？正式修订和历史记录不变。若其他页面已更新，操作将被拒绝。</p>", button("继续编辑", "arrow-left", "close-dialog") + button("确认放弃并重新读取", "trash-2", "confirm-discard-draft", true));
    }
    if (name === "confirm-discard-draft") {
      await autosaver.settled();
      const aid = state.editor.id;
      const result = await api(`/artifacts/${aid}/draft/discard`, { base_draft_version: autosaver.state().version, confirm: true });
      if (!result.is_current) throw new Error("此前已放弃，但工作草稿随后变化；请重新读取，不会再次删除。");
      autosaver.reset(null); state.dirty = false; closeDialog(); return openDocument(aid);
    }
    if (name === "ask") return ask(element.dataset.question);
    if (name === "new-chat") return createChat();
    if (name === "select-chat") return loadConversation(id || null);
    if (name === "refresh-chat") return loadConversation(state.activeConversation, state.currentChatCursor);
    if (name === "chat-earlier") return loadConversation(state.activeConversation, state.nextChatCursor);
    if (name === "chat-latest") return loadConversation(state.activeConversation);
    if (name === "retry-chat") return sendChat(state.activeConversation);
    if (name === "chat-mode") { chatDraft().newQuestion = element.dataset.mode === "new"; return render(); }
    if (name === "rename-chat" || name === "archive-chat") {
      const item = state.conversation;
      if (!item || state.pendingChats[item.id] || state.chatSending) throw new Error("请先确认当前消息的发送结果。");
      const rename = name === "rename-chat", next = item.status === "archived" ? "active" : "archived";
      return modal(rename ? "重命名会话" : next === "active" ? "恢复会话" : "归档会话", `<form id="conversation-edit-form" data-form="conversation-edit" data-id="${item.id}" data-version="${item.version}" ${rename ? "" : `data-status="${next}"`}>${rename ? `<label class="form-field">会话名称<input name="title" value="${esc(item.title)}" maxlength="80" required></label>` : `<p>${next === "active" ? "恢复后可继续提问，原消息保留。" : "归档后暂停提问，消息保留且可恢复。"}</p>`}</form>`, button("取消", "x", "close-dialog") + formButton("确认", "check", "conversation-edit-form"));
    }
    if (name === "new-task") return newTask();
    if (name === "confirm-task") {
      if (!pendingPreview) return;
      const result = await api(`/task-previews/${pendingPreview.id}/confirm`, {});
      await records(); closeDialog(); render(); taskDialog(result.id); return toast("任务已确认并写入演示库");
    }
    if (name === "task-status") {
      const task = state.tasks.find(item => item.id === id);
      await api(`/tasks/${id}/status`, { status: element.dataset.status, base_version: task.version });
      await records(); closeDialog(); render(); return toast("任务状态已更新");
    }
    if (name === "publication-request") return publicationForm();
    if (name === "review-publication") {
      const request = state.publications.find(item => item.id === id);
      const item = await api("/sources/" + element.dataset.source);
      currentPublication = request;
      currentSource = item;
      return modal("发布审阅 / " + item.title, V.sourceDetail(ui(), item) + `<p class="notice">目标范围：${V.scopeNames[request.scope]}</p>`, button("退回", "corner-down-left", "publication-decision", false, 'data-status="rejected"') + button("通过并发布", "check", "publication-decision", true, 'data-status="approved"'));
    }
    if (name === "publication-decision") { await api(`/admin/publications/${currentPublication.id}/decision`, { decision: element.dataset.status }); closeDialog(); await admin(); return toast("发布申请已处理"); }
    if (name === "withdraw-source") {
      const item = await api("/sources/" + id);
      currentSource = item;
      return modal("撤回发布", `<p>确认撤回“${esc(item.title)}”？撤回后不再参与问答、新引用或已引用文档的导出。</p><p class="notice">已下载到其他设备的文件不能由本系统撤回。</p>`, button("取消", "x", "close-dialog") + button("确认撤回", "archive-x", "confirm-withdraw", true));
    }
    if (name === "confirm-withdraw") { await api(`/sources/${currentSource.id}/withdraw`, { base_version: currentSource.version }); closeDialog(); await records(); if (state.page === "admin") await admin(); else render(); return toast("资料已撤回"); }
    if (name === "admin-tab") { state.adminTab = id; return render(); }
    if (name === "retry-boot") return boot();
  }
  async function submit(form) {
    const data = new FormData(form);
    const type = form.dataset.form;
    const intent = () => ({ key: form.dataset.intentKey || (form.dataset.intentKey = crypto.randomUUID()) });
    if (type === "login" || type === "reauth") {
      const user = await api("/session", { username: data.get("username"), password: data.get("password"), remember: type === "login" && data.get("remember") === "on" }, { login: true });
      if (type === "reauth") {
        if (user.id !== state.user.id) throw new Error("请使用原账号登录。");
        state.user = user; dialog.dataset.locked = "false"; closeDialog();
        if (user.role !== "admin") {
          Object.assign(state, { adminData: null, adminUsers: [], adminCategories: [], adminFeedback: [], adminTemplates: [], publications: [] });
          if (state.page === "admin") { state.page = "home"; state.activeModule = null; }
        }
        autosaver.reauthenticated(); render(); toast("已重新登录，请手动重试刚才的同步或保存操作。"); return;
      }
      state.user = user; await records(); render(true); return;
    }
    if (type === "account-edit" || type === "account-password") {
      const id = form.dataset.id;
      const secret = type === "account-password" || !id;
      if ((id || type === "account-password") && data.get("confirm") !== "yes") throw new Error("请先勾选确认此账号操作。");
      const payload = type === "account-password" ? { base_version: Number(form.dataset.version) } : { name: data.get("name"), role: data.get("role") };
      if (secret) {
        if (data.get("password") !== data.get("password_confirm")) throw new Error("两次输入的新密码不一致。");
        const minimum = state.loginOptions.demo === true ? 6 : 20;
        if (typeof data.get("password") !== "string" || data.get("password").length < minimum || data.get("password").length > 200) throw new Error(`新密码需为 ${minimum} 至 200 字符。`);
        payload.password = data.get("password");
      }
      if (type === "account-edit") {
        if (id) Object.assign(payload, { base_version: Number(form.dataset.version), active: data.get("active") === "on" });
        else payload.username = data.get("username");
      }
      const path = type === "account-password" ? `/admin/users/${id}/password` : id ? `/admin/users/${id}` : "/admin/users";
      const result = await api(path, payload, { ...intent(), method: id && type === "account-edit" ? "PATCH" : "POST", sensitive: secret });
      return managementSaved(result, type === "account-password" ? "密码已重置" : id ? "账号已更新" : "账号已创建，新密码不回显", "users", form);
    }
    if (type === "category-edit") {
      const id = form.dataset.id;
      const payload = { name: data.get("name"), ...(id ? { base_version: Number(form.dataset.version), active: data.get("active") === "on" } : {}) };
      const result = await api(id ? `/admin/categories/${id}` : "/admin/categories", payload, { ...intent(), method: id ? "PATCH" : "POST" });
      return managementSaved(result, id ? "分类已更新，现有正文与引用保持" : "分类已创建", "categories", form);
    }
    if (type === "source" || type === "source-version") {
      let item;
      const payload = { ...Object.fromEntries(data), ...(type === "source-version" ? { base_version: Number(form.dataset.version) } : {}) };
      try { item = await api(type === "source-version" ? `/sources/${form.dataset.id}/versions` : "/sources", payload, intent()); }
      catch (error) { if (error.code === "CATEGORY_UNAVAILABLE") await refreshCategoryChoice(form); throw error; }
      await records(); closeDialog(); render(); return source(item.id);
    }
    if (type === "source-sharing") {
      await api(`/sources/${form.dataset.id}/sharing`, { base_version: Number(form.dataset.version), scope: data.get("scope"), shared_user_ids: data.getAll("shared_user_ids") }, { ...intent(), method: "PATCH" });
      await records(); closeDialog(); render(); await source(form.dataset.id);
      return toast("共享范围已更新，后续原文读取和下载会重新鉴权。");
    }
    if (type === "artifact-sharing") {
      if (!state.editor?.can_share || state.historyMode || state.dirty || state.editor.id !== form.dataset.id) throw new Error("请先读取并保存本人当前稿件，再调整共享。");
      const scope = data.get("scope");
      if (scope !== "private" && data.get("confirm_body") !== "yes") throw new Error("请先确认正文、文档信息及后续修订的共享范围。");
      const before = snapshot(), aid = form.dataset.id;
      const result = await api(`/artifacts/${aid}/sharing`, { scope, shared_user_ids: scope === "selected" ? data.getAll("shared_user_ids") : [], base_version: Number(form.dataset.version), base_revision: Number(form.dataset.revision), confirm_body: data.get("confirm_body") === "yes" }, { ...intent(), method: "PATCH" });
      await records();
      if (state.editor?.id === aid && snapshot() === before) { state.editor = result; state.currentRevision = result.revision; setBaseline(); }
      closeDialog(); render();
      return toast(result.sharing_result.is_current ? "共享范围已保存并回读；资料原文权限保持独立。" : "此次操作此前已完成，范围随后已变化；现已读取最新范围，未重新授权。");
    }
    if (type === "import-upload") {
      const file = data.get("file");
      if (!file?.size || !/\.(docx|txt|md|pdf)$/i.test(file.name)) throw new Error("请选择非空的 DOCX、TXT、MD 或文本 PDF 文件。");
      if (file.size > 2 * 1024 * 1024) throw new Error("单个文件不能超过 2 MiB。");
      const upload = new FormData();
      upload.append("file", file, file.name);
      let result;
      const parent = form.dataset.parent ? { id: form.dataset.parent, version: Number(form.dataset.parentVersion) } : null;
      try { result = await api("/imports", upload, { ...intent(), multipart: true, parent }); }
      catch (error) { if (error.code === "IMPORT_REMOVED") delete form.dataset.intentKey; throw error; }
      await records(); render();
      await openImport(result.id);
      if (result.duplicate) toast("已打开此前导入的相同文件，未重复保存原件。");
      return;
    }
    if (type === "import-confirm") {
      let result;
      const parent = data.get("parent_source_id");
      const payload = Object.fromEntries(data);
      delete payload.parent_source_id;
      if (parent === "__unavailable__") throw new Error("原目标已变化，请重新选择入库位置；解析内容仍保留。");
      if (parent) Object.assign(payload, { import_id: form.dataset.id, base_version: Number(form.querySelector("[data-import-target]").selectedOptions[0].dataset.version) });
      try { result = await api(parent ? `/sources/${parent}/versions` : `/imports/${form.dataset.id}/confirm`, payload, intent()); }
      catch (error) { if (error.code === "CATEGORY_UNAVAILABLE") await refreshCategoryChoice(form); throw error; }
      delete state.importTargets[form.dataset.id];
      await records(); closeDialog(); render(); await source(result.source_id || result.id);
      return toast("已建立私有资料草稿，尚未发布，也不参与问答。");
    }
    if (type === "import-cancel") {
      const job = await api(`/imports/${form.dataset.id}/cancel`, {}, intent());
      await records(); render(); showImport(job);
      return toast(job.status === "canceling" ? "取消申请已保存，等待解析退出；原件仍保留。" : job.status === "canceled" ? "导入已取消，原件仍保留并占用额度。" : "取消操作已回读，当前状态为“" + V.importNames[job.status] + "”。");
    }
    if (type === "import-delete") {
      if (data.get("confirm") !== "yes") throw new Error("请先勾选确认删除这份未入库文件。");
      await api(`/imports/${form.dataset.id}/delete`, { confirm: true, file_sha256: data.get("file_sha256") }, intent());
      await records(); closeDialog(); render();
      return toast("已删除未入库文件，应用额度已回收；备份和下载副本未删除。");
    }
    if (type === "document") {
      const selected = state.templates.find(item => item.template_id === data.get("template"));
      if (!selected) throw new Error("模板已不可用，请重新选择。");
      const payload = { title: data.get("title"), template_version_id: form.dataset.templateVersion || selected.id, notes: data.get("notes") || "", source_ids: data.getAll("source_ids"), fields: fieldsFromData(data, selected) };
      const create = async () => {
        let item;
        try { item = await api("/artifacts", payload, intent()); }
        catch (error) {
          if (error.code === "TEMPLATE_UNAVAILABLE") {
            state.newDocumentDraft = { title: payload.title, notes: payload.notes, source_ids: payload.source_ids, fieldDrafts: { ...state.newDocumentDraft?.fieldDrafts, [selected.template_id]: payload.fields } };
            state.templates = await api("/templates"); showNewDocument();
            throw new Error("模板已更新或停用，已重新加载；请核对字段与版本后再次创建。");
          }
          throw error;
        }
        await records(); closeDialog(); await openDocument(item.id); toast("草稿已创建，待人工补充和核对。");
      };
      if (state.dirty) return navigate(create);
      return create();
    }
    if (type === "document-fields") {
      if (!R.editable(state.editor, state.historyMode) || !state.editor.template_binding) throw new Error("当前修订的文档信息只读。");
      pushUndo(); state.editor.fields = fieldsFromData(data, state.editor.template_binding); closeDialog(); markDirty(); render();
      return toast("文档信息已更新，正文未覆盖；当前修改尚未保存。");
    }
    if (type === "template-edit") {
      templateDraft = readTemplateDraft(form);
      await api(`/admin/templates/${templateDraft.template_id}/versions`, { base_version: Number(form.dataset.version), name: templateDraft.name, active: templateDraft.active, fields: templateDraft.fields, sections: templateDraft.sections }, intent());
      const [templates, publicTemplates] = await Promise.all([api("/admin/templates"), api("/templates")]);
      state.adminTemplates = templates; state.templates = publicTemplates; closeDialog(); render();
      return toast("模板新版本已保存，原有稿件未改写。");
    }
    if (type === "suggestion-target") {
      if (state.dirty || !R.editable(state.editor, state.historyMode)) throw new Error("请先保存可编辑的当前文档。");
      const before = snapshot(), aid = state.editor.id;
      const item = await api(`/artifacts/${aid}/suggestions`, { base_revision: state.editor.revision, target_block_id: data.get("target_block_id"), source_id: form.dataset.source, section_index: Number(form.dataset.section), operation: data.get("operation") }, intent());
      if (state.editor?.id !== aid || snapshot() !== before) throw new Error("本页正文已变化，请保存后重新预览。");
      currentSuggestion = { ...item, localSnapshot: before };
      return modal("采纳预览", W.suggestionPreview(ui(), item), button("取消建议", "x", "cancel-suggestion") + button("确认采纳并保存", "check", "apply-suggestion", true));
    }
    if (type === "ask") return ask(data.get("question"), data.get("scope"));
    if (type === "conversation-edit") {
      const id = form.dataset.id;
      const payload = { base_version: Number(form.dataset.version), ...(form.dataset.status ? { status: form.dataset.status } : { title: data.get("title") }) };
      let result;
      try { result = await api(`/conversations/${id}`, payload, { ...intent(), method: "PATCH" }); }
      catch (error) { if (error.status === 409 && state.activeConversation === id) await loadConversation(id); throw error; }
      state.conversations = state.conversations.map(item => item.id === id ? result : item);
      closeDialog();
      if (state.activeConversation === id) await loadConversation(id);
      return toast(result.change_result.is_current ? "会话已更新。" : "此操作此前已完成，现已读取最新状态，未重复修改。");
    }
    if (type === "answer-feedback") {
      try { await api(`/messages/${form.dataset.id}/feedback`, { rating: form.dataset.rating, reason: data.get("reason"), note: data.get("note") || "" }, intent()); }
      catch (error) { if (error.code === "FEEDBACK_EXISTS") await refreshConversation(); throw error; }
      await refreshConversation(); closeDialog();
      return toast("反馈已保存，可在回答下方查看处理状态。未触发模型训练。");
    }
    if (type === "feedback-triage") {
      const resolution = (data.get("resolution") || "").trim();
      if (data.get("status") === "closed" && !resolution) throw new Error("关闭反馈时请填写处理备注。");
      try { await api(`/admin/feedback/${form.dataset.id}/status`, { base_version: Number(form.dataset.version), status: data.get("status"), resolution }, intent()); }
      catch (error) { if (error.code === "VERSION_CONFLICT") { state.adminFeedback = await api("/admin/feedback"); render(); } throw error; }
      state.adminFeedback = await api("/admin/feedback"); closeDialog(); render();
      return toast("反馈处理状态已保存，处理备注可由反馈人回读。");
    }
    if (type === "review-submit" || type === "review-decision") {
      if (type === "review-submit") await api(`/artifacts/${state.editor.id}/reviews`, { reviewer_id: data.get("reviewer_id"), base_revision: state.editor.revision }, intent());
      else await api(`/reviews/${form.dataset.id}/decision`, { decision: data.get("decision"), note: data.get("note"), revision: state.editor.revision }, intent());
      state.editor = await api("/artifacts/" + state.editor.id); setBaseline(); await records(); closeDialog(); render(); return toast("审核记录已保存");
    }
    if (type === "comment") {
      if (!state.editor?.can_comment || state.historyMode) throw new Error("共享阅读不授予留言权限。");
      await api(`/artifacts/${state.editor.id}/comments`, { text: data.get("text"), base_revision: state.editor.revision }, intent());
      const latest = await api(`/artifacts/${state.editor.id}`);
      state.editor.comments = latest.comments;
      if (latest.revision !== state.editor.revision) state.conflict = true;
      render(); return toast("协作留言已保存");
    }
    if (type === "task") {
      pendingPreview = await api("/tasks/preview", Object.fromEntries(data), intent());
      return modal("确认任务", `<dl class="task-details"><dt>任务</dt><dd>${esc(pendingPreview.title)}</dd><dt>负责人</dt><dd>${esc(pendingPreview.assignee_name)}</dd><dt>截止日期</dt><dd>${esc(pendingPreview.due_date || "未设期限")}</dd><dt>说明</dt><dd>${esc(pendingPreview.note || "无")}</dd></dl><p class="notice warning">当前仅为预览。确认后才会写入待办，不发送外部消息。</p>`, button("取消", "x", "close-dialog") + button("确认创建", "check", "confirm-task", true));
    }
    if (type === "publication") { await api(`/sources/${currentSource.id}/publication-requests`, { base_version: currentSource.version, admin_id: data.get("admin_id"), scope: data.get("scope"), shared_user_ids: data.getAll("shared_user_ids") }, intent()); closeDialog(); await records(); render(); return toast("发布申请已提交给管理员"); }
  }
  document.addEventListener("click", event => {
    const element = event.target.closest("[data-action]");
    if (!element || element.disabled || element.dataset.busy) return;
    event.preventDefault();
    element.dataset.busy = "true";
    Promise.resolve(action(element.dataset.action, element)).catch(error => { if (dialog.open) dialogError(error); toast(error.message); }).finally(() => { delete element.dataset.busy; });
  });
  document.addEventListener("submit", event => {
    const form = event.target;
    if (!form.matches("[data-form],[data-home-search]")) return;
    event.preventDefault();
    if (form.dataset.busy) return;
    form.dataset.busy = "true";
    const buttons = [...document.querySelectorAll("button")].filter(button => button.form === form);
    buttons.forEach(button => { button.disabled = true; });
    const run = form.matches("[data-home-search]") ? (async () => { state.sourceQuery = new FormData(form).get("query").trim(); await page("module", "library"); })() : submit(form);
    const loginInputs = form.dataset.form === "login" ? [...form.querySelectorAll("input")] : [];
    loginInputs.forEach(input => { input.disabled = true; });
    Promise.resolve(run).catch(error => {
      const field = form.querySelector("[data-login-error]");
      if (field) field.textContent = error.message;
      if (dialog.open) dialogError(error);
      toast(error.message);
    }).finally(() => { delete form.dataset.busy; buttons.forEach(button => { button.disabled = false; }); loginInputs.forEach(input => { input.disabled = false; }); });
  });
  document.addEventListener("input", event => {
    const target = event.target;
    if (target.name === "username" && target.form?.dataset.form === "login") {
      state.loginUsername = target.value;
      target.form.querySelectorAll("[data-login-account]").forEach(input => { input.checked = input.value === target.value; });
      return;
    }
    if (target.matches("[data-chat-input]")) { chatDraft().text = target.value; return; }
    if (target.form?.matches?.('[data-form="source-version"],[data-form="source-sharing"],[data-form="artifact-sharing"],[data-form="import-confirm"]')) delete target.form.dataset.intentKey;
    if (target.form?.matches('[data-form="account-edit"],[data-form="account-password"],[data-form="category-edit"]')) delete target.form.dataset.intentKey;
    if (target.matches("[data-account-query]")) { state.accountQuery = target.value; const list = root.querySelector("[data-account-table]"); if (list) { list.innerHTML = A.accountRows(ui()); icons(); } return; }
    if (target.matches("[data-source-query]")) { state.sourceQuery = target.value; const list = root.querySelector("[data-source-list]"); if (list) { list.innerHTML = V.sourceList(ui()); icons(); } return; }
    if (!R.editable(state.editor, state.historyMode) || state.saving) return;
    if (target.matches("[data-editor-title],[data-block-text]")) {
      if (lastInput !== target) { pushUndo(); lastInput = target; }
      if (target.matches("[data-editor-title]")) state.editor.title = target.value;
      else { const block = state.editor.content_json.blocks.find(item => item.id === target.dataset.blockText); if (block) block.text = target.value; }
      markDirty();
    }
  });
  document.addEventListener("change", event => {
    const target = event.target;
    if (target.matches("[data-login-account]")) {
      const form = target.form;
      if (!state.loginOptions.accounts.some(item => item.username === target.value) || !form || form.dataset.busy) return;
      state.loginUsername = target.value;
      form.elements.namedItem("username").value = target.value;
      form.elements.namedItem("password").value = "";
      const error = form.querySelector("[data-login-error]");
      if (error) error.textContent = "";
      form.elements.namedItem("password").focus();
      return;
    }
    if (target.matches("[data-chat-scope]")) { chatDraft().scope = target.value; return; }
    if (target.form?.matches?.('[data-form="source-version"],[data-form="source-sharing"],[data-form="artifact-sharing"],[data-form="import-confirm"]')) delete target.form.dataset.intentKey;
    if (target.matches("[data-artifact-scope]")) {
      const form = target.form, open = target.value !== "private";
      form.querySelector("[data-artifact-recipients]").hidden = target.value !== "selected";
      form.querySelector("[data-artifact-disclosure]").hidden = !open;
      const confirmation = form.elements.namedItem("confirm_body");
      confirmation.required = open; confirmation.checked = false;
      return;
    }
    if (target.matches("[data-artifact-recipient]")) { target.form.elements.namedItem("confirm_body").checked = false; return; }
    if (target.matches("[data-import-target]")) {
      const form = target.form, item = state.sources.find(source => source.id === target.value);
      if (item) {
        form.elements.namedItem("title").value = item.title;
        form.elements.namedItem("label").value = `v${(item.version_number || 1) + 1}.0`;
        form.elements.namedItem("category_id").value = state.categories.some(category => category.id === item.category_id && category.active) ? item.category_id : "";
      }
      state.importTargets[form.dataset.id] = { id: target.value, version: item?.family_version || null };
      return;
    }
    if (target.form?.matches?.('[data-form="account-edit"],[data-form="account-password"],[data-form="category-edit"]')) delete target.form.dataset.intentKey;
    if (target.matches("[data-import-file]")) { if (target.form) delete target.form.dataset.intentKey; return; }
    if (target.matches("[data-new-template]")) {
      const data = new FormData(target.form), previous = state.templates.find(item => item.template_id === state.newTemplate) || state.templates[0];
      const draft = state.newDocumentDraft || { fieldDrafts: {} };
      if (previous) draft.fieldDrafts[previous.template_id] = fieldsFromData(data, previous);
      Object.assign(draft, { title: data.get("title") || "", notes: data.get("notes") || "", source_ids: data.getAll("source_ids") });
      state.newDocumentDraft = draft; state.newTemplate = target.value; return showNewDocument();
    }
    if (target.matches("[data-source-filter]")) { state.sourceFilter = target.value; return render(); }
    if (target.matches("[data-source-category]")) { state.sourceCategory = target.value; return render(); }
    if (target.matches("[data-account-filter]")) { state.accountFilter = target.value; return render(); }
    if (target.matches("[data-feedback-filter]")) { state.feedbackFilter = target.value; return render(); }
    if (target.matches("[data-block-type]") && R.editable(state.editor, state.historyMode)) { pushUndo(); state.editor.content_json.blocks.find(item => item.id === target.dataset.blockType).type = target.value; markDirty(); render(); }
  });
  document.addEventListener("keydown", event => {
    const current = event.target;
    if (current.matches('[role="tab"]') && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      const tabs = [...current.parentElement.querySelectorAll('[role="tab"]')];
      const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (tabs.indexOf(current) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); tabs[index].click();
      const newTabs = [...root.querySelectorAll('[role="tab"]')];
      newTabs.find(item => item.dataset.id === tabs[index].dataset.id)?.focus();
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s" && state.page === "editor") { event.preventDefault(); if (!state.saving) saveDocument(); }
  });
  document.addEventListener("focusin", event => { if (event.target.matches("[data-block-text]")) state.selectedBlock = event.target.dataset.blockText; });
  document.addEventListener("compositionstart", event => { if (event.target.matches("[data-editor-title],[data-block-text]")) autosaver.composing(true); });
  document.addEventListener("compositionend", event => { if (event.target.matches("[data-editor-title],[data-block-text]")) { markDirty(); autosaver.composing(false); } });
  dialog.addEventListener("cancel", event => { event.preventDefault(); autosaver.pause(false); pendingNavigation = null; closeDialog(); });
  window.addEventListener("beforeunload", event => { if (state.saving || state.dirty && !autosaver.safe()) { event.preventDefault(); event.returnValue = ""; } });
  async function boot() {
    try { state.loginOptions = await api("/login-options"); }
    catch { state.loginOptions = { demo: false, accounts: [] }; }
    try { state.user = await api("/session"); await records(); render(); }
    catch (error) {
      if (error.status === 401) { state.user = null; render(); }
      else { root.innerHTML = `<main class="initial-state" id="main"><h1>暂时无法读取工作台</h1><p>${esc(error.message)}</p>${button("重新连接", "refresh-cw", "retry-boot", true)}</main>`; icons(); }
    }
  }
  boot();
})();
