const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright");

async function main() {
  const [origin, credentialsFile] = process.argv.slice(2);
  assert.equal(new URL(origin).hostname, "127.0.0.1");
  const { accounts } = JSON.parse(fs.readFileSync(credentialsFile, "utf8"));
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const issues = [];
  async function login(username, viewport) {
    const page = await browser.newPage({ viewport });
    page.on("pageerror", error => issues.push(error.message));
    await page.goto(origin);
    await page.locator('input[name="username"]').fill(username);
    await page.locator('input[name="password"]').fill(accounts.find(a => a.username === username).password);
    await page.locator('form[data-form="login"] button[type="submit"]').click();
    return page;
  }
  try {
    const writer = await login("writer", { width: 1280, height: 900 });
    await writer.locator('[data-action="open-chat"]').first().click();
    await writer.route("**/api/workbench/conversations", async route => {
      if (route.request().method() === "POST") await new Promise(resolve => setTimeout(resolve, 400));
      await route.continue();
    });
    await writer.locator('[data-action="new-chat"]').first().click();
    await writer.getByText("正在创建会话，请稍候。", { exact: true }).waitFor();
    await writer.locator('.conversation-item.is-current[data-id^="conv_"]').waitFor();
    await writer.unroute("**/api/workbench/conversations");
    await writer.locator('[data-action="new-task"]').last().click();
    let dialog = writer.getByRole("dialog");
    await dialog.getByLabel("任务标题").fill("核对虚构验收材料");
    await dialog.getByLabel("负责人").selectOption("u_reviewer");
    await dialog.getByRole("button", { name: "预览任务" }).click();
    await dialog.getByRole("button", { name: "确认创建" }).click();
    await dialog.getByRole("heading", { name: "核对虚构验收材料" }).waitFor();
    await dialog.getByRole("button", { name: "关闭", exact: true }).first().click();
    await writer.getByLabel("会话关联待办").getByText(/核对虚构验收材料.*待处理/).waitFor();
    const reviewer = await login("reviewer", { width: 390, height: 844 });
    await reviewer.getByRole("button", { name: "查看待办" }).click();
    await reviewer.getByRole("button", { name: /核对虚构验收材料/ }).click();
    dialog = reviewer.getByRole("dialog");
    await dialog.getByRole("button", { name: "标记完成" }).click();
    await dialog.getByLabel("处理结果（可选）").fill("已核对虚构材料并记录结果");
    let dropped = false;
    await reviewer.route("**/api/workbench/tasks/*/status", async route => {
      if (!dropped) { dropped = true; await route.fetch(); await route.abort("failed"); }
      else await route.continue();
    });
    await dialog.getByRole("button", { name: "确认完成" }).click();
    await dialog.locator("fieldset:disabled").waitFor();
    await dialog.getByRole("button", { name: "确认完成" }).click();
    await reviewer.unroute("**/api/workbench/tasks/*/status");
    await writer.locator('[data-action="refresh-chat"]').first().click();
    await writer.getByLabel("会话关联待办").getByText(/核对虚构验收材料.*已完成.*已核对虚构材料并记录结果/).waitFor();
    assert.equal(await reviewer.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(issues, []);
    console.log("PASS: conversation-linked task, private roles, completion result and creator readback");
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
