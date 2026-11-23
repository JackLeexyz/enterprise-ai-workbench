// Run against a freshly initialized, isolated acceptance demo.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

async function run() {
  const [origin, credentialsFile, output] = process.argv.slice(2);
  assert.equal(new URL(origin).hostname, "127.0.0.1", "Use a local instance or a loopback SSH tunnel.");
  const { accounts } = JSON.parse(fs.readFileSync(credentialsFile, "utf8"));
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const issues = [];
  async function open(username, viewport) {
    const page = await browser.newPage({ viewport });
    page.on("pageerror", error => issues.push(error.message));
    await page.goto(origin);
    await page.locator('input[name="username"]').fill(username);
    await page.locator('input[name="password"]').fill(accounts.find(a => a.username === username).password);
    await page.locator('form[data-form="login"] button[type="submit"]').click();
    await page.getByRole("button", { name: "项目验收助手", exact: true }).click();
    await page.getByRole("heading", { name: "项目交付与验收助手" }).waitFor();
    return page;
  }
  async function check(page) {
    await page.locator('select[name="project_id"]').selectOption("demo-a");
    await page.getByRole("button", { name: "核对材料", exact: true }).click();
  }
  async function screenshot(page, filename) {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, filename + " horizontal overflow");
    if (output) await page.screenshot({ path: path.join(output, filename), fullPage: true });
  }
  try {
    const desktop = await open("writer", { width: 1280, height: 900 });
    await desktop.getByRole("button", { name: "核对材料", exact: true }).click();
    await desktop.getByText("请先选择要核对的项目。", { exact: true }).waitFor();
    await check(desktop);
    await desktop.getByText("已到件 2/4 · 待补 2 项", { exact: true }).waitFor();
    await desktop.locator(".acceptance-evidence summary").click();
    await desktop.getByText(/项目负责人应准备实施说明/).waitFor();
    const item = desktop.locator(".acceptance-item").filter({ hasText: "功能验收清单" });
    await item.getByRole("button", { name: "修改负责人" }).click();
    await desktop.getByRole("dialog").locator('select[name="assignee_id"]').selectOption("u_writer");
    let dropped = false;
    await desktop.route("**/acceptance/projects/demo-a/items/checklist/reassign", async route => {
      if (!dropped) { dropped = true; await route.fetch(); await route.abort("failed"); }
      else await route.continue();
    });
    await desktop.getByRole("dialog").getByRole("button", { name: "保存并回读" }).click();
    await desktop.getByRole("dialog").locator('select[name="assignee_id"]:disabled').waitFor();
    await desktop.getByRole("dialog").getByRole("button", { name: "保存并回读" }).click();
    await desktop.unroute("**/acceptance/projects/demo-a/items/checklist/reassign");
    await item.getByText(/负责人：.*未登记版本/).waitFor();
    await item.getByRole("button", { name: "修改负责人" }).click();
    await desktop.getByRole("dialog").locator('select[name="assignee_id"]').selectOption("u_reviewer");
    await desktop.getByRole("dialog").getByRole("button", { name: "保存并回读" }).click();
    await item.getByRole("button", { name: "预览待办" }).click();
    const modal = desktop.getByRole("dialog");
    await modal.getByText("补件：功能验收清单", { exact: true }).waitFor();
    await modal.getByRole("button", { name: "确认创建" }).click();
    await item.getByText("待办：待处理", { exact: true }).waitFor();
    await desktop.getByRole("button", { name: /我的待办/ }).first().click();
    await desktop.getByRole("button", { name: /补件：功能验收清单/ }).first().click();
    await desktop.getByRole("dialog").getByRole("button", { name: "取消任务" }).click();
    await desktop.getByRole("button", { name: "工作台", exact: true }).first().click();
    await desktop.getByRole("button", { name: "项目验收助手", exact: true }).click();
    await check(desktop);
    await item.getByText("待办：已取消", { exact: true }).waitFor();
    await item.getByRole("button", { name: "预览待办" }).click();
    await modal.getByRole("button", { name: "确认创建" }).click();
    await item.getByText("待办：待处理", { exact: true }).waitFor();
    const assignee = await open("reviewer", { width: 1280, height: 900 });
    await assignee.getByRole("button", { name: /我的待办/ }).first().click();
    await assignee.getByRole("button", { name: /补件：功能验收清单/ }).filter({ hasText: "待处理" }).first().click();
    await assignee.getByRole("dialog").getByRole("button", { name: "标记完成" }).click();
    await assignee.getByRole("dialog").getByRole("button", { name: "确认完成" }).click();
    await assignee.getByRole("button", { name: "工作台", exact: true }).first().click();
    await assignee.getByRole("button", { name: "项目验收助手", exact: true }).click();
    await check(assignee);
    await assignee.getByText("待办：已完成", { exact: true }).waitFor();
    await assignee.getByText("已到件 2/4 · 待补 2 项", { exact: true }).waitFor();
    await check(desktop);
    await item.getByText("待办：已完成", { exact: true }).waitFor();
    await item.getByRole("button", { name: "预览待办" }).click();
    await modal.getByRole("button", { name: "确认创建" }).click();
    await item.getByText("待办：待处理", { exact: true }).waitFor();
    await item.getByRole("button", { name: "登记到件" }).click();
    await modal.getByRole("textbox", { name: "材料版本" }).fill("v1.0");
    await modal.locator('select[name="material_source_id"]').selectOption("delivery");
    await modal.getByRole("button", { name: "保存并回读" }).click();
    await desktop.getByText("已到件 3/4 · 待补 1 项", { exact: true }).waitFor();
    await item.getByRole("button", { name: /关联资料/ }).waitFor();
    await item.getByRole("button", { name: /关联资料/ }).click();
    await desktop.getByRole("dialog").getByText(/项目负责人应准备实施说明/).waitFor();
    await desktop.getByRole("dialog").getByRole("button", { name: "关闭", exact: true }).first().click();
    await screenshot(desktop, "acceptance-desktop.png");

    const mobile = await open("reviewer", { width: 390, height: 844 });
    await check(mobile);
    await mobile.getByText("已到件 3/4 · 待补 1 项", { exact: true }).waitFor();
    assert.equal(await mobile.getByRole("button", { name: "登记到件" }).count(), 0);
    await screenshot(mobile, "acceptance-mobile.png");
    const admin = await browser.newPage();
    await admin.goto(origin);
    await admin.locator('input[name="username"]').fill("admin");
    await admin.locator('input[name="password"]').fill(accounts.find(a => a.username === "admin").password);
    const loginResponse = admin.waitForResponse(response => response.url().endsWith("/api/workbench/session") && response.request().method() === "POST");
    await admin.locator('form[data-form="login"] button[type="submit"]').click();
    const login = (await (await loginResponse).json()).data;
    const withdrawn = await admin.evaluate(async csrf => {
      const source = await (await fetch("/api/workbench/sources/delivery")).json();
      const response = await fetch("/api/workbench/sources/delivery/withdraw", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ base_version: source.data.version })
      });
      return response.status;
    }, login.csrf);
    assert.equal(withdrawn, 200);
    await check(desktop);
    await item.getByText("关联资料已失效或无权访问", { exact: true }).waitFor();
    assert.equal(await item.getByRole("button", { name: /关联资料/ }).count(), 0);
    await item.getByRole("button", { name: "更新关联" }).click();
    await desktop.getByRole("dialog").locator('select[name="material_source_id"]').selectOption("product");
    await desktop.getByRole("dialog").getByRole("button", { name: "保存并回读" }).click();
    await item.getByRole("button", { name: /关联资料/ }).waitFor();
    await desktop.getByRole("button", { name: "新建项目" }).click();
    const creation = desktop.getByRole("dialog");
    await creation.getByLabel("项目名称").fill("虚构新项目");
    await creation.getByLabel("截止日期").fill("2026-12-30");
    await creation.getByLabel("审核人").selectOption("u_reviewer");
    await creation.getByLabel("验收依据").selectOption("product");
    await creation.getByLabel("材料清单（每行一项，最多20项）").fill("施工说明\n验收记录");
    let createDropped = false;
    await desktop.route("**/api/workbench/acceptance/projects", async route => {
      if (route.request().method() === "POST" && !createDropped) { createDropped = true; await route.fetch(); await route.abort("failed"); }
      else await route.continue();
    });
    await creation.getByRole("button", { name: "创建项目" }).click();
    await creation.locator("fieldset:disabled").waitFor();
    await creation.getByRole("button", { name: "创建项目" }).click();
    await desktop.unroute("**/api/workbench/acceptance/projects");
    await desktop.getByRole("heading", { name: "虚构新项目" }).waitFor();
    await desktop.getByText("已到件 0/2 · 待补 2 项", { exact: true }).waitFor();
    const newItem = desktop.locator(".acceptance-item").filter({ hasText: "施工说明" });
    await newItem.getByRole("button", { name: "预览待办" }).click();
    await desktop.getByRole("dialog").getByRole("button", { name: "确认创建" }).click();
    await newItem.getByText("待办：待处理", { exact: true }).waitFor();
    await assignee.getByRole("button", { name: /我的待办/ }).first().click();
    await assignee.getByRole("button", { name: /补件：施工说明/ }).filter({ hasText: "待处理" }).click();
    await assignee.getByRole("dialog").getByRole("button", { name: "标记完成" }).click();
    await assignee.getByRole("dialog").getByRole("button", { name: "确认完成" }).click();
    await desktop.locator('form[data-form="acceptance-check"] button[type="submit"]').click();
    await newItem.getByText("待办：已完成", { exact: true }).waitFor();
    await newItem.getByRole("button", { name: "登记到件" }).click();
    await desktop.getByRole("dialog").getByRole("textbox", { name: "材料版本" }).fill("v1.0");
    await desktop.getByRole("dialog").locator('select[name="material_source_id"]').selectOption("product");
    await desktop.getByRole("dialog").getByRole("button", { name: "保存并回读" }).click();
    await desktop.getByText("已到件 1/2 · 待补 1 项", { exact: true }).waitFor();
    await newItem.getByRole("button", { name: /关联资料/ }).waitFor();
    const secondItem = desktop.locator(".acceptance-item").filter({ hasText: "验收记录" });
    await secondItem.getByRole("button", { name: "登记到件" }).click();
    await desktop.getByRole("dialog").getByRole("textbox", { name: "材料版本" }).fill("v1.0");
    await desktop.getByRole("dialog").getByRole("button", { name: "保存并回读" }).click();
    await desktop.getByRole("button", { name: "提交验收审核" }).click();
    await desktop.getByRole("dialog").getByRole("button", { name: "确认提交" }).click();
    await desktop.locator(".acceptance-review").getByText("待审核", { exact: false }).waitFor();
    await mobile.reload();
    await mobile.getByRole("button", { name: "项目验收助手", exact: true }).click();
    await mobile.locator('select[name="project_id"]').selectOption({ label: "虚构新项目" });
    await mobile.getByRole("button", { name: "核对材料", exact: true }).click();
    await mobile.getByRole("button", { name: "审核决定" }).click();
    await mobile.getByRole("dialog").getByLabel("审核决定").selectOption("approved");
    await mobile.getByRole("dialog").getByRole("button", { name: "确认决定" }).click();
    await mobile.locator(".acceptance-review").getByText("审核通过", { exact: false }).waitFor();
    await desktop.locator('form[data-form="acceptance-check"] button[type="submit"]').click();
    await desktop.locator(".acceptance-review").getByText("审核通过", { exact: false }).waitFor();
    const downloadEvent = desktop.waitForEvent("download");
    await desktop.getByRole("button", { name: "下载审核记录" }).click();
    const download = await downloadEvent;
    assert.equal(download.suggestedFilename(), "验收审核记录.txt");
    const reportText = fs.readFileSync(await download.path(), "utf8");
    assert.match(reportText, /虚构新项目/);
    assert.match(reportText, /不代替客户签署或真实验收/);
    await desktop.getByText(/审核历史（最近 1 条）/).click();
    await desktop.getByText("验收记录 · v1.0 · 手工登记", { exact: true }).waitFor();
    await screenshot(mobile, "acceptance-review-mobile.png");
    assert.deepEqual(issues, []);
    console.log("Browser passed: project clarification, source citation, preview, confirmation, receipt/readback, reviewer, 1280/390px.");
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
