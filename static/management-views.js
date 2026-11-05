globalThis.WORKBENCH_MANAGEMENT_VIEWS = (() => {
  const roleName = role => role === "admin" ? "管理员" : "员工";
  function categoryOptions(ui, placeholder = false, selected = "") {
    const active = (ui.state.categories || []).filter(item => item.active);
    const unavailable = selected && !active.some(item => item.id === selected);
    return `${unavailable ? '<option value="" selected disabled>原分类已停用，请重新选择</option>' : placeholder ? '<option value="">请选择有效分类</option>' : ""}${active.map(item => `<option value="${ui.esc(item.id)}" ${selected === item.id ? "selected" : ""}>${ui.esc(item.name)}</option>`).join("")}`;
  }
  function categoryFilter(ui) {
    const categories = new Map((ui.state.categories || []).map(item => [item.id, item.name]));
    for (const item of ui.sources) if (item.category_id) categories.set(item.category_id, item.category);
    return `<select class="select-field" data-source-category aria-label="资料分类"><option value="all">全部分类</option>${[...categories].map(([id, name]) => `<option value="${ui.esc(id)}" ${ui.state.sourceCategory === id ? "selected" : ""}>${ui.esc(name)}</option>`).join("")}</select>`;
  }
  function catalogTabs(ui) {
    return `<div class="catalog-tabs" role="tablist" aria-label="模板与分类">${[["templates", "写作模板"], ["categories", "资料分类"]].map(([id, name]) => `<button role="tab" data-action="catalog-tab" data-id="${id}" aria-selected="${(ui.state.catalogTab || "templates") === id}">${name}</button>`).join("")}</div>`;
  }
  function accounts(ui) {
    if (ui.state.adminUsersError) return `<section class="admin-section"><h2>团队账号</h2><p class="notice warning" role="alert">账号列表暂未更新：${ui.esc(ui.state.adminUsersError)}</p>${ui.button("重新读取账号", "refresh-cw", "refresh-admin-users", true)}</section>`;
    const all = ui.state.adminUsers || [];
    const filter = ui.state.accountFilter || "all";
    return `<section class="admin-section"><div class="section-heading"><h2>团队账号<span class="count">${all.length}</span></h2><div class="section-actions">${ui.iconButton("refresh-cw", "刷新账号", "refresh-admin-users")}${ui.button("新建账号", "user-plus", "new-account", true)}</div></div><div class="management-filter"><label class="search-field">${ui.icon("search")}<input type="search" data-account-query aria-label="搜索账号或姓名" placeholder="搜索姓名或账号" value="${ui.esc(ui.state.accountQuery || "")}"></label><select class="select-field" data-account-filter aria-label="账号状态">${[["all", "全部状态"], ["active", "已启用"], ["inactive", "已停用"]].map(([id, name]) => `<option value="${id}" ${filter === id ? "selected" : ""}>${name}</option>`).join("")}</select></div><div class="account-table" role="table" aria-label="团队账号" data-account-table>${accountRows(ui)}</div></section>`;
  }
  function accountRows(ui) {
    const query = (ui.state.accountQuery || "").toLocaleLowerCase();
    const filter = ui.state.accountFilter || "all";
    const rows = (ui.state.adminUsers || []).filter(item => (!query || (item.name + " " + item.username).toLocaleLowerCase().includes(query)) && (filter === "all" || item.active === (filter === "active")));
    return `<div class="account-table-head" role="row"><span role="columnheader">姓名</span><span role="columnheader">账号</span><span role="columnheader">类型</span><span role="columnheader">状态</span><span role="columnheader">操作</span></div>${rows.map(item => `<div class="account-row" role="row"><span role="cell" data-label="姓名"><strong>${ui.esc(item.name)}</strong>${item.id === ui.state.user.id ? '<small>当前账号</small>' : ""}</span><span role="cell" data-label="账号">${ui.esc(item.username)}</span><span role="cell" data-label="类型">${roleName(item.role)}</span><span role="cell" data-label="状态">${ui.status(item.active ? "已启用" : "已停用")}</span><span class="management-actions" role="cell" data-label="操作">${ui.iconButton("sliders-horizontal", "修改姓名、类型与状态", "edit-account", `data-id="${ui.esc(item.id)}"`)}${ui.iconButton("key-round", "重置密码", "reset-account-password", `data-id="${ui.esc(item.id)}"`)}</span></div>`).join("") || '<p class="empty">没有匹配的账号</p>'}`;
  }
  function accountForm(ui, item = null) {
    const creating = !item;
    const last = item?.active && item.role === "admin" && (ui.state.adminUsers || []).filter(user => user.active && user.role === "admin").length === 1;
    return `<form id="account-edit-form" data-form="account-edit" data-id="${ui.esc(item?.id || "")}" data-version="${item?.version || ""}"><label class="form-field">姓名<input name="name" maxlength="60" value="${ui.esc(item?.name || "")}" required autocomplete="off"></label><div class="field-pair"><label class="form-field">账号<input name="username" maxlength="40" minlength="3" pattern="[a-z][a-z0-9_.\\-]{2,39}" value="${ui.esc(item?.username || "")}" ${creating ? "required" : "readonly"} autocomplete="off"></label><label class="form-field">账号类型<select name="role"><option value="employee" ${item?.role !== "admin" ? "selected" : ""}>员工</option><option value="admin" ${item?.role === "admin" ? "selected" : ""}>管理员</option></select></label></div>${creating ? passwordInputs(ui) : `<label class="template-check account-active"><input type="checkbox" name="active" ${item.active ? "checked" : ""}>启用账号</label><p class="notice warning">停用或更改类型会退出该账号的所有会话。停用、降为员工会撤销待处理资料发布的临时审阅权，资料和历史任务保留。</p>${last ? '<p class="notice">当前为最后一个启用的管理员，不能停用或降级。</p>' : ""}<label class="delete-confirm-check"><input type="checkbox" name="confirm" value="yes" required>确认修改此账号的状态与权限</label>`}</form>`;
  }
  function passwordInputs(ui) {
    return `<label class="form-field">新密码（20 至 200 字符）<input name="password" type="password" minlength="20" maxlength="200" required autocomplete="new-password"></label><label class="form-field">再次输入新密码<input name="password_confirm" type="password" minlength="20" maxlength="200" required autocomplete="new-password"></label>`;
  }
  function passwordForm(ui, item) {
    return `<form id="account-password-form" data-form="account-password" data-id="${ui.esc(item.id)}" data-version="${item.version}"><dl class="task-details"><dt>账号</dt><dd>${ui.esc(item.username)}</dd><dt>姓名</dt><dd>${ui.esc(item.name)}</dd></dl>${passwordInputs(ui)}<p class="notice warning">重置后旧密码失效，全部会话退出。新密码不回显、不自动发送。</p><label class="delete-confirm-check"><input type="checkbox" name="confirm" value="yes" required>确认重置此账号的密码</label></form>`;
  }
  function categories(ui) {
    if (ui.state.adminCategoriesError) return `<section class="admin-section"><h2>资料分类</h2><p class="notice warning" role="alert">分类列表暂未更新：${ui.esc(ui.state.adminCategoriesError)}</p>${ui.button("重新读取分类", "refresh-cw", "refresh-admin-categories", true)}</section>`;
    const rows = ui.state.adminCategories || [];
    return `<section class="admin-section"><div class="section-heading"><h2>资料分类<span class="count">${rows.length}</span></h2><div class="section-actions">${ui.iconButton("refresh-cw", "刷新分类", "refresh-admin-categories")}${ui.button("新增分类", "folder-plus", "new-category", true)}</div></div><div class="category-list">${rows.map(item => `<div class="category-row"><span class="category-name">${ui.icon("folder")}<strong>${ui.esc(item.name)}</strong>${item.id === "cat_other" ? "<small>默认分类</small>" : ""}</span><span class="caption">v${item.version}</span>${ui.status(item.active ? "已启用" : "已停用")}${ui.iconButton("pencil", item.id === "cat_other" ? "默认分类不可改名或停用" : "修改分类", "edit-category", `data-id="${ui.esc(item.id)}" ${item.id === "cat_other" ? "disabled" : ""}`)}</div>`).join("") || '<p class="empty">暂无分类</p>'}</div></section>`;
  }
  function categoryForm(ui, item = null) {
    return `<form id="category-edit-form" data-form="category-edit" data-id="${ui.esc(item?.id || "")}" data-version="${item?.version || ""}"><label class="form-field">分类名称<input name="name" maxlength="30" value="${ui.esc(item?.name || "")}" required autocomplete="off"></label>${item ? `<label class="template-check account-active"><input type="checkbox" name="active" ${item.active ? "checked" : ""}>启用分类</label><p class="notice">停用后不能用于新资料，现有资料和引用保留。改名不更改正文或来源版本。</p>` : ""}</form>`;
  }
  return Object.freeze({ categoryOptions, categoryFilter, catalogTabs, accounts, accountRows, accountForm, passwordForm, categories, categoryForm });
})();
