"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const ROOT = path.resolve(__dirname, "..");
const fixtures = JSON.parse(fs.readFileSync(path.join(ROOT, "fixtures.json"), "utf8"));
const templates = JSON.parse(fs.readFileSync(path.join(ROOT, "templates.json"), "utf8")).templates.map(item => ({ ...item, id: "tv_" + item.template_id + "_1", number: 1, active: true, history: [{ id: "tv_" + item.template_id + "_1", number: 1, name: item.name }] }));
const richFixture = JSON.parse(fs.readFileSync(path.join(ROOT, "tests/rich_fixture.json"), "utf8"));
const lucide = require(path.join(ROOT, "static/assets/lucide.min.js"));
const usedIcons = new Set();
let checks = 0;
function check(description, fn) { fn(); checks++; process.stdout.write(`ok ${checks} - ${description}\n`); }
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = name => { usedIcons.add(name); return `<i data-lucide="${name}"></i>`; };
const button = (text, symbol, action, primary, attrs = "") => `<button data-action="${action}" ${attrs}>${icon(symbol)}${esc(text)}</button>`;
const iconButton = (symbol, title, action, attrs = "") => `<button aria-label="${title}" data-action="${action}" ${attrs}>${icon(symbol)}</button>`;
const users = [{ id: "u_writer", username: "writer", name: "演示员工", role: "employee" }, { id: "u_reviewer", username: "reviewer", name: "审核同事", role: "employee" }, { id: "u_admin", username: "admin", name: "后台管理员", role: "admin" }];
const categories = [["other", "其他"], ["general", "通用资料"], ["product", "产品资料"], ["implementation", "实施指南"], ["service", "服务知识"], ["policy", "内部制度"], ["internal", "内部文档"]].map(([key, name]) => ({ id: "cat_" + key, name, active: true, version: 1, updated_at: "2026-11-04" }));
const sources = fixtures.sources.map(item => ({ ...item, label: item.version, version_id: "ver_" + item.id, owner_id: item.id === "weekly" ? "u_writer" : "u_admin", publication: item.id === "weekly" ? "draft" : "published", scope: item.id === "weekly" ? "private" : "internal", version: 1, status: item.id === "weekly" ? "未发布" : "已发布" }));
for (const item of sources) Object.assign(item, { category_id: categories.find(category => category.name === item.category)?.id || "cat_other", family_id: "family_" + item.id, family_version: 1, version_number: 1, is_current_published: item.publication === "published", shared_user_ids: [] });
const doc = {
  id: "proposal", owner_id: "u_writer", title: "企业知识库实施方案", revision: 1, status: "draft", can_edit: true,
  access: "owner", can_share: true, can_comment: true, can_export: false, sharing: { scope: "private", shared_user_ids: [], version: 1 },
  content_json: { schema_version: 1, blocks: [{ id: "b_heading", type: "heading", text: "项目目标" }, { id: "b_body", type: "paragraph", text: "建立统一资料目录，提供可追溯的知识查询与文档协作。" }] },
  citations: [{ source_id: "product", version_id: "ver_product" }], reference_details: [{ source_id: "product", version_id: "ver_product", title: "企业 AI 工作台产品说明", label: "v1.2", available: true }],
  reviews: [], comments: [], history: [{ revision: 1, title: "企业知识库实施方案", created_at: "2026-11-04T00:00:00Z" }], created_at: "2026-11-04T00:00:00Z", updated_at: "2026-11-04T00:00:00Z"
};
function templateContext() {
  const context = vm.createContext({ console, URL });
  for (const filename of ["module-views.js", "rich-views.js", "writing-views.js", "management-views.js", "views.js"]) vm.runInContext(fs.readFileSync(path.join(ROOT, "static", filename), "utf8"), context, { filename });
  return context;
}
const context = templateContext();
const state = { user: users[0], sources, imports: [], documents: [{ ...doc, state: "草稿", version: "r1", updated: "11/04 08:00" }], reviews: [], tasks: [], messages: [], documentTab: "all", sourceQuery: "", sourceFilter: "all", editor: doc, dirty: false, saving: false, historyMode: false, editorPanel: "body", undo: [], adminTab: "sources", publications: [], adminData: { users, audit: [] } };
state.importUsage = { used_bytes: 0, limit_bytes: 20 * 1024 * 1024, remaining_bytes: 20 * 1024 * 1024, pending_count: 0, pending_limit: 5 };
state.templates = templates; state.adminTemplates = templates;
state.categories = categories; state.adminCategories = categories;
state.adminUsers = users.map(item => ({ ...item, active: true, version: 1 }));
state.users = users;
function ui() {
  const out = { direction: "modules", state, sources, esc, icon, button, iconButton, status: text => `<span>${esc(text)}</span>`, label: text => text, formatDate: text => text };
  out.libraryBody = () => context.WORKBENCH_VIEWS.library(out);
  out.conversationHistory = () => context.WORKBENCH_VIEWS.conversation(out);
  out.composer = () => context.WORKBENCH_VIEWS.composer(out);
  out.referenceList = () => context.WORKBENCH_VIEWS.references(out);
  out.conversationList = () => context.WORKBENCH_VIEWS.conversationList(out);
  return out;
}
check("homepage renders three modules and actual data counts", () => {
  const html = context.CARD_HOME.render(ui());
  assert.equal((html.match(/class="application-card/g) || []).length, 3);
  assert.match(html, /5 份资料 \/ 4 已发布/);
  assert.match(html, /data-action="document" data-id="proposal"/);
});
check("knowledge editor assistant reviewer and admin templates render", () => {
  for (const view of ["library", "editor", "queue", "conversation", "composer", "references", "login", "newSource", "newDocument", "importList", "importForm"]) assert.equal(typeof context.WORKBENCH_VIEWS[view](ui()), "string");
  for (const tab of ["sources", "templates", "accounts", "ai"]) { state.adminTab = tab; assert.match(context.WORKBENCH_VIEWS.admin(ui()), /管理后台/); }
});
check("demo login offers fixed role choices and an optional remembered session without embedding passwords", () => {
  const demo = { ...ui(), state: { ...state, loginOptions: { demo: true, accounts: [{ username: "writer", label: "写作员工" }, { username: "reviewer", label: "审核同事" }, { username: "admin", label: "管理员" }] } } };
  const html = context.WORKBENCH_VIEWS.login(demo);
  assert.equal((html.match(/data-login-account/g) || []).length, 3);
  assert.match(html, /value="writer" checked/);
  assert.match(html, /name="remember"/);
  assert.match(html, /autocomplete="current-password"/);
  assert.doesNotMatch(html, /name="password"[^>]*value=/);
  assert.doesNotMatch(context.WORKBENCH_VIEWS.login(ui()), /data-login-account|name="remember"/);
});
check("demo account password controls use eight characters while normal controls remain strict", () => {
  const demo = { ...ui(), state: { ...state, loginOptions: { demo: true } } };
  assert.match(context.WORKBENCH_MANAGEMENT_VIEWS.accountForm(demo), /minlength="8"/);
  assert.match(context.WORKBENCH_MANAGEMENT_VIEWS.accountForm(ui()), /minlength="20"/);
});
check("file preview escapes metadata warnings and markdown rather than rendering HTML", () => {
  const bad = '<img src=x onerror="alert(1)">';
  const html = context.WORKBENCH_VIEWS.importPreview(ui(), { id: "imp1", filename: bad + ".md", file_size: 128, status: "preview", attempts: 1, characters: 25, warnings: [bad], sections: [{ title: bad, text: bad }] });
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img/);
  assert.match(html, /data-form="import-confirm"/);
  assert.match(html, /未发布/);
});
check("import failure and maximum attempts cannot be mistaken for saved material", () => {
  const html = context.WORKBENCH_VIEWS.importPreview(ui(), { filename: "broken.docx", file_size: 128, status: "failed", attempts: 3, error_message: "文件已损坏" });
  assert.match(html, /文件已损坏/);
  assert.match(html, /3 次解析上限/);
  assert.doesNotMatch(html, /data-form="import-confirm"/);
});
check("cancel states do not offer a material confirmation form", () => {
  for (const status of ["canceling", "canceled"]) {
    const html = context.WORKBENCH_VIEWS.importPreview(ui(), { filename: "demo.txt", file_size: 123, status, attempts: 1 });
    assert.doesNotMatch(html, /data-form="import-confirm"/);
    assert.match(html, status === "canceling" ? /尚未退出/ : /仍保留/);
  }
});
check("delete confirmation escapes file details and requires an explicit checkbox", () => {
  const html = context.WORKBENCH_VIEWS.importDelete(ui(), { id: "i1", filename: '<img src=x>.txt', file_size: 123, file_sha256: "0".repeat(64) });
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /type="checkbox"[^>]+required/);
  assert.match(html, /备份/);
});
check("unpublished sources are not offered as new document or assistant evidence", () => {
  assert.doesNotMatch(context.WORKBENCH_VIEWS.newDocument(ui()), /value="weekly"/);
  assert.doesNotMatch(context.WORKBENCH_VIEWS.references(ui()), /data-id="weekly"/);
});
check("current published picker survives a newer private draft without adding history", () => {
  const V = context.WORKBENCH_VIEWS;
  const current = { ...sources[0], id: "current_visible", publication: "published", is_current_published: true };
  const custom = { ...ui(), sources: [{ ...sources[0], id: "private_head", publication: "draft", is_current_published: false, published_source: current }, { ...current, id: "history", is_current_published: false }] };
  assert.deepEqual(Array.from(V.publishedSources(custom), item => item.id), ["current_visible"]);
  assert.match(V.composer(custom), /value="current_visible"/);
  assert.doesNotMatch(V.composer(custom), /value="private_head"|value="history"/);
});
check("source history is read-only and historical content cannot start a new excerpt", () => {
  const item = { ...sources[1], is_current_published: false, versions: [{ ...sources[1], is_current_published: false, version_number: 1 }] };
  const html = context.WORKBENCH_VIEWS.sourceDetail(ui(), item);
  assert.match(html, /历史发布版/);
  assert.match(html, /disabled aria-current="true"/);
  assert.doesNotMatch(html, /data-action="insert-excerpt"/);
});
check("sharing and new version forms bind family concurrency and escape source content", () => {
  const item = { ...sources[0], family_version: 7, title: '<img src=x>', publication: "published", scope: "selected", shared_user_ids: ["u_reviewer"] };
  const html = context.WORKBENCH_VIEWS.sourceSharing(ui(), item) + context.WORKBENCH_VIEWS.newSource(ui(), item);
  assert.match(html, /data-version="7"/);
  assert.match(html, /value="u_reviewer" checked/);
  assert.match(html, /全部历史发布版/);
  assert.match(html, /data-form="source-version"/);
  assert.doesNotMatch(html, /<img src=x/);
});
check("text PDF upload and exact page locator are explicit without OCR claim", () => {
  assert.match(context.WORKBENCH_VIEWS.importForm(ui()), /accept=".docx,.txt,.md,.pdf"/);
  assert.match(context.WORKBENCH_VIEWS.importForm(ui()), /扫描 PDF/);
  const html = context.WORKBENCH_VIEWS.evidenceDetail(ui(), { category: "其他", label: "v1", locator: "第 3 页 / 片段 1", version_id: "ver_pdf", historical: true, section: { title: "第3页", text: "Fictional text" } });
  assert.match(html, /第 3 页 \/ 片段 1/);
  assert.match(html, /历史发布版/);
});
check("import confirmation exposes only owned targets and preserves a missing target as an error", () => {
  state.importTargets = { job_version: "missing_target" };
  const html = context.WORKBENCH_VIEWS.importPreview(ui(), { id: "job_version", filename: "fictional.pdf", file_size: 200, status: "preview", attempts: 1, warnings: [], sections: [] });
  assert.match(html, /value="__unavailable__" selected/);
  assert.match(html, /value="weekly" data-version="1"/);
  assert.doesNotMatch(html, /value="product" data-version=/);
  state.importTargets = {};
});
check("stored upload target is restored without any frontend memory", () => {
  const html = context.WORKBENCH_VIEWS.importPreview(ui(), { id: "restored_job", filename: "fictional.pdf", file_size: 200, status: "preview", attempts: 1, warnings: [], sections: [], parent_source_id: "weekly", base_family_version: 1 });
  assert.match(html, /value="weekly" data-version="1" selected/);
});
check("stale persisted upload target requires a fresh explicit selection", () => {
  const custom = { ...ui(), sources: sources.map(item => item.id === "weekly" ? { ...item, family_version: 2 } : item) };
  const job = { id: "restored_stale", filename: "fictional.pdf", file_size: 200, status: "preview", attempts: 1, warnings: [], sections: [], parent_source_id: "weekly", base_family_version: 1 };
  assert.match(context.WORKBENCH_VIEWS.importPreview(custom, job), /value="__unavailable__" selected/);
  state.importTargets = { restored_stale: { id: "weekly", version: 2 } };
  const confirmed = context.WORKBENCH_VIEWS.importPreview(custom, job);
  assert.doesNotMatch(confirmed, /value="__unavailable__"/);
  assert.match(confirmed, /value="weekly" data-version="2" selected/);
  state.importTargets = { restored_stale: { id: "", version: null } };
  assert.doesNotMatch(context.WORKBENCH_VIEWS.importPreview(custom, job), /value="weekly"[^>]*selected/);
  state.importTargets = {};
});
check("editor escapes document title body comments and refs", () => {
  const bad = '<img src=x onerror="alert(1)">';
  const old = doc.title;
  doc.title = bad; doc.comments = [{ author_name: bad, text: bad, created_at: "2026-11-04" }];
  const html = context.WORKBENCH_VIEWS.editor(ui());
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img/);
  doc.title = old; doc.comments = [];
});
check("historical and shared editors contain no text editing controls", () => {
  state.historyMode = true;
  assert.doesNotMatch(context.WORKBENCH_VIEWS.editor(ui()), /data-block-text=/);
  state.historyMode = false; doc.can_edit = false;
  assert.doesNotMatch(context.WORKBENCH_VIEWS.editor(ui()), /data-action="save-document"/);
  doc.can_edit = true;
});
check("artifact sharing binds both versions and escapes the disclosure form", () => {
  const html = context.WORKBENCH_WRITING_VIEWS.artifactSharing(ui(), { ...doc, title: '<img src=x>', sharing: { scope: "selected", shared_user_ids: ["u_reviewer"], version: 4 } });
  assert.match(html, /data-version="4" data-revision="1"/);
  assert.match(html, /name="confirm_body"[^>]*required/);
  assert.match(html, /value="u_reviewer" checked/);
  assert.match(html, /后续保存的修订/);
  assert.doesNotMatch(html, /<img src=x|value="u_writer"/);
});
check("private revocation hides optional disclosure and recipient fields", () => {
  const html = context.WORKBENCH_WRITING_VIEWS.artifactSharing(ui(), doc);
  assert.match(html, /data-artifact-recipients hidden/);
  assert.match(html, /data-artifact-disclosure hidden/);
  assert.doesNotMatch(html, /name="confirm_body"[^>]*required/);
});
check("shared reading excludes export comments review actions and historical metadata", () => {
  const shared = { ...doc, access: "shared", can_edit: false, can_share: false, can_comment: false, can_export: false, history: [{ revision: 7, created_at: "hidden_history" }], comments: [{ text: "hidden_comment" }], reviews: [{ status: "approved", revision: 1, note: "hidden_review" }] };
  const html = context.WORKBENCH_VIEWS.editor({ ...ui(), state: { ...state, editor: shared } });
  assert.match(html, /共享阅读/);
  assert.match(html, /data-action="refresh-document"/);
  assert.doesNotMatch(html, /data-action="export-document"|data-action="artifact-sharing"|data-form="comment"|hidden_comment|hidden_review|hidden_history/);
});
check("unavailable artifact references cannot be opened from the sidebar", () => {
  const html = context.WORKBENCH_VIEWS.editor({ ...ui(), state: { ...state, editor: { ...doc, reference_details: [{ source_id: "restricted", title: "无权访问的来源", available: false }] } } });
  assert.match(html, /data-id="restricted" disabled title="引用资料未授权或已失效"/);
});
check("document tabs separate author drafts sharing and assigned pending reviews", () => {
  const documents = [{ ...doc, id: "mine", state: "待审核", version: "r1" }, { ...doc, id: "shared", owner_id: "u_admin", owner_name: "同事", access: "shared", is_shared: true, state: "草稿", version: "r1" }, { ...doc, id: "assigned", owner_id: "u_admin", access: "reviewer", state: "待审核", version: "r1" }];
  for (const [tab, expected] of [["mine", "mine"], ["shared", "shared"], ["review", "assigned"]]) {
    const html = context.CARD_HOME.documentGrid({ ...ui(), state: { ...state, documents, documentTab: tab, reviews: [{ artifact_id: "assigned" }] } });
    assert.deepEqual([...html.matchAll(/data-action="document" data-id="([^"]+)"/g)].map(match => match[1]), [expected]);
  }
});
check("structured body renders three heading levels inline styles and actual table cells", () => {
  const html = context.WORKBENCH_RICH_VIEWS.body(ui(), richFixture);
  for (const level of [1, 2, 3]) assert.match(html, new RegExp(`data-level="${level}"`));
  assert.match(html, /<strong>关键事项<\/strong>/); assert.match(html, /<em>/);
  assert.equal((html.match(/<th scope=/g) || []).length, 3);
  assert.equal((html.match(/<td>/g) || []).length, 6);
  assert.match(html, /rel="noopener noreferrer"/); assert.match(html, /demo=1&amp;source=workbench/);
});
check("structured text and cell content never become executable HTML", () => {
  const value = JSON.parse(JSON.stringify(richFixture));
  value.blocks[2].runs[0].text = '<script>alert(1)</script>';
  value.blocks[4].rows[0][0].runs[0].text = '<img src=x onerror="x">';
  value.blocks[0].level = '1 onclick="x"';
  const html = context.WORKBENCH_RICH_VIEWS.body(ui(), value);
  assert.doesNotMatch(html, /<script>|<img src=x|onclick="x"/); assert.match(html, /&lt;script&gt;/);
});
check("unsafe links remain literal text and preview links do not open external sites", () => {
  const R = context.WORKBENCH_RICH_VIEWS;
  for (const href of ["javascript:alert(1)", "data:text/html,x", "//example.com", "https://u:p@example.com", "https://example.com\\x", "https://example.com\n"]) assert.equal(R.safeHref(href), false);
  const html = R.inline(ui(), [{ text: "unsafe", marks: [{ type: "link", href: "javascript:alert(1)" }] }]);
  assert.equal(html, "unsafe");
  assert.doesNotMatch(R.body({ ...ui(), preview: true }, richFixture), /href="https:/);
});
check("structured owner view preserves review and sharing but does not offer legacy editing", () => {
  const rich = { ...doc, content_json: richFixture };
  const html = context.WORKBENCH_VIEWS.editor({ ...ui(), state: { ...state, editor: rich } });
  assert.match(html, /编辑器组件尚未接入/); assert.match(html, /data-action="submit-review"/); assert.match(html, /data-action="artifact-sharing"/);
  assert.doesNotMatch(html, /data-block-text=|data-action="save-document"|data-action="add-block"/);
  const source = context.WORKBENCH_VIEWS.sourceDetail({ ...ui(), state: { ...state, editor: rich } }, sources[0]);
  assert.doesNotMatch(source, /data-action="insert-excerpt"/);
});
check("structured shared reader does not gain internal records or writing actions", () => {
  const rich = { ...doc, access: "shared", can_edit: false, can_share: false, can_comment: false, content_json: richFixture };
  const html = context.WORKBENCH_VIEWS.editor({ ...ui(), state: { ...state, editor: rich } });
  assert.match(html, /rich-table/); assert.doesNotMatch(html, /data-action="submit-review"|data-action="artifact-sharing"|data-form="comment"/);
});
check("unsupported future formats fail closed for editing", () => {
  assert.equal(context.WORKBENCH_RICH_VIEWS.editable({ ...doc, content_json: { schema_version: 3 } }), false);
  assert.equal(context.WORKBENCH_RICH_VIEWS.editable(doc), true);
});
check("rich replacement preview discloses target style reset", () => {
  const html = context.WORKBENCH_WRITING_VIEWS.suggestionPreview(ui(), { base_revision: 2, target_format_reset: true, operation: "replace", target_text: "格式段落", excerpt: "新摘录", source: { title: "虚构资料", label: "v1", locator: "第1节" } });
  assert.match(html, /清除目标段落的行内样式与链接/);
});
check("evidence preview renders exact section and escapes all source content", () => {
  const html = context.WORKBENCH_VIEWS.evidenceDetail(ui(), { category: "通用资料", label: "v1", locator: "第 2 节", version_id: "ver_test", section: { title: '<img src=x>', text: '<script>private</script>' } });
  assert.match(html, /data-evidence-target tabindex="-1"/);
  assert.match(html, /ver_test/);
  assert.doesNotMatch(html, /<script>|<img src=x/);
});
check("conversation binds saved evidence identity rather than filtered position", () => {
  state.messages = [{ id: "m_test", question: "测试", matches: [{ evidence_id: "ev_fixed", source_id: "delivery", title: "交付", label: "v1", excerpt: "内容", locator: "第 2 节" }], unavailable_count: 1 }];
  const html = context.WORKBENCH_VIEWS.conversation(ui());
  assert.match(html, /data-action="evidence" data-id="ev_fixed" data-message="m_test"/);
  assert.match(html, /1 条旧引用已失效/);
  assert.match(html, /data-rating="unhelpful"/);
  state.messages = [];
});
check("conversation names questions and clarification prompts are escaped", () => {
  const bad = '<img src=x onerror="alert(1)">';
  const custom = { ...ui(), state: { ...state, activeConversation: "c1", conversations: [{ id: "c1", title: bad, status: "active", message_count: 1 }], messages: [{ id: "m1", question: bad, matches: [], reply_type: "needs_info", clarification_prompt: bad, sequence: 1 }] } };
  const html = context.WORKBENCH_VIEWS.conversationList(custom) + context.WORKBENCH_VIEWS.conversation(custom);
  assert.doesNotMatch(html, /<img src=x|data-action="answer-feedback"/);
  assert.match(html, /&lt;img|规则澄清/);
});
check("archived failed and older conversation pages never expose an ask form", () => {
  for (const change of [{ conversation: { status: "archived" } }, { chatError: "read failed" }, { currentChatCursor: 5 }]) {
    const html = context.WORKBENCH_VIEWS.composer({ ...ui(), state: { ...state, ...change } });
    assert.doesNotMatch(html, /data-form="ask"/);
  }
});
check("clarification mode and ambiguous-send retry are explicit", () => {
  const html = context.WORKBENCH_VIEWS.composer({ ...ui(), state: { ...state, activeConversation: "c1", conversation: { state: "waiting_for_info" }, pendingChats: { c1: {} }, chatDrafts: { c1: { text: "保留问题" } } } });
  assert.match(html, /data-mode="reply" aria-pressed="true"/);
  assert.match(html, /data-action="retry-chat"/);
  assert.match(html, /readonly/);
});
check("feedback form states private note and no training boundary", () => {
  for (const rating of ["helpful", "unhelpful"]) {
    const html = context.WORKBENCH_VIEWS.feedbackForm(ui(), { id: "m_test" }, rating);
    assert.match(html, /仅自己可见/);
    assert.match(html, /不触发模型训练/);
    assert.match(html, /maxlength="500"/);
    assert.match(html, /data-form="answer-feedback"/);
  }
});
check("admin feedback view uses metadata without rendering supplied private fields", () => {
  state.adminFeedback = [{ id: "f_test", rating: "unhelpful", reason: "missing", status: "pending", version: 1, answer_type: "no_evidence", note: "private-note", question: "private-question", resolution: "", updated_at: "2026-11-04" }];
  const html = context.WORKBENCH_VIEWS.feedbackQueue(ui());
  assert.doesNotMatch(html, /private-note|private-question/);
  assert.match(html, /data-action="triage-feedback"/);
  state.feedbackFilter = "closed";
  assert.doesNotMatch(context.WORKBENCH_VIEWS.feedbackQueue(ui()), /data-action="triage-feedback"/);
  state.feedbackFilter = "open";
});
check("closed feedback is read-only and escaped in triage", () => {
  const html = context.WORKBENCH_VIEWS.feedbackTriage(ui(), { id: "f_test", rating: "unhelpful", reason: "other", status: "closed", resolution: '<img src=x>', version: 2 });
  assert.doesNotMatch(html, /data-form="feedback-triage"|<img src=x/);
});
check("versioned draft form contains exact template id and bounded required metadata", () => {
  const html = context.WORKBENCH_WRITING_VIEWS.newDocument(ui());
  assert.match(html, /data-template-version="tv_solution_1"/);
  assert.match(html, /name="field_subject"/);
  assert.match(html, /name="field_objective"/);
  assert.match(html, /待确认/);
});
check("template editor uses fields checkboxes and ordered sections rather than executable JSON", () => {
  const html = context.WORKBENCH_WRITING_VIEWS.templateForm(ui(), templates[0]);
  assert.match(html, /name="required_0"/);
  assert.match(html, /data-action="move-template-section"/);
  assert.doesNotMatch(html, /textarea name="json"/);
});
check("document checks and preview escape user text and expose no direct write", () => {
  const W = context.WORKBENCH_WRITING_VIEWS;
  const html = W.suggestionPreview(ui(), { base_revision: 1, operation: "replace", target_text: '<script>x</script>', excerpt: '<img src=x>', source: { title: "资料", label: "v1", locator: "第 1 节" }, expires_at: "2026-11-04" });
  assert.doesNotMatch(html, /<script>|<img src=x/);
  assert.match(html, /非模型生成/);
  const original = state.editor;
  state.editor = { ...doc, template_binding: templates[0], fields: {} };
  assert.equal(W.localChecks(state.editor).missing_fields.length, 2);
  assert.match(W.editorInfo(ui()), /必填缺项 2/);
  assert.match(W.checksDetail(ui(), W.localChecks(state.editor)), /方案对象/);
  assert.match(W.documentFields(ui()), /data-form="document-fields"/);
  assert.doesNotMatch(W.documentFields(ui(), true), /<input|<textarea|<form/);
  assert.match(W.templateVersion(ui(), templates[0]), /只读/);
  assert.match(W.suggestionTarget(ui(), sources[1], 0), /name="target_block_id"/);
  state.editor = original;
});
check("metadata fields do not inherit JavaScript prototype properties", () => {
  const html = context.WORKBENCH_WRITING_VIEWS.fieldInputs(ui(), { fields: [{ key: "constructor", label: "字段", required: true, multiline: false }] });
  assert.doesNotMatch(html, /function Object/);
  assert.match(html, /value=""/);
});
check("account controls escape names and never render supplied credential fields", () => {
  const A = context.WORKBENCH_MANAGEMENT_VIEWS;
  const previous = state.adminUsers;
  state.adminUsers = [{ id: "u_test", username: "test", name: '<img src=x>', role: "employee", active: false, version: 2, password: "never-render-this", password_hash: "never-render-hash" }];
  const html = A.accounts(ui()) + A.accountForm(ui(), state.adminUsers[0]) + A.passwordForm(ui(), state.adminUsers[0]);
  assert.doesNotMatch(html, /<img src=x|never-render-this|never-render-hash/);
  assert.match(html, /data-action="edit-account"/);
  assert.match(html, /type="password"/);
  assert.match(html, /minlength="20"/);
  assert.match(html, /全部会话退出/);
  state.adminUsers = previous;
});
check("account filtering and read errors are distinct from empty results", () => {
  const A = context.WORKBENCH_MANAGEMENT_VIEWS;
  state.accountFilter = "inactive";
  assert.match(A.accounts(ui()), /没有匹配的账号/);
  state.accountFilter = "all"; state.adminUsersError = "暂不可用";
  const html = A.accounts(ui());
  assert.match(html, /重新读取账号/);
  assert.doesNotMatch(html, /data-action="edit-account"|没有匹配/);
  state.adminUsersError = "";
});
check("account pattern compiles under Unicode sets and accepts supported usernames", () => {
  const html = context.WORKBENCH_MANAGEMENT_VIEWS.accountForm(ui());
  const pattern = new RegExp("^(?:" + html.match(/pattern="([^"]+)"/)[1] + ")$", "v");
  for (const name of ["new.user", "staff-01", "user_02"]) assert.equal(pattern.test(name), true);
  for (const name of ["Bad User", "a", "1staff"]) assert.equal(pattern.test(name), false);
});
check("categories use catalog ids and protect the default without delete controls", () => {
  const A = context.WORKBENCH_MANAGEMENT_VIEWS;
  const html = A.categories(ui());
  assert.match(html, /data-id="cat_other" disabled/);
  assert.doesNotMatch(html, /delete-category|data-action="trash/);
  assert.match(context.WORKBENCH_VIEWS.newSource(ui()), /name="category_id"/);
  assert.match(A.categoryOptions(ui()), /value="cat_product"/);
  state.sourceCategory = "cat_product";
  const filtered = context.WORKBENCH_VIEWS.sourceList(ui());
  assert.match(filtered, /data-id="product"/);
  assert.doesNotMatch(filtered, /data-id="delivery"/);
  state.sourceCategory = "all";
});
check("category forms escape labels and failed reads do not expose an empty catalog", () => {
  const A = context.WORKBENCH_MANAGEMENT_VIEWS;
  assert.doesNotMatch(A.categoryForm(ui(), { id: "cat_test", name: '<img src=x>', active: true, version: 2 }), /<img src=x/);
  state.adminCategoriesError = "读取失败";
  assert.match(A.categories(ui()), /重新读取分类/);
  assert.doesNotMatch(A.categories(ui()), /新增分类|暂无分类/);
  state.adminCategoriesError = "";
});
check("all referenced static resources exist without external CDNs", () => {
  const html = fs.readFileSync(path.join(ROOT, "static/index.html"), "utf8");
  for (const [, target] of html.matchAll(/(?:src|href)="(\/static\/[^"#]+)"/g)) assert.ok(fs.existsSync(path.join(ROOT, target.slice(1))), target);
  assert.doesNotMatch(html, /https?:\/\//);
  for (let i = 1; i <= 3; i++) { const bytes = fs.readFileSync(path.join(ROOT, `static/assets/document-${i}.png`)); assert.equal(bytes.readUInt32BE(16), 520); assert.equal(bytes.readUInt32BE(20), 650); }
});
check("local icon library contains template and dialog tool icons", () => {
  const script = fs.readFileSync(path.join(ROOT, "static/app.js"), "utf8");
  for (const [, name] of script.matchAll(/\b(?:icon|iconButton)\("([a-z0-9-]+)"/g)) usedIcons.add(name);
  for (const [, name] of script.matchAll(/\bbutton\("[^"\n]*", "([a-z0-9-]+)"/g)) usedIcons.add(name);
  for (const name of usedIcons) assert.ok(lucide.icons[name.split("-").map(part => part[0].toUpperCase() + part.slice(1)).join("")], name);
});

function runtime(user = users[0], loginOptions = { demo: false, accounts: [], password_min_length: 20, remember_seconds: 28800 }) {
  const listeners = new Map();
  const node = () => ({ dataset: {}, innerHTML: "", textContent: "", disabled: false, isConnected: true, classList: { add() {}, remove() {}, toggle() {} }, querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, focus() {}, append() {}, remove() {}, click() {}, scrollIntoView() {} });
  const root = node(), dialog = node(), toast = node();
  let dialogForm = null;
  dialog.querySelector = selector => selector === "#template-edit-form" ? dialogForm : null;
  dialog.open = false; dialog.showModal = () => { dialog.open = true; }; dialog.close = () => { dialog.open = false; }; dialog.addEventListener = () => {};
  const calls = [], downloads = [];
  const database = { docs: [JSON.parse(JSON.stringify(doc))], drafts: new Map(), sources: JSON.parse(JSON.stringify(sources)), templates: JSON.parse(JSON.stringify(templates)), users: users.map(item => ({ ...item, active: true, version: 1 })), categories: JSON.parse(JSON.stringify(categories)), imports: [], tasks: [], reviews: [], messages: [], conversations: [], feedback: [], previews: new Map(), suggestions: new Map(), templateVersions: new Map(templates.map(item => [item.id, item])) };
  let failSave = false, failNetwork = false, failJSON = false, failRecords = false, failMessages = false, denyEvidence = false, failManagementJSON = false, denyArtifact = false, advanceBeforeDraftRead = false;
  const successfulWrites = new Map();
  let failChatJSON = false, chatReadGate, chatWriteGate;
  function holdChat(kind) { let release; const promise = new Promise(resolve => { release = resolve; }); if (kind === "read") chatReadGate = promise; else chatWriteGate = promise; return release; }
  function conversationInfo(item) {
    const messages = database.messages.filter(message => message.conversation_id === item.id), last = messages.at(-1);
    return { ...item, message_count: messages.length, last_message_id: last?.id || null, state: item.status === "archived" ? "archived" : last?.reply_type === "needs_info" ? "waiting_for_info" : "ready" };
  }
  let nextImport = 0;
  function element(dataset) { return { ...node(), dataset, parentElement: node() }; }
  async function fetcher(url, options) {
    const endpoint = url.replace("/api/workbench", "");
    const body = options.body ? options.body instanceof FakeFormData ? Object.fromEntries(options.body) : JSON.parse(options.body) : null;
    calls.push({ endpoint, body, headers: options.headers, method: options.method });
    if (endpoint === "/sources" && !body && failRecords) { failRecords = false; throw new Error("readback unavailable"); }
    if ((endpoint === "/messages" || /^\/conversations\/[^/]+\/messages/.test(endpoint)) && !body && failMessages) { failMessages = false; throw new Error("readback unavailable"); }
    if (/^\/conversations\/[^/]+\/messages/.test(endpoint)) {
      const gate = body ? chatWriteGate : chatReadGate;
      if (body) chatWriteGate = null; else chatReadGate = null;
      if (gate) await gate;
    }
    const key = options.headers["Idempotency-Key"];
    if (body && successfulWrites.has(key)) {
      const cached = JSON.parse(successfulWrites.get(key));
      if (/^\/conversations\/[^/]+\/messages$/.test(endpoint)) cached.data = { conversation: conversationInfo(database.conversations.find(item => item.id === endpoint.split("/")[2])), message: database.messages.find(item => item.id === cached.data.message.id) };
      if (/^\/conversations\/[^/]+$/.test(endpoint)) { const item = database.conversations.find(item => item.id === endpoint.split("/")[2]); cached.data = { ...conversationInfo(item), change_result: { applied_version: cached.data.change_result.applied_version, is_current: item.version === cached.data.change_result.applied_version } }; }
      if (/^\/artifacts\/[^/]+\/sharing$/.test(endpoint)) { const item = database.docs.find(item => item.id === endpoint.split("/")[2]); return { ok: true, status: 200, json: async () => ({ data: { ...item, sharing_result: { applied_version: cached.data.sharing_result.applied_version, is_current: item.sharing.version === cached.data.sharing_result.applied_version } } }) }; }
      if (endpoint === "/imports" && !database.imports.some(item => item.id === cached.data.id)) return { ok: false, status: 410, json: async () => ({ error: { code: "IMPORT_REMOVED", message: "文件已删除，请重新发起上传。" } }) };
      if (endpoint.startsWith("/imports/") && endpoint.endsWith("/cancel")) return { ok: true, status: 200, json: async () => ({ data: database.imports.find(item => item.id === cached.data.id) }) };
      return { ok: true, status: 200, json: async () => cached };
    }
    let result = {}, error;
    if (endpoint.endsWith("/revisions") && failNetwork) { failNetwork = false; throw new Error("offline"); }
    if (endpoint === "/login-options") result = loginOptions;
    else if (endpoint === "/session") result = { ...user, csrf: "mock-csrf" };
    else if (endpoint === "/sources") {
      const category = body?.category_id ? database.categories.find(item => item.id === body.category_id && item.active) : database.categories[0];
      if (body && !category) error = { code: "CATEGORY_UNAVAILABLE", message: "分类已停用，请重新选择。" };
      else { if (body) database.sources.push({ ...body, category_id: category.id, category: category.name, id: "src_new", version_id: "ver_new", owner_id: "u_writer", scope: "private", publication: "draft", version: 1, family_id: "family_new", family_version: 1, version_number: 1, shared_user_ids: [], is_current_published: false, sections: [{ title: "正文", text: body.text }] }); result = body ? database.sources.at(-1) : database.sources; }
    }
    else if (endpoint === "/users") result = database.users.filter(item => item.active).map(({ active, version, ...item }) => item);
    else if (endpoint === "/categories") result = database.categories.filter(item => item.active);
    else if (endpoint === "/admin/users") {
      if (body) {
        result = { id: "u_new_" + database.users.length, username: body.username, name: body.name, role: body.role, active: true, version: 1 };
        database.users.push(result);
      } else result = database.users;
    }
    else if (/^\/admin\/users\/[^/]+(?:\/password)?$/.test(endpoint)) {
      const item = database.users.find(item => item.id === endpoint.split("/")[3]);
      if (body.base_version !== item.version) error = { code: "VERSION_CONFLICT", message: "账号已变化，请刷新。" };
      else { const password = endpoint.endsWith("/password"); Object.assign(item, password ? {} : { name: body.name, role: body.role, active: body.active }, { version: item.version + 1 }); result = { ...item, reauthenticate: item.id === user.id && (password || item.role !== user.role || !item.active) }; }
    }
    else if (endpoint === "/admin/categories") {
      if (body) { result = { id: "cat_new_" + database.categories.length, name: body.name, active: true, version: 1 }; database.categories.push(result); }
      else result = database.categories;
    }
    else if (/^\/admin\/categories\/[^/]+$/.test(endpoint)) {
      const item = database.categories.find(item => item.id === endpoint.split("/")[3]);
      if (body.base_version !== item.version) error = { code: "VERSION_CONFLICT", message: "分类已变化，请刷新。" };
      else { Object.assign(item, { name: body.name, active: body.active, version: item.version + 1 }); for (const source of database.sources) if (source.category_id === item.id) source.category = item.name; result = item; }
    }
    else if (endpoint === "/templates") result = database.templates.filter(item => item.active);
    else if (endpoint === "/admin/templates") result = database.templates;
    else if (endpoint.startsWith("/templates/versions/")) result = database.templateVersions.get(endpoint.split("/").at(-1));
    else if (/^\/admin\/templates\/[^/]+\/versions$/.test(endpoint)) {
      const item = database.templates.find(item => item.template_id === endpoint.split("/")[3]);
      Object.assign(item, body, { number: item.number + 1, id: "tv_" + item.template_id + "_" + (item.number + 1) });
      item.history.unshift({ id: item.id, number: item.number, name: item.name }); database.templateVersions.set(item.id, JSON.parse(JSON.stringify(item))); result = item;
    }
    else if (endpoint === "/imports") {
      if (body) {
        const previous = database.imports.find(item => item.filename === body.file.name);
        if (!previous) database.imports.push({ id: "import" + (++nextImport), file_sha256: String(nextImport).padStart(64, "0"), filename: body.file.name, file_size: body.file.size, status: "queued", attempts: 0, warnings: [], characters: 0, sections: [], parent_source_id: options.headers["X-Source-Parent"] || null, base_family_version: options.headers["X-Source-Version"] ? Number(options.headers["X-Source-Version"]) : null, updated_at: "2026-11-04T00:00:00Z" });
        result = { ...(previous || database.imports.at(-1)), duplicate: Boolean(previous) };
      } else result = database.imports;
    }
    else if (endpoint === "/imports/usage") {
      const used = database.imports.reduce((sum, item) => sum + item.file_size, 0);
      const pending = database.imports.filter(item => ["queued", "running", "canceling", "preview"].includes(item.status)).length;
      result = { used_bytes: used, limit_bytes: 20 * 1024 * 1024, remaining_bytes: 20 * 1024 * 1024 - used, pending_count: pending, pending_limit: 5, remaining_slots: 5 - pending, retained_count: database.imports.length };
    }
    else if (endpoint.startsWith("/imports/")) {
      const item = database.imports.find(job => job.id === endpoint.split("/")[2]);
      if (endpoint.endsWith("/confirm")) {
        if (!item.source_id) database.sources.push({ id: "src_" + item.id, title: body.title, category: body.category, label: body.label, owner_id: "u_writer", publication: "draft", scope: "private", version_id: "ver_" + item.id, version: 1, sections: item.sections, attachment: { filename: item.filename, file_size: item.file_size } });
        item.source_id = "src_" + item.id; item.status = "committed";
        result = { source_id: item.source_id };
      } else if (endpoint.endsWith("/retry")) { item.status = "queued"; result = { id: item.id }; }
      else if (endpoint.endsWith("/cancel")) { item.status = item.status === "running" ? "canceling" : "canceled"; item.sections = []; item.characters = 0; result = item; }
      else if (endpoint.endsWith("/delete")) { result = { id: item.id, deleted: true, released_bytes: item.file_size }; database.imports = database.imports.filter(job => job.id !== item.id); }
      else result = item;
    }
    else if (endpoint === "/artifacts") {
      if (body && !database.templates.some(item => item.id === body.template_version_id && item.active)) error = { code: "TEMPLATE_UNAVAILABLE", message: "模板已更新。" };
      else { if (body) database.docs.push({ ...JSON.parse(JSON.stringify(doc)), id: "doc_new", title: body.title, template_binding: database.templateVersions.get(body.template_version_id), fields: body.fields || {} }); result = body ? { id: "doc_new", revision: 1 } : database.docs.filter(item => !denyArtifact || item.id !== "proposal"); }
    }
    else if (endpoint === "/reviews") result = database.reviews;
    else if (endpoint === "/tasks") result = database.tasks;
    else if (endpoint === "/conversations") {
      if (body) database.conversations.push({ id: "conv_" + (database.conversations.length + 1), title: body.title, status: "active", version: 1, user_id: user.id });
      result = body ? conversationInfo(database.conversations.at(-1)) : database.conversations.map(conversationInfo);
    }
    else if (/^\/conversations\/[^/]+/.test(endpoint)) {
      const item = database.conversations.find(item => item.id === endpoint.split("/")[2]);
      if (!item) error = { code: "NOT_FOUND", message: "会话不存在。" };
      else if (body && body.base_version !== item.version) error = { code: "VERSION_CONFLICT", message: "会话已变化，请刷新。" };
      else if (/\/messages(?:\?|$)/.test(endpoint)) {
        if (body && item.status === "archived") error = { code: "CONVERSATION_ARCHIVED", message: "会话已归档。" };
        else if (body) {
          const vague = body.text === "怎么办", count = database.messages.filter(message => message.conversation_id === item.id).length;
          database.messages.push({ id: "m" + (database.messages.length + 1), question: body.text, conversation_id: item.id, sequence: count + 1, clarification_for: body.reply_to || null, reply_type: vague ? "needs_info" : "evidence_excerpt", clarification_prompt: vague ? "请补充具体事项、目标或资料名称，再继续查询。" : "", matches: vague ? [] : [{ evidence_id: "ev_" + "1".repeat(64), version_id: "ver_delivery", section_index: 0, source_id: "delivery", title: "交付流程", label: "v2.0", excerpt: "实施说明与验收清单", locator: "第 1 节" }], feedback: null });
          if (item.title === "新会话") item.title = body.text;
          item.version++; result = { conversation: conversationInfo(item), message: database.messages.at(-1) };
        } else {
          const all = database.messages.filter(message => message.conversation_id === item.id), cursor = Number(new URL("http://mock" + endpoint).searchParams.get("cursor")) || all.length + 1;
          const selected = all.filter(message => message.sequence < cursor).slice(-20);
          result = { conversation: conversationInfo(item), messages: selected, next_cursor: selected[0]?.sequence > 1 ? String(selected[0].sequence) : null };
        }
      } else if (body) { Object.assign(item, body.title ? { title: body.title } : { status: body.status }); item.version++; result = { ...conversationInfo(item), change_result: { applied_version: item.version, is_current: true } }; }
      else result = conversationInfo(item);
    }
    else if (endpoint === "/messages") result = database.messages.filter(item => !item.conversation_id);
    else if (/^\/messages\/[^/]+\/evidence\//.test(endpoint)) {
      if (denyEvidence) error = { code: "NOT_FOUND", message: "引用已失效，请重新查询。" };
      else result = { ...database.messages[0].matches[0], category: "实施指南", section: { title: "交付准备", text: "实施说明与验收清单，这是精确章节的完整原文。" } };
    }
    else if (/^\/messages\/[^/]+\/feedback$/.test(endpoint)) {
      result = { id: "feedback1", ...body, status: "pending", version: 1, answer_type: "cited_excerpts", resolution: "", updated_at: "2026-11-04" };
      database.feedback.push(result); database.messages[0].feedback = result;
    }
    else if (endpoint.startsWith("/sources/")) {
      const item = database.sources.find(item => item.id === endpoint.split("/")[2]);
      if (endpoint.endsWith("/sharing")) {
        if (body.base_version !== item.family_version) error = { code: "VERSION_CONFLICT", message: "范围已变化，请重新打开。" };
        else { Object.assign(item, { scope: body.scope, shared_user_ids: body.shared_user_ids || [], family_version: item.family_version + 1 }); result = item; }
      } else if (endpoint.endsWith("/versions")) {
        if (body.base_version !== item.family_version) error = { code: "VERSION_CONFLICT", message: "资料已变化，请保留输入。" };
        else {
          const job = body.import_id ? database.imports.find(job => job.id === body.import_id) : null;
          const next = item.version_number + 1;
          result = { ...item, ...body, id: item.id + "_v" + next, version_id: "ver_" + next, version_number: next, family_version: item.family_version + 1, publication: "draft", is_current_published: false, scope: "private", sections: job ? job.sections : [{ title: "正文", text: body.text }], shared_user_ids: [] };
          for (const source of database.sources) if (source.family_id === item.family_id) source.family_version = result.family_version;
          database.sources.push(result);
          if (job) { job.status = "committed"; job.source_id = result.id; }
        }
      } else result = { ...item, versions: database.sources.filter(source => source.family_id === item.family_id), can_share: item.owner_id === user.id, can_add_version: item.owner_id === user.id && !database.sources.some(source => source.family_id === item.family_id && source.version_number > item.version_number) };
    }
    else if (endpoint === "/tasks/preview") { result = { ...body, id: "preview1", assignee_name: "审核同事", status: "awaiting_confirmation" }; database.previews.set(result.id, result); }
    else if (endpoint === "/task-previews/preview1/confirm") { const preview = database.previews.get("preview1"); if (!database.tasks.length) database.tasks.push({ ...preview, id: "task1", creator_id: "u_writer", status: "pending", version: 1 }); result = { id: "task1" }; }
    else if (/^\/tasks\/[^/]+\/status$/.test(endpoint)) { const task = database.tasks[0]; task.status = body.status; task.version++; result = task; }
    else if (/^\/artifacts\/[^/]+\/suggestions/.test(endpoint)) {
      const aid = endpoint.split("/")[2], sid = endpoint.split("/")[4], task = endpoint.split("/")[5];
      const item = database.docs.find(item => item.id === aid);
      if (!sid) {
        const source = database.sources.find(item => item.id === body.source_id), target = item.content_json.blocks.find(item => item.id === body.target_block_id);
        result = { id: "suggestion" + (database.suggestions.size + 1), artifact_id: aid, base_revision: body.base_revision, target_block_id: body.target_block_id, target_text: target.text, operation: body.operation, excerpt: source.sections[body.section_index].text, source: { source_id: source.id, title: source.title, label: source.label, locator: "第 1 节" }, status: "prepared", expires_at: "2026-11-04T00:10:00Z" };
        database.suggestions.set(result.id, result);
      } else {
        const preview = database.suggestions.get(sid);
        if (task === "apply") {
          if (preview.status === "prepared") { preview.previous = JSON.parse(JSON.stringify(item)); const targetIndex = item.content_json.blocks.findIndex(block => block.id === preview.target_block_id); if (preview.operation === "replace") item.content_json.blocks[targetIndex].text = preview.excerpt; else item.content_json.blocks.splice(targetIndex + 1, 0, { id: "b_" + sid, type: "paragraph", text: preview.excerpt }); item.revision++; item.status = "draft"; item.undo_suggestion = { id: sid, applied_revision: item.revision }; preview.status = "applied"; preview.applied_revision = item.revision; }
          result = { id: aid, revision: preview.applied_revision };
        } else if (task === "cancel") { preview.status = "canceled"; result = { id: sid, status: "canceled" }; }
        else if (task === "undo") { if (preview.status !== "undone") { const revision = item.revision + 1; Object.assign(item, JSON.parse(JSON.stringify(preview.previous)), { revision, undo_suggestion: null }); preview.status = "undone"; preview.undo_revision = revision; } result = { id: aid, revision: preview.undo_revision }; }
        else result = preview;
      }
    }
    else if (/^\/artifacts\/[^/]+/.test(endpoint)) {
      const id = endpoint.split("/")[2];
      const item = database.docs.find(item => item.id === id);
      if (denyArtifact) error = { code: "NOT_FOUND", message: "文档不存在或无访问权限。" };
      else if (endpoint.endsWith("/draft")) {
        if (!body && advanceBeforeDraftRead) { item.revision++; advanceBeforeDraftRead = false; }
        const draft = database.drafts.get(id) || { draft_version: 0, has_draft: false, base_revision: item.revision, draft: null };
        if (body) {
          if (body.base_draft_version !== draft.draft_version) error = { code: "DRAFT_CONFLICT", message: "工作草稿已在其他页面更新。" };
          else if (body.base_revision !== item.revision) error = { code: "REVISION_CONFLICT", message: "修订已变化。" };
          else { const { base_revision, base_draft_version, ...payload } = body; database.drafts.set(id, { draft_version: base_draft_version + 1, has_draft: true, base_revision, draft: payload }); result = { id, base_revision, draft_version: base_draft_version + 1 }; }
        } else result = { id, ...draft, current_revision: item.revision, stale: draft.base_revision !== item.revision, reference_details: item.reference_details };
      }
      else if (endpoint.endsWith("/draft/discard")) {
        const current = database.drafts.get(id);
        if ((current?.draft_version || 0) !== body.base_draft_version) error = { code: "DRAFT_CONFLICT", message: "草稿已变化。" };
        else { const draft_version = body.base_draft_version + 1; database.drafts.set(id, { draft_version, has_draft: false, base_revision: item.revision, draft: null }); result = { id, draft_version, is_current: true }; }
      }
      else if (endpoint.endsWith("/sharing")) {
        if (body.base_version !== item.sharing.version) error = { code: "VERSION_CONFLICT", message: "共享范围已变化，请读取最新范围。" };
        else if (body.base_revision !== item.revision) error = { code: "REVISION_CONFLICT", message: "正文已变化，请重新读取。" };
        else { item.sharing = { scope: body.scope, shared_user_ids: body.shared_user_ids, version: item.sharing.version + 1 }; result = { ...item, sharing_result: { applied_version: item.sharing.version, is_current: true } }; }
      }
      else if (endpoint.endsWith("/revisions")) {
        if (failSave) { failSave = false; error = { code: "REVISION_CONFLICT", message: "文档已被更新，请检查最新版本。" }; }
        else { item.title = body.title; item.content_json = body.content_json; item.citations = body.citations; item.fields = body.fields || item.fields; item.revision++; item.status = "draft"; item.draft_version = (database.drafts.get(id)?.draft_version || 0) + 1; database.drafts.set(id, { draft_version: item.draft_version, has_draft: false, base_revision: item.revision, draft: null }); result = { id, revision: item.revision, draft_version: item.draft_version }; }
      } else if (endpoint.endsWith("/reviews")) { const review = { id: "review1", artifact_id: id, revision: item.revision, reviewer_id: body.reviewer_id, status: "pending", reviewer_name: "审核同事" }; item.reviews.unshift(review); item.status = "pending"; result = review; }
      else if (endpoint.endsWith("/comments")) { item.comments.push({ author_name: "演示员工", text: body.text, created_at: "2026-11-04" }); result = { id: "c1" }; }
      else if (endpoint.endsWith("/exports")) result = { id: "export1", status: "ready" };
      else if (endpoint.endsWith("/checks")) result = context.WORKBENCH_WRITING_VIEWS.localChecks(item);
      else { item.can_export = item.access !== "shared" && item.status === "approved"; result = item; }
    } else if (endpoint === "/admin") result = { users: database.users.filter(item => item.active), audit: [] };
    else if (endpoint === "/admin/publications") result = [];
    else if (endpoint === "/admin/feedback") result = database.feedback.map(({ note, question, ...item }) => item);
    else if (/^\/admin\/feedback\/[^/]+\/status$/.test(endpoint)) { const item = database.feedback.find(item => item.id === endpoint.split("/")[3]); Object.assign(item, { status: body.status, resolution: body.resolution, version: item.version + 1 }); result = { id: item.id, status: item.status, version: item.version }; }
    const encoded = JSON.stringify(error ? { error } : { data: result });
    if (body && !error) successfulWrites.set(key, encoded);
    const malformed = endpoint.endsWith("/revisions") && failJSON || body && (endpoint === "/admin/users" || endpoint.endsWith("/password")) && failManagementJSON || body && /^\/conversations\/[^/]+\/messages$/.test(endpoint) && failChatJSON;
    if (malformed) { failJSON = false; failManagementJSON = false; failChatJSON = false; }
    return { ok: !error, status: error ? error.code === "NOT_FOUND" ? 404 : 409 : 200, json: async () => { if (malformed) throw new Error("truncated JSON"); return JSON.parse(encoded); } };
  }
  const document = {
    activeElement: node(), getElementById: id => ({ app: root, "workspace-dialog": dialog, toast })[id],
    addEventListener: (name, handler) => listeners.set(name, handler), querySelectorAll: () => [],
    createElement: tag => { const item = node(); if (tag === "a") item.click = () => downloads.push(item.href); return item; }
  };
  class FakeFormData extends Map { constructor(form) { super(Object.entries(form?.values || {})); } append(key, value) { this.set(key, value); } getAll(key) { const value = this.get(key); return Array.isArray(value) ? value : value ? [value] : []; } }
  const timers = new Map();
  let timerId = 0;
  const sandbox = vm.createContext({ document, window: { scrollTo() {}, addEventListener() {} }, location: { reload() {} }, console, URL, fetch: fetcher, FormData: FakeFormData, crypto: webcrypto, setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; }, clearTimeout: id => timers.delete(id), Intl, lucide: { createIcons() {} } });
  for (const filename of ["module-views.js", "rich-views.js", "writing-views.js", "management-views.js", "views.js", "autosave.js", "app.js"]) vm.runInContext(fs.readFileSync(path.join(ROOT, "static", filename), "utf8"), sandbox, { filename });
  const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
  async function click(action, dataset = {}) { const item = element({ action, ...dataset }); listeners.get("click")({ target: { closest: () => item }, preventDefault() {} }); await flush(); }
  async function submit(type, values, dataset = {}) {
    const categorySelect = { innerHTML: "", value: values.category_id || "" };
    const passwordFields = [{ value: values.password || "" }, { value: values.password_confirm || "" }];
    const form = { ...node(), dataset: Object.assign(dataset, { form: type }), values, categorySelect, passwordFields, matches: selector => selector === "[data-home-search]" ? false : true };
    form.querySelector = selector => selector === '[name="category_id"]' ? categorySelect : selector === "[data-import-target]" ? { selectedOptions: [{ dataset: { version: dataset.targetVersion || "1" } }] } : null;
    form.querySelectorAll = selector => selector === '[name="password"],[name="password_confirm"]' ? passwordFields : [];
    listeners.get("submit")({ target: form, preventDefault() {} }); await flush(); return form;
  }
  function input(selector, value, dataset = {}) { const item = { ...node(), value, dataset, matches: query => query.split(",").includes(selector) }; listeners.get("input")({ target: item }); }
  async function change(selector, value, values) {
    const recipients = { hidden: false }, disclosure = { hidden: false }, confirm = { required: true, checked: true };
    const fields = { username: { value: "writer" }, password: { value: "not-a-real-secret", focus() {} } };
    const form = { values, dataset: {}, querySelector: key => key === "[data-artifact-recipients]" ? recipients : disclosure, elements: { namedItem: name => selector === "[data-login-account]" ? fields[name] : confirm } };
    listeners.get("change")({ target: { ...node(), value, form, matches: query => query === selector } }); await flush();
    return { recipients, disclosure, confirm, fields };
  }
  async function poll() { for (const [id, timer] of timers) if (timer.delay === 1000) { timers.delete(id); await timer.fn(); await flush(); return true; } return false; }
  return { root, dialog, toast, calls, downloads, database, click, submit, input, change, poll, flush, holdChat, setFailChatJSON: () => { failChatJSON = true; }, setDialogForm: values => { dialogForm = { values }; }, setFailSave: () => { failSave = true; }, setFailNetwork: () => { failNetwork = true; }, setFailJSON: () => { failJSON = true; }, setFailRecords: () => { failRecords = true; }, setFailManagementJSON: () => { failManagementJSON = true; }, setFailMessages: () => { failMessages = true; }, advanceBeforeDraftRead: () => { advanceBeforeDraftRead = true; }, denyEvidence: () => { denyEvidence = true; }, denyArtifact: () => { denyArtifact = true; } };
}
function templateFormValues(item) {
  const values = { name: item.name, active: "on" };
  item.fields.forEach((field, index) => { Object.assign(values, { ["key_" + index]: field.key, ["label_" + index]: field.label }); if (field.required) values["required_" + index] = "on"; if (field.multiline) values["multiline_" + index] = "on"; });
  item.sections.forEach((section, index) => { Object.assign(values, { ["section_key_" + index]: section.key, ["section_title_" + index]: section.title, ["section_field_" + index]: section.field_keys.map(key => String(item.fields.findIndex(field => field.key === key))) }); });
  return values;
}
async function controllerChecks() {
  const loginApp = runtime(users[0], { demo: true, accounts: [{ username: "writer", label: "写作员工" }, { username: "admin", label: "管理员" }] });
  await loginApp.flush();
  const choice = await loginApp.change("[data-login-account]", "admin");
  check("choosing an account fills only its username and clears the previous account password", () => { assert.equal(choice.fields.username.value, "admin"); assert.equal(choice.fields.password.value, ""); });
  await loginApp.submit("login", { username: "writer", password: "test-only-value", remember: "on" });
  check("remembered login sends an explicit boolean without a credential idempotency cache", () => { const call = loginApp.calls.find(item => item.endpoint === "/session" && item.body); assert.equal(call.body.remember, true); });
  await loginApp.submit("login", { username: "writer", password: "test-only-value" });
  check("unchecked remembered login is explicitly false", () => { const call = loginApp.calls.filter(item => item.endpoint === "/session" && item.body).at(-1); assert.equal(call.body.remember, false); });
  const short = await loginApp.submit("account-edit", { username: "demo.test", name: "测试", role: "employee", password: "8letters", password_confirm: "8letters" });
  check("demo controller permits eight-character test passwords through the sensitive account endpoint", () => { assert.equal(loginApp.calls.filter(item => item.endpoint === "/admin/users" && item.body).length, 1); });
  await conversationControllerChecks();
  const readRaceApp = runtime(); await readRaceApp.flush(); readRaceApp.advanceBeforeDraftRead(); await readRaceApp.click("document", { id: "proposal" });
  check("document and draft revision read race pauses editing rather than mixing snapshots", () => { assert.match(readRaceApp.toast.textContent, /正式修订在读取时发生变化/); assert.match(readRaceApp.root.innerHTML, /本页编辑尚未覆盖/); assert.equal(readRaceApp.calls.filter(item => item.method === "POST" && item.endpoint.endsWith("/draft")).length, 0); });
  const navReadApp = runtime(); await navReadApp.flush(); await navReadApp.click("document", { id: "proposal" });
  navReadApp.input("[data-editor-title]", "跳转前草稿"); navReadApp.setFailRecords(); await navReadApp.click("open-documents");
  navReadApp.input("[data-editor-title]", "跳转失败后继续编辑"); await navReadApp.poll();
  check("failed navigation keeps the existing draft queue available for continued editing", () => { assert.equal(navReadApp.database.drafts.get("proposal").draft.title, "跳转失败后继续编辑"); assert.equal(navReadApp.database.docs[0].revision, 1); });
  const autoApp = runtime(); await autoApp.flush(); await autoApp.click("document", { id: "proposal" });
  autoApp.input("[data-editor-title]", "仅草稿中的新标题"); await autoApp.poll();
  check("background autosave uses draft endpoint and leaves revision title unchanged", () => { assert.equal(autoApp.database.docs[0].revision, 1); assert.equal(autoApp.database.drafts.get("proposal").draft.title, "仅草稿中的新标题"); assert.equal(autoApp.calls.filter(item => item.endpoint.endsWith("/revisions")).length, 0); });
  await autoApp.click("editor-panel", { id: "history" });
  check("acknowledged copy shows an explicit uncommitted status", () => { assert.match(autoApp.root.innerHTML, /工作草稿已同步/); assert.match(autoApp.root.innerHTML, /尚未形成正式修订/); });
  await autoApp.click("submit-review");
  check("autosaved content must become a revision before review", () => { assert.doesNotMatch(autoApp.dialog.innerHTML, /review-submit-form/); assert.match(autoApp.toast.textContent, /先保存/); });
  await autoApp.click("discard-reload");
  check("discard action asks before removing server copy", () => { assert.equal(autoApp.database.drafts.get("proposal").has_draft, true); assert.match(autoApp.dialog.innerHTML, /确认放弃/); });
  await autoApp.click("confirm-discard-draft");
  check("confirmed discard removes the copy without deleting revision history", () => { assert.equal(autoApp.database.drafts.get("proposal").has_draft, false); assert.equal(autoApp.database.docs[0].revision, 1); assert.doesNotMatch(autoApp.root.innerHTML, /仅草稿中的新标题/); });
  autoApp.input("[data-editor-title]", "本页未覆盖的内容");
  autoApp.database.drafts.set("proposal", { draft_version: 4, has_draft: true, base_revision: 1, draft: { title: "其他页面的草稿", content_json: doc.content_json, citations: doc.citations, fields: doc.fields || {} } });
  await autoApp.poll(); await autoApp.click("editor-panel", { id: "body" });
  check("draft conflict keeps local content and does not overwrite another tab", () => { assert.match(autoApp.root.innerHTML, /本页未覆盖的内容/); assert.match(autoApp.root.innerHTML, /草稿存在冲突/); assert.equal(autoApp.database.drafts.get("proposal").draft.title, "其他页面的草稿"); });
  await autoApp.click("open-documents");
  check("unsynchronized navigation requires a separate leave confirmation", () => assert.match(autoApp.dialog.innerHTML, /尚有未同步修改/));
  await autoApp.click("close-dialog"); await autoApp.click("show-working-copy");
  check("viewing latest working copy is a read-only modal not a local overwrite", () => { assert.match(autoApp.dialog.innerHTML, /其他页面的草稿/); assert.match(autoApp.root.innerHTML, /本页未覆盖的内容/); });
  const copyStaleApp = runtime(); await copyStaleApp.flush();
  copyStaleApp.database.drafts.set("proposal", { draft_version: 3, has_draft: true, base_revision: 0, draft: { title: "旧修订的草稿", content_json: doc.content_json, citations: doc.citations, fields: {} } });
  await copyStaleApp.click("document", { id: "proposal" });
  check("stale working copy is not restored over a newer revision", () => { assert.match(copyStaleApp.root.innerHTML, /工作草稿基于旧修订/); assert.doesNotMatch(copyStaleApp.root.innerHTML, /value="旧修订的草稿"/); assert.equal(copyStaleApp.calls.filter(item => item.method === "POST" && item.endpoint.endsWith("/draft")).length, 0); });
  const richApp = runtime(); richApp.database.docs[0].content_json = JSON.parse(JSON.stringify(richFixture));
  await richApp.flush(); await richApp.click("document", { id: "proposal" });
  await richApp.click("save-document"); await richApp.click("add-block");
  richApp.input("[data-editor-title]", "不能通过旧控件覆盖结构化文档");
  await richApp.click("save-document");
  check("structured owner cannot accidentally flatten the document through old controller actions", () => { assert.equal(richApp.calls.filter(call => call.endpoint.endsWith("/revisions")).length, 0); assert.equal(richApp.database.docs[0].content_json.blocks.length, richFixture.blocks.length); assert.match(richApp.root.innerHTML, /rich-table/); });
  await richApp.click("submit-review");
  check("read-only formatted owner can still submit the saved revision for review", () => assert.match(richApp.dialog.innerHTML, /data-form="review-submit"/));
  const sharingApp = runtime(); await sharingApp.flush();
  await sharingApp.click("document", { id: "proposal" }); await sharingApp.click("artifact-sharing");
  check("artifact sharing reloads current content and users before confirmation without writing", () => { assert.match(sharingApp.dialog.innerHTML, /data-form="artifact-sharing"/); assert.equal(sharingApp.calls.filter(call => call.method === "PATCH").length, 0); });
  await sharingApp.submit("artifact-sharing", { scope: "selected", shared_user_ids: ["u_reviewer"] }, { id: "proposal", version: "1", revision: "1" });
  check("artifact sharing refuses an unconfirmed body before requesting a write", () => { assert.match(sharingApp.toast.textContent, /请先确认正文/); assert.equal(sharingApp.calls.filter(call => call.method === "PATCH").length, 0); });
  const sharingDataset = { id: "proposal", version: "1", revision: "1" };
  sharingApp.setFailRecords();
  await sharingApp.submit("artifact-sharing", { scope: "selected", shared_user_ids: ["u_reviewer"], confirm_body: "yes" }, sharingDataset);
  await sharingApp.submit("artifact-sharing", { scope: "selected", shared_user_ids: ["u_reviewer"], confirm_body: "yes" }, sharingDataset);
  check("sharing readback failure retries one PATCH intent without incrementing twice", () => { const writes = sharingApp.calls.filter(call => call.method === "PATCH"); assert.equal(writes.length, 2); assert.equal(writes[0].headers["Idempotency-Key"], writes[1].headers["Idempotency-Key"]); assert.equal(writes[0].body.base_version, 1); assert.equal(writes[0].body.base_revision, 1); assert.equal(sharingApp.database.docs[0].sharing.version, 2); assert.equal(sharingApp.dialog.open, false); });
  sharingApp.database.docs[0].sharing = { scope: "private", shared_user_ids: [], version: 3 };
  await sharingApp.submit("artifact-sharing", { scope: "selected", shared_user_ids: ["u_reviewer"], confirm_body: "yes" }, sharingDataset);
  check("an old sharing retry reads back a subsequent revocation instead of regranting", () => { assert.equal(sharingApp.database.docs[0].sharing.scope, "private"); assert.match(sharingApp.toast.textContent, /未重新授权/); });
  sharingApp.input("[data-block-text]", "未保存内容不应在共享时丢失", { blockText: "b_body" });
  const beforeSharingReads = sharingApp.calls.length;
  await sharingApp.click("artifact-sharing");
  check("unsaved author edits block sharing and remain in memory", () => { assert.equal(sharingApp.calls.length, beforeSharingReads); assert.match(sharingApp.toast.textContent, /请先保存本页正文/); });
  const privateChoice = await sharingApp.change("[data-artifact-scope]", "private");
  const selectedChoice = await sharingApp.change("[data-artifact-scope]", "selected");
  check("scope changes keep recipient and body confirmation requirements in sync", () => { assert.ok(privateChoice.recipients.hidden && privateChoice.disclosure.hidden && !privateChoice.confirm.required); assert.ok(!selectedChoice.recipients.hidden && !selectedChoice.disclosure.hidden && selectedChoice.confirm.required); assert.equal(selectedChoice.confirm.checked, false); });
  const changedRecipient = await sharingApp.change("[data-artifact-recipient]", "u_admin");
  check("changing recipients requires a fresh body disclosure confirmation", () => assert.equal(changedRecipient.confirm.checked, false));
  const readerApp = runtime(users[1]);
  Object.assign(readerApp.database.docs[0], { access: "shared", can_edit: false, can_share: false, can_comment: false, can_export: false, is_shared: true });
  await readerApp.flush(); await readerApp.click("document", { id: "proposal" });
  await readerApp.click("save-document"); await readerApp.click("export-document"); await readerApp.submit("comment", { text: "不能留言" });
  check("shared controller never issues edit comment or export requests", () => assert.equal(readerApp.calls.filter(call => call.body && /^\/artifacts\//.test(call.endpoint)).length, 0));
  readerApp.denyArtifact(); await readerApp.click("refresh-document");
  check("revoked shared read clears the old body and returns to the filtered document space", () => { assert.doesNotMatch(readerApp.root.innerHTML, /data-editor-body|建立统一资料目录/); assert.match(readerApp.root.innerHTML, /暂无可见稿件/); assert.match(readerApp.toast.textContent, /无访问权限/); });
  const versionApp = runtime(); await versionApp.flush();
  await versionApp.click("source", { id: "weekly" });
  await versionApp.click("source-sharing");
  check("owner sharing opens a bound form without writing", () => { assert.match(versionApp.dialog.innerHTML, /data-form="source-sharing"/); assert.equal(versionApp.calls.filter(call => call.method === "PATCH").length, 0); });
  await versionApp.submit("source-sharing", { scope: "selected", shared_user_ids: ["u_reviewer"] }, { id: "weekly", version: "1" });
  check("sharing submits PATCH with family version then reads the source again", () => { const call = versionApp.calls.find(call => call.method === "PATCH"); assert.equal(call.endpoint, "/sources/weekly/sharing"); assert.equal(call.body.base_version, 1); assert.equal(versionApp.database.sources.find(item => item.id === "weekly").scope, "selected"); });
  await versionApp.click("source-version-text");
  check("new text version uses separate private draft form", () => { assert.match(versionApp.dialog.innerHTML, /data-form="source-version"/); assert.match(versionApp.dialog.innerHTML, /data-version="2"/); });
  await versionApp.submit("source-version", { title: "虚构新版本", label: "v2", category_id: "cat_other", text: "新版本正文" }, { id: "weekly", version: "2" });
  check("new version writes to the original lineage endpoint and never overwrites old source", () => { const created = versionApp.database.sources.at(-1); assert.equal(created.id, "weekly_v2"); assert.equal(created.scope, "private"); assert.notEqual(versionApp.database.sources.find(item => item.id === "weekly").sections[0].text, "新版本正文"); });
  await versionApp.click("source-version-file");
  check("upload new version preserves explicit parent and accepts text PDF", () => { assert.match(versionApp.dialog.innerHTML, /data-parent="weekly_v2"/); assert.match(versionApp.dialog.innerHTML, /\.pdf/); });
  await versionApp.submit("import-upload", { file: { name: "fictional.pdf", size: 150 } }, { parent: "weekly_v2", parentVersion: "3" });
  const versionJob = versionApp.database.imports[0];
  check("version upload transmits and reads back persistent target snapshot", () => { const call = versionApp.calls.find(call => call.endpoint === "/imports" && call.body); assert.equal(call.headers["X-Source-Parent"], "weekly_v2"); assert.equal(call.headers["X-Source-Version"], "3"); assert.equal(versionJob.parent_source_id, "weekly_v2"); assert.equal(versionJob.base_family_version, 3); });
  Object.assign(versionJob, { status: "preview", attempts: 1, sections: [{ title: "第1页", text: "Fictional PDF evidence", page_number: 1, fragment_number: 1 }], characters: 23 });
  await versionApp.click("import-job", { id: versionJob.id });
  check("parsed new version targets the chosen owned source without automatic commit", () => { assert.match(versionApp.dialog.innerHTML, /value="weekly_v2" data-version="3" selected/); assert.equal(versionJob.status, "preview"); });
  const refreshed = runtime();
  refreshed.database.sources = JSON.parse(JSON.stringify(versionApp.database.sources));
  refreshed.database.imports = JSON.parse(JSON.stringify(versionApp.database.imports));
  await refreshed.flush(); await refreshed.click("import-job", { id: versionJob.id });
  check("fresh controller restores target from stored import rather than prior page memory", () => assert.match(refreshed.dialog.innerHTML, /value="weekly_v2" data-version="3" selected/));
  refreshed.database.sources.find(item => item.id === "weekly_v2").family_version++;
  await refreshed.click("home"); await refreshed.click("import-job", { id: versionJob.id });
  check("changed target is not silently refreshed to a new baseline", () => assert.match(refreshed.dialog.innerHTML, /value="__unavailable__" selected/));
  await versionApp.submit("import-confirm", { parent_source_id: "weekly_v2", title: "虚构 PDF 新版", category_id: "cat_other", label: "v3" }, { id: versionJob.id, targetVersion: "3" });
  check("file confirmation sends parsed import id and optimistic family version", () => { const call = versionApp.calls.filter(call => call.endpoint === "/sources/weekly_v2/versions").at(-1); assert.equal(call.body.import_id, versionJob.id); assert.equal(call.body.base_version, 3); assert.equal(versionJob.status, "committed"); assert.equal(versionApp.database.sources.at(-1).sections[0].page_number, 1); });
  const app = runtime(); await app.flush();
  check("controller boots using login options session and eleven API resources", () => { assert.match(app.root.innerHTML, /功能模块/); assert.equal(app.calls.length, 13); });
  await app.click("open-library");
  check("knowledge module loads from API", () => assert.match(app.root.innerHTML, /data-source-query/));
  await app.click("source", { id: "delivery" });
  check("source modal fetches original sections", () => assert.match(app.dialog.innerHTML, /交付准备/));
  await app.click("close-dialog");
  await app.click("new-source");
  await app.submit("source", { title: "新录入资料", category: "通用资料", label: "v1", text: "人工录入的测试正文" });
  check("text material creation persists through API and is private draft", () => { assert.equal(app.database.sources.length, 6); assert.equal(app.database.sources.at(-1).publication, "draft"); assert.match(app.dialog.innerHTML, /人工录入的测试正文/); });
  await app.click("close-dialog");
  await app.click("document", { id: "proposal" });
  check("document opens editable body and citation sidebar", () => { assert.match(app.root.innerHTML, /data-block-text="b_body"/); assert.match(app.root.innerHTML, /引用资料/); });
  app.input("[data-block-text]", "更新后的正文与验收计划，待负责人确认。", { blockText: "b_body" });
  await app.click("open-documents");
  check("navigation acknowledges a working draft without adding a revision", () => { assert.equal(app.database.docs[0].revision, 1); assert.equal(app.database.drafts.get("proposal").has_draft, true); assert.match(app.root.innerHTML, /data-card-documents/); });
  await app.click("document", { id: "proposal" });
  check("opening again restores the acknowledged working draft", () => { assert.match(app.root.innerHTML, /已恢复工作草稿/); assert.match(app.root.innerHTML, /更新后的正文与验收计划/); });
  app.setFailSave(); await app.click("save-document");
  check("save conflict preserves unsaved content", () => { assert.match(app.root.innerHTML, /更新后的正文与验收计划/); assert.match(app.root.innerHTML, /本页编辑尚未覆盖/); assert.equal(app.database.docs[0].revision, 1); });
  await app.click("save-document");
  check("save increments server revision and reads it back", () => { assert.equal(app.database.docs[0].revision, 2); assert.match(app.root.innerHTML, /已保存 r2/); });
  await app.submit("comment", { text: "请核对补充信息" });
  check("comments are posted against a concrete revision", () => { const call = app.calls.find(item => item.endpoint.endsWith("/comments")); assert.equal(call.body.base_revision, 2); assert.match(app.root.innerHTML, /请核对补充信息/); });
  await app.click("source", { id: "delivery" });
  await app.click("insert-excerpt", { id: "delivery", section: "0" });
  check("source excerpt begins with explicit target choice and no document mutation", () => { assert.match(app.dialog.innerHTML, /选择采纳位置/); assert.equal(app.database.docs[0].revision, 2); });
  await app.submit("suggestion-target", { target_block_id: "b_body", operation: "insert_after" }, { source: "delivery", section: "0" });
  check("server preview does not save the draft", () => { assert.match(app.dialog.innerHTML, /采纳预览/); assert.equal(app.database.docs[0].revision, 2); });
  app.setFailRecords(); await app.click("apply-suggestion"); await app.click("apply-suggestion");
  check("apply retries after readback failure without duplicate insertion", () => { assert.equal(app.database.docs[0].revision, 3); assert.match(app.root.innerHTML, /准备实施说明/); assert.match(app.root.innerHTML, /已保存 r3/); });
  app.input("[data-block-text]", "采纳后继续编辑，仍待负责人确认。", { blockText: "b_body" });
  app.setFailNetwork(); await app.click("save-document"); await app.click("save-document");
  check("network retry reuses idempotency key for the same save", () => { const saves = app.calls.filter(item => item.endpoint.endsWith("/revisions")); assert.equal(saves.at(-1).headers["Idempotency-Key"], saves.at(-2).headers["Idempotency-Key"]); assert.equal(app.database.docs[0].revision, 4); });
  app.input("[data-block-text]", "补充负责人确认记录，继续按修订审阅。", { blockText: "b_body" });
  app.setFailJSON(); await app.click("save-document"); await app.click("save-document");
  check("truncated success response keeps the original operation key", () => { const saves = app.calls.filter(item => item.endpoint.endsWith("/revisions")); assert.equal(saves.at(-1).headers["Idempotency-Key"], saves.at(-2).headers["Idempotency-Key"]); assert.equal(app.database.docs[0].revision, 5); assert.match(app.root.innerHTML, /已保存 r5/); });
  await app.submit("review-submit", { reviewer_id: "u_reviewer" });
  check("review submit binds the saved revision", () => { const review = app.database.docs[0].reviews[0]; assert.equal(review.revision, 5); assert.equal(review.reviewer_id, "u_reviewer"); });
  app.database.docs[0].status = "approved";
  await app.click("document", { id: "proposal" }); await app.click("export-document");
  check("export requests authenticated binary download rather than faking success", () => { assert.equal(app.downloads[0], "/api/workbench/exports/export1/download"); assert.match(app.toast.textContent, /浏览器将请求下载/); });
  await app.click("new-task"); await app.submit("task", { title: "核对交付清单", note: "人工核对", assignee_id: "u_reviewer", due_date: "2026-11-10" });
  check("task preview has no side effect until confirmation", () => { assert.equal(app.database.tasks.length, 0); assert.match(app.dialog.innerHTML, /当前仅为预览/); });
  await app.click("confirm-task");
  check("confirmed task is stored then read back", () => { assert.equal(app.database.tasks.length, 1); assert.match(app.dialog.innerHTML, /核对交付清单/); });
  await app.click("close-dialog"); await app.click("ask", { question: "交付验收需要哪些材料？" });
  check("assistant shows cited source excerpts with offline mode", () => { assert.match(app.root.innerHTML, /实施说明与验收清单/); assert.match(app.root.innerHTML, /资料摘录 \/ 离线检索/); assert.match(app.root.innerHTML, /data-id="delivery"/); });
  await app.click("evidence", { id: "ev_" + "1".repeat(64), message: "m1" });
  check("citation action fetches saved evidence endpoint and exact section", () => { assert.match(app.dialog.innerHTML, /精确章节的完整原文/); assert.equal(app.calls.at(-1).endpoint, "/messages/m1/evidence/ev_" + "1".repeat(64)); });
  await app.click("close-dialog"); app.denyEvidence();
  await app.click("evidence", { id: "ev_" + "1".repeat(64), message: "m1" });
  check("unavailable evidence does not open cached full text", () => { assert.equal(app.dialog.open, false); assert.match(app.toast.textContent, /引用已失效/); });
  await app.click("answer-feedback", { id: "m1", rating: "unhelpful" });
  check("opening feedback form does not submit it", () => { assert.equal(app.database.feedback.length, 0); assert.match(app.dialog.innerHTML, /data-form="answer-feedback"/); });
  const feedbackIntent = { id: "m1", rating: "unhelpful" };
  app.setFailMessages();
  await app.submit("answer-feedback", { reason: "missing", note: "private-note" }, feedbackIntent);
  await app.submit("answer-feedback", { reason: "missing", note: "private-note" }, feedbackIntent);
  check("feedback retry preserves intent and reads back one persisted record", () => { assert.equal(app.database.feedback.length, 1); const calls = app.calls.filter(item => item.endpoint.endsWith("/feedback") && item.body); assert.equal(calls.at(-1).headers["Idempotency-Key"], calls.at(-2).headers["Idempotency-Key"]); assert.match(app.root.innerHTML, /待处理/); assert.match(app.root.innerHTML, /private-note/); });
  Object.assign(app.database.messages[0].feedback, { status: "closed", resolution: "已完成问题分类" });
  await app.click("refresh-feedback");
  check("employee can refresh and read admin resolution", () => assert.match(app.root.innerHTML, /已完成问题分类/));
  const adminApp = runtime(users[2]); await adminApp.flush();
  await adminApp.click("source", { id: "product" });
  await adminApp.click("withdraw-source", { id: "delivery" });
  check("withdraw confirmation fetches its exact source rather than another cached modal", () => { assert.equal(adminApp.calls.at(-1).endpoint, "/sources/delivery"); assert.match(adminApp.dialog.innerHTML, /项目交付与验收流程/); assert.equal(adminApp.database.sources.find(item => item.id === "delivery").publication, "published"); });
  await adminApp.click("close-dialog");
  adminApp.database.feedback.push({ id: "f_admin", rating: "unhelpful", reason: "missing", status: "pending", version: 1, resolution: "", answer_type: "no_evidence", note: "private-note", question: "private-question", updated_at: "2026-11-04" });
  await adminApp.click("open-admin"); await adminApp.click("admin-tab", { id: "feedback" });
  check("admin feedback tab contains safe metadata only", () => { assert.match(adminApp.root.innerHTML, /回答反馈/); assert.doesNotMatch(adminApp.root.innerHTML, /private-note|private-question/); });
  await adminApp.click("triage-feedback", { id: "f_admin" });
  const beforeTriage = adminApp.calls.length;
  await adminApp.submit("feedback-triage", { status: "closed", resolution: "" }, { id: "f_admin", version: "1" });
  check("closing feedback requires resolution before sending", () => { assert.equal(adminApp.calls.length, beforeTriage); assert.match(adminApp.toast.textContent, /请填写处理备注/); });
  await adminApp.submit("feedback-triage", { status: "closed", resolution: "已归档复现条件" }, { id: "f_admin", version: "1" });
  check("admin triage writes optimistic version and re-reads status", () => { const call = adminApp.calls.find(item => item.endpoint.endsWith("/status")); assert.equal(call.body.base_version, 1); assert.equal(adminApp.database.feedback[0].status, "closed"); assert.match(adminApp.toast.textContent, /已保存/); });
  await app.click("open-documents"); await app.click("new-document");
  const formIntent = {};
  app.setFailRecords(); await app.submit("document", { title: "新建方案", template: "solution", notes: "需求已确认", source_ids: ["product"] }, formIntent);
  await app.submit("document", { title: "新建方案", template: "solution", notes: "需求已确认", source_ids: ["product"] }, formIntent);
  check("form retry after a readback failure does not duplicate the create", () => { const creates = app.calls.filter(item => item.endpoint === "/artifacts" && item.body); assert.equal(creates.at(-1).headers["Idempotency-Key"], creates.at(-2).headers["Idempotency-Key"]); assert.equal(app.database.docs.length, 2); });
  check("new template document is created and opened as editable draft", () => { assert.equal(app.database.docs.length, 2); assert.match(app.root.innerHTML, /value="新建方案"/); });
  check("draft binds the selected template version with visible missing information", () => { assert.equal(app.database.docs[1].template_binding.id, "tv_solution_1"); assert.match(app.root.innerHTML, /必填缺项 2/); });
  await app.click("submit-review");
  check("missing required metadata blocks review in the UI", () => assert.match(app.toast.textContent, /补齐并保存/));
  await app.click("document-fields"); await app.submit("document-fields", { field_subject: "演示企业", field_objective: "规范文档交付" });
  check("metadata editing does not silently overwrite document text", () => { assert.match(app.root.innerHTML, /未保存/); assert.equal(app.database.docs[1].fields.subject, ""); assert.doesNotMatch(app.database.docs[1].content_json.blocks[1].text, /演示企业/); });
  await app.click("save-document");
  check("metadata persists with the new revision", () => { assert.equal(app.database.docs[1].fields.subject, "演示企业"); assert.match(app.root.innerHTML, /必填缺项 0/); });
  await app.click("writing-checks");
  check("saved document checks use an authorized server endpoint", () => { assert.match(app.dialog.innerHTML, /无必填缺项/); assert.match(app.calls.at(-1).endpoint, /\/checks$/); });
  await app.click("close-dialog"); await app.click("source", { id: "delivery" }); await app.click("insert-excerpt", { id: "delivery", section: "0" });
  await app.submit("suggestion-target", { target_block_id: "b_body", operation: "insert_after" }, { source: "delivery", section: "0" });
  const beforeCancel = app.database.docs[1].revision;
  await app.click("cancel-suggestion");
  check("canceling a server preview changes no document revision", () => assert.equal(app.database.docs[1].revision, beforeCancel));
  await app.click("source", { id: "delivery" }); await app.click("insert-excerpt", { id: "delivery", section: "0" });
  await app.submit("suggestion-target", { target_block_id: "b_body", operation: "replace" }, { source: "delivery", section: "0" });
  app.input("[data-block-text]", "预览后另行修改本页正文", { blockText: "b_body" });
  const applyCalls = app.calls.filter(item => item.endpoint.endsWith("/apply")).length;
  await app.click("apply-suggestion");
  check("unsaved target changes block apply before any write request", () => { assert.equal(app.calls.filter(item => item.endpoint.endsWith("/apply")).length, applyCalls); assert.match(app.toast.textContent, /已变化/); });
  await app.click("close-dialog"); await app.click("save-document");
  await app.click("source", { id: "delivery" }); await app.click("insert-excerpt", { id: "delivery", section: "0" });
  await app.submit("suggestion-target", { target_block_id: "b_body", operation: "replace" }, { source: "delivery", section: "0" }); await app.click("apply-suggestion");
  const undoId = app.database.docs[1].undo_suggestion.id;
  await app.click("undo-suggestion", { id: undoId });
  check("undo of adoption saves another revision and restores previous text", () => { assert.equal(app.database.docs[1].undo_suggestion, null); assert.match(app.root.innerHTML, /预览后另行修改本页正文/); assert.match(app.toast.textContent, /历史记录保留/); });
  await app.click("new-document");
  await app.change("[data-new-template]", "delivery", { title: "切换模板保留标题", notes: "保留补充", source_ids: ["product"], field_subject: "方案对象原值" });
  check("changing template retains shared draft data and loads its field schema", () => { assert.match(app.dialog.innerHTML, /切换模板保留标题/); assert.match(app.dialog.innerHTML, /name="field_content"/); assert.doesNotMatch(app.dialog.innerHTML, /name="field_objective"/); });
  await app.click("close-dialog");
  await adminApp.click("admin-tab", { id: "templates" }); await adminApp.click("edit-template", { id: "solution" });
  check("admin can open a structured template-version editor", () => assert.match(adminApp.dialog.innerHTML, /data-form="template-edit"/));
  const editedTemplateValues = { ...templateFormValues(templates[0]), name: "新版产品方案" };
  await adminApp.submit("template-edit", editedTemplateValues, { id: "solution", version: "1" });
  check("template save increments version and leaves pre-existing document binding intact", () => { assert.equal(adminApp.database.templates.find(item => item.template_id === "solution").number, 2); assert.equal(app.database.docs[1].template_binding.id, "tv_solution_1"); assert.match(adminApp.toast.textContent, /原有稿件未改写/); });
  await adminApp.click("template-history", { id: "tv_solution_1" });
  check("old template versions remain read-only", () => { assert.match(adminApp.dialog.innerHTML, /只读/); assert.doesNotMatch(adminApp.dialog.innerHTML, /data-form="template-edit"/); });
  const staleApp = runtime(); await staleApp.flush(); await staleApp.click("new-document");
  staleApp.database.templates.find(item => item.template_id === "solution").id = "tv_solution_2";
  staleApp.database.templates.find(item => item.template_id === "solution").number = 2;
  await staleApp.submit("document", { title: "保留未创建的标题", template: "solution", notes: "保留备注", source_ids: ["product"], field_subject: "保留对象", field_objective: "保留目标" }, { templateVersion: "tv_solution_1" });
  check("stale template rejection refreshes schema but preserves draft input for confirmation", () => { assert.equal(staleApp.database.docs.length, 1); assert.match(staleApp.dialog.innerHTML, /data-template-version="tv_solution_2"/); assert.match(staleApp.dialog.innerHTML, /保留未创建的标题/); assert.match(staleApp.dialog.innerHTML, /保留对象/); assert.match(staleApp.toast.textContent, /再次创建/); });
  await app.click("open-library"); await app.click("import-file");
  check("knowledge module offers bounded file upload", () => { assert.match(app.root.innerHTML, /data-action="import-file"/); assert.match(app.dialog.innerHTML, /accept=".docx,.txt,.md,.pdf"/); assert.match(app.dialog.innerHTML, /2 MiB/); });
  const uploadIntent = {};
  app.setFailRecords(); await app.submit("import-upload", { file: { name: "导入交付.md", size: 123 } }, uploadIntent);
  await app.submit("import-upload", { file: { name: "导入交付.md", size: 123 } }, uploadIntent);
  check("upload retry preserves intent and leaves parsing separate from source creation", () => {
    const uploads = app.calls.filter(item => item.endpoint === "/imports" && item.body);
    assert.equal(uploads.at(-1).headers["Idempotency-Key"], uploads.at(-2).headers["Idempotency-Key"]);
    assert.equal(uploads.at(-1).headers["Content-Type"], undefined);
    assert.equal(uploads.at(-1).headers["X-CSRF-Token"], "mock-csrf");
    assert.equal(app.database.imports.length, 1);
    assert.equal(app.database.sources.length, 6);
    assert.match(app.dialog.innerHTML, /等待解析/);
  });
  const waitingHTML = app.dialog.innerHTML;
  await app.poll();
  check("unchanged polling does not redraw the dialog", () => assert.equal(app.dialog.innerHTML, waitingHTML));
  Object.assign(app.database.imports[0], { status: "preview", attempts: 1, characters: 18, sections: [{ title: "第1行", text: "入库前人工核对交付依据。" }], warnings: [] });
  await app.poll();
  check("parsed text is previewed but does not auto-publish or create sources", () => { assert.match(app.dialog.innerHTML, /入库前人工核对/); assert.match(app.dialog.innerHTML, /确认私有入库/); assert.equal(app.database.sources.length, 6); });
  await app.submit("import-confirm", { title: "导入交付", category: "实施指南", label: "v1.0" }, { id: "import1" });
  check("confirmation reads back the committed private source using source_id", () => { assert.equal(app.database.sources.length, 7); assert.equal(app.database.sources.at(-1).scope, "private"); assert.equal(app.database.sources.at(-1).publication, "draft"); assert.match(app.dialog.innerHTML, /入库前人工核对/); assert.match(app.toast.textContent, /尚未发布/); });
  await app.click("download-source", { id: "src_import1" });
  check("original download requests the permission-checked file route", () => { assert.equal(app.downloads.at(-1), "/api/workbench/sources/src_import1/file"); assert.match(app.toast.textContent, /再次检查资料权限/); });
  await app.click("close-dialog"); await app.click("import-file");
  const secondUploadIntent = {};
  await app.submit("import-upload", { file: { name: "broken.docx", size: 123 } }, secondUploadIntent);
  Object.assign(app.database.imports[1], { status: "failed", attempts: 1, error_message: "DOCX 已损坏" });
  await app.poll();
  check("parse failure offers an explicit retry without a confirm control", () => { assert.match(app.dialog.innerHTML, /DOCX 已损坏/); assert.match(app.dialog.innerHTML, /data-action="retry-import"/); assert.doesNotMatch(app.dialog.innerHTML, /data-form="import-confirm"/); });
  await app.click("retry-import", { id: "import2" });
  check("retry requeues the original job rather than uploading a duplicate", () => { assert.equal(app.database.imports.length, 2); assert.equal(app.database.imports[1].status, "queued"); assert.match(app.dialog.innerHTML, /等待解析/); });
  await app.click("close-dialog");
  const stillPolling = await app.poll();
  check("closing import preview stops its polling", () => assert.equal(stillPolling, false));
  await app.click("import-file");
  const before = app.calls.length;
  await app.submit("import-upload", { file: { name: "large.txt", size: 2 * 1024 * 1024 + 1 } });
  check("oversized selection is rejected before HTTP upload", () => { assert.equal(app.calls.length, before); assert.match(app.toast.textContent, /不能超过 2 MiB/); });
  await app.click("close-dialog"); await app.click("import-job", { id: "import2" });
  await app.click("cancel-import", { id: "import2" });
  check("opening cancel confirmation has no write side effect", () => { assert.equal(app.database.imports[1].status, "queued"); assert.match(app.dialog.innerHTML, /data-form="import-cancel"/); });
  const cancelIntent = { id: "import2" };
  await app.submit("import-cancel", {}, cancelIntent);
  check("confirmed cancellation retains original and refreshes usage", () => { assert.equal(app.database.imports[1].status, "canceled"); assert.equal(app.database.imports.length, 2); assert.match(app.dialog.innerHTML, /仍保留/); assert.match(app.root.innerHTML, /246 B/); });
  await app.click("retry-import", { id: "import2" });
  check("canceled file can be explicitly resumed", () => assert.equal(app.database.imports[1].status, "queued"));
  await app.submit("import-cancel", {}, cancelIntent);
  check("old cancel intent reports the resumed status without canceling it again", () => { assert.equal(app.database.imports[1].status, "queued"); assert.match(app.toast.textContent, /当前状态为“等待解析”/); });
  app.database.imports[1].status = "running";
  await app.click("import-job", { id: "import2" }); await app.click("cancel-import", { id: "import2" });
  await app.submit("import-cancel", {}, { id: "import2" });
  check("canceling running parse is not presented as stopped or deletable", () => { assert.equal(app.database.imports[1].status, "canceling"); assert.match(app.dialog.innerHTML, /尚未退出/); assert.doesNotMatch(app.dialog.innerHTML, /data-action="delete-import"/); });
  app.database.imports[1].status = "canceled";
  await app.poll();
  check("worker cancellation acknowledgement enables file deletion", () => assert.match(app.dialog.innerHTML, /data-action="delete-import"/));
  await app.click("delete-import", { id: "import2" });
  const deleteIntent = { id: "import2" };
  const deletedHash = app.database.imports[1].file_sha256;
  const noDeleteYet = app.calls.length;
  await app.submit("import-delete", { file_sha256: deletedHash }, deleteIntent);
  check("unchecked delete confirmation makes no API call", () => { assert.equal(app.calls.length, noDeleteYet); assert.equal(app.database.imports.length, 2); });
  app.setFailRecords(); await app.submit("import-delete", { confirm: "yes", file_sha256: deletedHash }, deleteIntent);
  await app.submit("import-delete", { confirm: "yes", file_sha256: deletedHash }, deleteIntent);
  check("delete retry reuses intent and reads back reclaimed quota without double deletion", () => { const deletes = app.calls.filter(item => item.endpoint.endsWith("/delete")); assert.equal(deletes.at(-1).headers["Idempotency-Key"], deletes.at(-2).headers["Idempotency-Key"]); assert.equal(deletes.at(-1).body.confirm, true); assert.equal(deletes.at(-1).body.file_sha256, deletedHash); assert.equal(app.database.imports.length, 1); assert.match(app.root.innerHTML, /123 B/); assert.match(app.toast.textContent, /额度已回收/); });
  await app.click("import-job", { id: "import1" });
  check("committed import offers no destructive file controls", () => { assert.doesNotMatch(app.dialog.innerHTML, /data-action="delete-import"/); assert.doesNotMatch(app.dialog.innerHTML, /data-action="cancel-import"/); });
  await app.click("close-dialog"); await app.click("import-file");
  await app.submit("import-upload", { file: { name: "broken.docx", size: 123 } }, secondUploadIntent);
  check("replaying removed upload does not silently recreate it", () => { assert.equal(app.database.imports.length, 1); assert.match(app.toast.textContent, /文件已删除/); assert.equal(secondUploadIntent.intentKey, undefined); });
  await app.submit("import-upload", { file: { name: "broken.docx", size: 123 } }, secondUploadIntent);
  check("explicit new upload after removal creates a new identity", () => { assert.equal(app.database.imports.length, 2); assert.equal(app.database.imports[1].id, "import3"); });
  const manage = runtime(users[2]); await manage.flush(); await manage.click("open-admin"); await manage.click("admin-tab", { id: "accounts" });
  check("admin account page offers actual create edit and password actions", () => { assert.match(manage.root.innerHTML, /data-action="new-account"/); assert.match(manage.root.innerHTML, /data-action="reset-account-password"/); });
  await manage.click("new-account");
  const writesBeforeMismatch = manage.calls.filter(item => item.body).length;
  const password = "not-a-real-password-" + "1".repeat(20);
  await manage.submit("account-edit", { name: "新同事", username: "new.user", role: "employee", password, password_confirm: password + "x" }, {});
  check("password mismatch is rejected before any write", () => { assert.equal(manage.calls.filter(item => item.body).length, writesBeforeMismatch); assert.match(manage.toast.textContent, /不一致/); });
  const credentialIntent = {};
  const newAccountValues = { name: "新同事", username: "new.user", role: "employee", password, password_confirm: password };
  manage.setFailManagementJSON(); await manage.submit("account-edit", newAccountValues, credentialIntent);
  const savedCredentialForm = await manage.submit("account-edit", newAccountValues, credentialIntent);
  check("ambiguous credential response retries one intent without duplicate accounts or password echo", () => {
    const writes = manage.calls.filter(item => item.endpoint === "/admin/users" && item.body);
    assert.equal(writes.at(-1).headers["Idempotency-Key"], writes.at(-2).headers["Idempotency-Key"]);
    assert.equal(manage.database.users.filter(item => item.username === "new.user").length, 1);
    assert.equal(manage.dialog.open, false);
    assert.match(manage.toast.textContent, /不回显/);
    assert.doesNotMatch(manage.root.innerHTML + manage.toast.textContent, new RegExp(password));
  });
  check("confirmed credential write clears both password controls before list readback", () => assert.ok(savedCredentialForm.passwordFields.every(input => input.value === "")));
  await manage.click("edit-account", { id: "u_writer" });
  const noConfirmCalls = manage.calls.filter(item => item.body).length;
  await manage.submit("account-edit", { name: "演示员工", role: "employee" }, { id: "u_writer", version: "1" });
  check("account status change requires explicit confirmation", () => assert.equal(manage.calls.filter(item => item.body).length, noConfirmCalls));
  await manage.submit("account-edit", { name: "演示员工", role: "employee", confirm: "yes" }, { id: "u_writer", version: "1" });
  check("disable is PATCH with explicit false and current version", () => {
    const call = manage.calls.find(item => item.endpoint === "/admin/users/u_writer" && item.body);
    assert.equal(call.method, "PATCH"); assert.equal(call.body.active, false); assert.equal(call.body.base_version, 1);
    assert.equal(manage.database.users[0].active, false);
    assert.match(manage.root.innerHTML, /已停用/);
  });
  await manage.click("edit-account", { id: "u_reviewer" });
  manage.setFailRecords();
  await manage.submit("account-edit", { name: "更新后的同事", role: "employee", active: "on", confirm: "yes" }, { id: "u_reviewer", version: "1" });
  check("successful mutation and failed list readback are reported separately", () => {
    assert.equal(manage.database.users[1].name, "更新后的同事");
    assert.match(manage.toast.textContent, /账号已更新；列表暂未回读/);
    assert.match(manage.root.innerHTML, /重新读取账号/);
    assert.doesNotMatch(manage.root.innerHTML, /data-action="edit-account"/);
  });
  await manage.click("refresh-admin-users");
  check("account refresh restores confirmed current rows", () => assert.match(manage.root.innerHTML, /更新后的同事/));
  await manage.click("admin-tab", { id: "templates" }); await manage.click("catalog-tab", { id: "categories" }); await manage.click("new-category");
  await manage.submit("category-edit", { name: "售后与服务" }, {});
  check("category creation refreshes both catalog administration and employee choices", () => { assert.match(manage.root.innerHTML, /售后与服务/); assert.ok(manage.calls.some(item => item.endpoint === "/categories")); });
  await manage.click("edit-category", { id: "cat_product" });
  await manage.submit("category-edit", { name: "产品知识", active: "on" }, { id: "cat_product", version: "1" });
  check("category rename uses PATCH without rewriting source text or identity", () => {
    const call = manage.calls.find(item => item.endpoint === "/admin/categories/cat_product" && item.body);
    assert.equal(call.method, "PATCH"); assert.equal(call.body.base_version, 1);
    assert.equal(manage.database.sources[0].version_id, "ver_product");
    assert.equal(manage.database.sources[0].sections[0].text, sources[0].sections[0].text);
  });
  await manage.click("open-library"); await manage.change("[data-source-category]", "cat_product");
  check("category and scope filters apply to authorized source rows", () => { assert.match(manage.root.innerHTML, /data-id="product"/); assert.doesNotMatch(manage.root.innerHTML, /data-id="delivery"/); });
  await manage.click("new-source");
  manage.database.categories.find(item => item.id === "cat_product").active = false;
  const beforeRejectedSource = manage.database.sources.length;
  const staleCategoryForm = await manage.submit("source", { title: "保留标题", text: "保留正文", category_id: "cat_product", label: "v1" });
  check("stale category selection is rejected without creating or clearing the form", () => { assert.equal(manage.database.sources.length, beforeRejectedSource); assert.equal(manage.dialog.open, true); assert.match(manage.toast.textContent, /分类已停用/); });
  check("stale category reload requires a fresh choice while retaining title and body", () => { assert.equal(staleCategoryForm.categorySelect.value, ""); assert.doesNotMatch(staleCategoryForm.categorySelect.innerHTML, /value="cat_product"/); assert.equal(staleCategoryForm.values.title, "保留标题"); assert.equal(staleCategoryForm.values.text, "保留正文"); });
  await manage.click("close-dialog"); await manage.click("open-admin"); await manage.click("admin-tab", { id: "accounts" });
  await manage.click("reset-account-password", { id: "u_admin" });
  await manage.submit("account-password", { password, password_confirm: password, confirm: "yes" }, { id: "u_admin", version: "1" });
  check("self password reset removes protected UI and requires a fresh login", () => { assert.match(manage.root.innerHTML, /data-form="login"/); assert.doesNotMatch(manage.root.innerHTML, /更新后的同事|管理后台/); assert.match(manage.toast.textContent, /当前会话已退出/); });
  process.stdout.write(`\n${checks} template/controller checks passed. Mock DOM/API only; no browser or network acceptance.\n`);
}
async function conversationControllerChecks() {
  const app = runtime(); await app.flush(); await app.click("open-chat"); await app.click("new-chat");
  const first = app.database.conversations[0];
  check("new conversation persists a private empty group instead of clearing legacy messages", () => { assert.equal(app.database.conversations.length, 1); assert.match(app.root.innerHTML, /我的会话|历史问答/); assert.equal(app.database.messages.length, 0); });
  await app.submit("ask", { question: "怎么办", scope: "all" });
  check("vague question renders a clarification without answer voting", () => { assert.match(app.root.innerHTML, /待补充 \/ 规则澄清/); assert.match(app.root.innerHTML, /data-mode="reply" aria-pressed="true"/); assert.doesNotMatch(app.root.innerHTML, /data-action="answer-feedback"/); });
  await app.submit("ask", { question: "交付验收需要哪些材料？", scope: "all" });
  check("supplement explicitly targets the latest clarification in the selected group", () => { const call = app.calls.filter(item => item.body && item.endpoint === `/conversations/${first.id}/messages`).at(-1); assert.equal(call.body.reply_to, "m1"); assert.equal(call.body.base_version, 2); assert.match(app.root.innerHTML, /补充信息/); });
  await app.submit("ask", { question: "怎么办", scope: "all" });
  await app.click("chat-mode", { mode: "new" });
  await app.submit("ask", { question: "新的独立问题", scope: "all" });
  check("new-question mode omits previous clarification context", () => { const call = app.calls.filter(item => item.body && item.endpoint.endsWith("/messages")).at(-1); assert.equal(call.body.reply_to, undefined); });
  app.input("[data-chat-input]", "第一会话未发内容");
  await app.click("new-chat"); const second = app.database.conversations[1];
  app.input("[data-chat-input]", "第二会话未发内容");
  await app.click("select-chat", { id: first.id });
  check("switching groups preserves separate unsent text and does not blend history", () => { assert.match(app.root.innerHTML, /第一会话未发内容/); assert.doesNotMatch(app.root.innerHTML, /第二会话未发内容/); });
  await app.click("select-chat", { id: "" });
  check("legacy view excludes grouped messages without migrating old history", () => { assert.doesNotMatch(app.root.innerHTML, /class="chat-message"/); assert.equal(app.database.messages.length, 4); });
  await app.click("select-chat", { id: second.id });
  app.setFailChatJSON(); await app.submit("ask", { question: "一次发送", scope: "all" });
  check("ambiguous send keeps a frozen pending intent and shows explicit retry", () => { assert.equal(app.database.messages.length, 5); assert.match(app.root.innerHTML, /data-action="retry-chat"/); });
  await app.click("ask", { question: "不应抢占原消息" });
  await app.click("retry-chat");
  check("retry reuses one message identity and does not append a duplicate", () => { const calls = app.calls.filter(item => item.body && item.endpoint === `/conversations/${second.id}/messages`); assert.equal(calls.length, 2); assert.equal(calls[0].headers["Idempotency-Key"], calls[1].headers["Idempotency-Key"]); assert.deepEqual(calls[0].body, calls[1].body); assert.equal(app.database.messages.length, 5); assert.doesNotMatch(app.root.innerHTML, /data-action="retry-chat"/); });
  const releaseRead = app.holdChat("read");
  await app.click("select-chat", { id: first.id }); await app.click("select-chat", { id: second.id });
  releaseRead(); await app.flush();
  check("late conversation read cannot replace the more recently selected group", () => { assert.match(app.root.innerHTML, /一次发送/); assert.doesNotMatch(app.root.innerHTML, /第一会话未发内容/); });
  const releaseWrite = app.holdChat("write");
  await app.submit("ask", { question: "迟到的发送", scope: "all" });
  await app.click("select-chat", { id: first.id }); releaseWrite(); await app.flush();
  check("late send completion stays attached to its original group", () => { assert.equal(app.database.messages.at(-1).conversation_id, second.id); assert.match(app.root.innerHTML, /第一会话未发内容/); assert.doesNotMatch(app.root.innerHTML, /<p>迟到的发送<\/p>/); });
  await app.click("rename-chat");
  await app.submit("conversation-edit", { title: "验收咨询" }, { id: first.id, version: String(first.version) });
  check("rename is version-bound PATCH and reads current metadata", () => { const call = app.calls.filter(item => item.method === "PATCH" && item.endpoint === `/conversations/${first.id}`).at(-1); assert.equal(call.body.title, "验收咨询"); assert.match(app.root.innerHTML, /验收咨询/); });
  await app.click("archive-chat");
  check("archive opens a confirmation without writing", () => { assert.equal(first.status, "active"); assert.match(app.dialog.innerHTML, /消息保留且可恢复/); });
  await app.submit("conversation-edit", {}, { id: first.id, version: String(first.version), status: "archived" });
  check("archived conversation is readable but cannot submit another question", () => { assert.equal(first.status, "archived"); assert.doesNotMatch(app.root.innerHTML, /data-form="ask"/); });
  await app.click("archive-chat"); await app.submit("conversation-edit", {}, { id: first.id, version: String(first.version), status: "active" });
  check("explicit restore re-enables the composer without changing message count", () => { assert.equal(first.status, "active"); assert.match(app.root.innerHTML, /data-form="ask"/); assert.equal(app.database.messages.length, 6); });
  first.version++;
  await app.submit("ask", { question: "保留冲突问题", scope: "all" });
  check("stale tab does not overwrite another version and retains its question", () => { assert.equal(app.database.messages.length, 6); assert.match(app.root.innerHTML, /保留冲突问题/); assert.match(app.toast.textContent, /会话已变化/); });
  app.setFailMessages(); await app.click("select-chat", { id: second.id });
  check("failed group read clears cached evidence and blocks new sends", () => { assert.match(app.root.innerHTML, /重新读取/); assert.doesNotMatch(app.root.innerHTML, /class="chat-message"|data-form="ask"/); });
  await app.click("refresh-chat");
  const count = app.database.messages.length;
  for (let i = 3; i <= 25; i++) app.database.messages.push({ id: "page_" + i, conversation_id: second.id, sequence: i, question: "分页问题" + i, matches: [], reply_type: "no_evidence" });
  await app.click("chat-latest");
  check("latest conversation page exposes a bounded earlier cursor", () => { assert.match(app.root.innerHTML, /第 6–25 条/); assert.match(app.root.innerHTML, /data-action="chat-earlier"/); });
  await app.click("chat-earlier");
  check("earlier page is chronological and read-only until returning to latest", () => { assert.match(app.root.innerHTML, /第 1–5 条/); assert.doesNotMatch(app.root.innerHTML, /data-form="ask"/); assert.equal(app.database.messages.length, count + 23); });
  await app.click("chat-latest");
  check("returning to latest restores composer without issuing any mutation", () => assert.match(app.root.innerHTML, /data-form="ask"/));
  const confirmed = runtime(); await confirmed.flush(); await confirmed.click("open-chat"); await confirmed.click("new-chat");
  confirmed.setFailMessages(); await confirmed.submit("ask", { question: "已确认写入但列表读取失败", scope: "all" });
  check("successful send and failed readback do not expose a mutation retry", () => { assert.equal(confirmed.database.messages.length, 1); assert.match(confirmed.root.innerHTML, /重新读取/); assert.doesNotMatch(confirmed.root.innerHTML, /data-action="retry-chat"/); });
  await confirmed.click("refresh-chat");
  check("refresh after acknowledged send performs only a read and recovers the saved turn", () => { assert.equal(confirmed.calls.filter(item => item.body && item.endpoint.endsWith("/messages")).length, 1); assert.match(confirmed.root.innerHTML, /已确认写入但列表读取失败/); });
}
controllerChecks().catch(error => { console.error(error); process.exitCode = 1; });
