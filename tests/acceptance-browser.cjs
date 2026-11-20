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
    await item.getByRole("button", { name: "预览待办" }).click();
    const modal = desktop.getByRole("dialog");
    await modal.getByText("补件：功能验收清单", { exact: true }).waitFor();
    await modal.getByRole("button", { name: "确认创建" }).click();
    await item.getByText("待办：待处理", { exact: true }).waitFor();
    await item.getByRole("button", { name: "登记到件" }).click();
    await modal.getByRole("textbox", { name: "材料版本" }).fill("v1.0");
    await modal.getByRole("button", { name: "保存并回读" }).click();
    await desktop.getByText("已到件 3/4 · 待补 1 项", { exact: true }).waitFor();
    await screenshot(desktop, "acceptance-desktop.png");

    const mobile = await open("reviewer", { width: 390, height: 844 });
    await check(mobile);
    await mobile.getByText("已到件 3/4 · 待补 1 项", { exact: true }).waitFor();
    assert.equal(await mobile.getByRole("button", { name: "登记到件" }).count(), 0);
    await screenshot(mobile, "acceptance-mobile.png");
    assert.deepEqual(issues, []);
    console.log("Browser passed: project clarification, source citation, preview, confirmation, receipt/readback, reviewer, 1280/390px.");
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
