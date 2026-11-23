globalThis.WORKBENCH_ACCEPTANCE = (() => {
  function workspace({ state, esc, icon, button, status }) {
    const { projects, project, busy } = state.acceptance;
    const inactive = busy ? "disabled" : "";
    const selected = project?.id || "";
    const heading = `<div class="workspace-title"><button class="back-home" data-action="home">${icon("arrow-left")}工作台</button><h1>项目交付与验收助手</h1><span class="caption">演示环境</span>${button("新建项目", "plus", "acceptance-create", true)}</div>`;
    const query = `<form class="acceptance-query" data-form="acceptance-check"><label class="form-field">验收项目<select name="project_id" ${inactive}><option value="">请选择项目</option>${projects.map(p => `<option value="${esc(p.id)}" ${selected === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label><button type="submit" class="button primary" ${inactive}>${icon("clipboard-check")}核对材料</button></form>`;
    if (!project) return heading + query + `<p class="caption" role="status">${busy ? "正在读取台账…" : projects.length ? "请选择要核对的项目。" : "没有可访问的验收项目。"}</p>`;
    const summary = project.summary;
    const summaryLabel = summary ? `已到件 ${summary.total - summary.missing}/${summary.total} · 待补 ${summary.missing} 项` : "验收依据不可用，未判定缺项";
    const evidence = project.evidence ? `<details class="acceptance-evidence"><summary>验收依据：${esc(project.evidence.title)} · ${esc(project.evidence.version)}</summary><p><strong>${esc(project.evidence.locator)}</strong></p><p>${esc(project.evidence.text)}</p></details>` : "";
    const review = project.review;
    const reviewLabels = { pending: "待审核", approved: "审核通过", rejected: "已退回" };
    const reviewText = review ? `${reviewLabels[review.status] || "未知状态"}${review.stale ? "（依据已变化，须重新提交）" : ""}` : "尚未提交审核";
    const reviewActions = (project.can_update && summary && summary.missing === 0 && (!review || review.stale || review.status === "rejected") ? button("提交验收审核", "send", "acceptance-review-submit", true, inactive) : "") + (review?.can_decide ? button("审核决定", "clipboard-check", "acceptance-review-decide", true, inactive) : "") + (review?.status === "approved" && !review.stale ? button("下载审核记录", "download", "acceptance-review-report", false, inactive) : "");
    const reviewPanel = `<div class="acceptance-review"><p><strong>验收审核：</strong>${esc(reviewText)}</p>${review?.comment ? `<p>审核意见：${esc(review.comment)}</p>` : ""}${reviewActions}<p class="caption">演示审核不替代客户签署或真实验收。</p></div>`;
    const history = project.review_history || [];
    const historyPanel = history.length ? `<details class="acceptance-evidence"><summary>审核历史（最近 ${history.length} 条）</summary><ol>${history.map(entry => `<li><p><strong>${esc(reviewLabels[entry.status] || "未知状态")}</strong> · 台账 r${esc(entry.project_version)} · ${esc(entry.submitted_by)} 提交于 ${esc(entry.submitted_at)}</p>${entry.decided_at ? `<p>${esc(entry.decided_by)} 审核于 ${esc(entry.decided_at)}${entry.comment ? `：${esc(entry.comment)}` : ""}</p>` : ""}${entry.materials ? `<ul>${entry.materials.map(material => `<li>${esc(material.name)} · ${esc(material.version)} · ${material.linked ? "已关联资料" : "手工登记"}</li>`).join("")}</ul>` : '<p class="caption">旧记录未保存材料清单。</p>'}</li>`).join("")}</ol><p class="caption">历史记录不代表当前版本有效，请以当前审核状态为准。</p></details>` : "";
    const tasks = { pending: "待处理", in_progress: "进行中", completed: "已完成", canceled: "已取消" };
    const list = project.items.map(item => {
      const received = !!item.material_version;
      const attributes = `data-id="${esc(item.item_id)}" ${inactive}`;
      const actions = project.can_update && !received
        ? (summary && (!item.task_id || ["canceled", "completed"].includes(item.task_status)) ? button("预览待办", "list-todo", "acceptance-preview", false, attributes) + button("修改负责人", "user-round", "acceptance-reassign", false, attributes) : "") + button("登记到件", "file-check-2", "acceptance-receive", false, attributes)
        : project.can_update && received ? button("更新关联", "link", "acceptance-relink", false, attributes) : "";
      const ref = item.material_reference;
      const reference = ref?.state === "available" ? `<button class="source-link" data-action="source" data-id="${esc(ref.source_id)}">关联资料：${esc(ref.title)} / ${esc(ref.version)}</button>` : ref?.state === "unavailable" ? '<p class="caption">关联资料已失效或无权访问</p>' : "";
      return `<li class="acceptance-item"><div><strong>${esc(item.name)}</strong><p class="caption">负责人：${esc(item.assignee_name)} · ${received ? esc(item.material_version) : "未登记版本"}</p>${reference}</div><div class="acceptance-status">${status(received ? "已到件" : summary ? "待补件" : "待核实")}${item.task_status ? `<span class="caption">待办：${esc(tasks[item.task_status] || item.task_status)}</span>` : ""}</div>${actions ? `<div class="acceptance-actions">${actions}</div>` : ""}</li>`;
    }).join("");
    return heading + query + `<section class="open-section" aria-label="验收材料"><div class="section-heading"><h2>${esc(project.name)}</h2><span class="caption">截止 ${esc(project.due_date)} · 台账 r${esc(project.version)}</span></div><p class="acceptance-summary" role="status">${summaryLabel}</p>${evidence}<ul class="acceptance-list">${list}</ul>${reviewPanel}${historyPanel}</section>`;
  }
  return Object.freeze({ workspace });
})();
