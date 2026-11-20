globalThis.WORKBENCH_ACCEPTANCE = (() => {
  function workspace({ state, esc, icon, button, status }) {
    const { projects, project, busy } = state.acceptance;
    const inactive = busy ? "disabled" : "";
    const selected = project?.id || "";
    const heading = `<div class="workspace-title"><button class="back-home" data-action="home">${icon("arrow-left")}工作台</button><h1>项目交付与验收助手</h1><span class="caption">虚构数据</span></div>`;
    const query = `<form class="acceptance-query" data-form="acceptance-check"><label class="form-field">验收项目<select name="project_id" ${inactive}><option value="">请选择项目</option>${projects.map(p => `<option value="${esc(p.id)}" ${selected === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label><button type="submit" class="button primary" ${inactive}>${icon("clipboard-check")}核对材料</button></form>`;
    if (!project) return heading + query + `<p class="caption" role="status">${busy ? "正在读取台账…" : projects.length ? "请选择要核对的项目。" : "没有可访问的验收项目。"}</p>`;
    const summary = project.summary;
    const summaryLabel = summary ? `已到件 ${summary.total - summary.missing}/${summary.total} · 待补 ${summary.missing} 项` : "验收依据不可用，未判定缺项";
    const evidence = project.evidence ? `<details class="acceptance-evidence"><summary>验收依据：${esc(project.evidence.title)} · ${esc(project.evidence.version)}</summary><p><strong>${esc(project.evidence.locator)}</strong></p><p>${esc(project.evidence.text)}</p></details>` : "";
    const tasks = { pending: "待处理", in_progress: "进行中", completed: "已完成", canceled: "已取消" };
    const list = project.items.map(item => {
      const received = !!item.material_version;
      const attributes = `data-id="${esc(item.item_id)}" ${inactive}`;
      const actions = project.can_update && !received
        ? (summary && !item.task_id ? button("预览待办", "list-todo", "acceptance-preview", false, attributes) : "") + button("登记到件", "file-check-2", "acceptance-receive", false, attributes)
        : "";
      return `<li class="acceptance-item"><div><strong>${esc(item.name)}</strong><p class="caption">负责人：${esc(item.assignee_name)} · ${received ? esc(item.material_version) : "未登记版本"}</p></div><div class="acceptance-status">${status(received ? "已到件" : summary ? "待补件" : "待核实")}${item.task_status ? `<span class="caption">待办：${esc(tasks[item.task_status] || item.task_status)}</span>` : ""}</div>${actions ? `<div class="acceptance-actions">${actions}</div>` : ""}</li>`;
    }).join("");
    return heading + query + `<section class="open-section" aria-label="验收材料"><div class="section-heading"><h2>${esc(project.name)}</h2><span class="caption">截止 ${esc(project.due_date)} · 台账 r${esc(project.version)}</span></div><p class="acceptance-summary" role="status">${summaryLabel}</p>${evidence}<ul class="acceptance-list">${list}</ul></section>`;
  }
  return Object.freeze({ workspace });
})();
