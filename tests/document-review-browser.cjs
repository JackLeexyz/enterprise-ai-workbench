const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright");

async function main() {
  const [origin, credentialsFile, outputFile] = process.argv.slice(2);
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
    await writer.locator('[data-action="open-documents"]').first().click();
    await writer.locator('[data-action="new-document"]').first().click();
    let dialog = writer.getByRole("dialog");
    await dialog.getByLabel("文档标题").fill("虚构项目交付方案");
    await dialog.locator('input[name="field_subject"]').fill("虚构项目");
    await dialog.locator('textarea[name="field_objective"]').fill("完成知识资料整理与交付验收");
    await dialog.getByRole("button", { name: "创建草稿" }).click();
    await writer.locator('[data-action="submit-review"]').waitFor();
    await writer.locator('[data-action="submit-review"]').click();
    await dialog.getByLabel("审核同事").selectOption("u_reviewer");
    await dialog.getByRole("button", { name: "提交审核", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    const reviewer = await login("reviewer", { width: 390, height: 844 });
    await reviewer.getByRole("button", { name: "查看待办" }).click();
    await reviewer.getByRole("heading", { name: "我的待办", exact: true }).waitFor();
    await reviewer.locator(".task-list-grid").getByRole("button", { name: /虚构项目交付方案/ }).click();
    await reviewer.locator('[data-action="review-decision"]').click();
    dialog = reviewer.getByRole("dialog");
    await dialog.getByLabel("审核结果").selectOption("approved");
    await dialog.getByLabel("审核意见").fill("已核对虚构资料");
    await dialog.getByRole("button", { name: "确认审核结果" }).click();
    await dialog.waitFor({ state: "hidden" });
    assert.equal(await reviewer.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await writer.locator('[data-action="refresh-document"]').click();
    const exportButton = writer.locator('[data-action="export-document"]');
    await exportButton.waitFor({ state: "visible" });
    await writer.waitForFunction(() => !document.querySelector('[data-action="export-document"]').disabled);
    const downloadEvent = writer.waitForEvent("download");
    await exportButton.click();
    const download = await downloadEvent;
    assert.match(download.suggestedFilename(), /\.docx$/);
    await download.saveAs(outputFile);
    const admin = await login("admin", { width: 1280, height: 900 });
    await admin.locator('[data-action="open-documents"]').first().waitFor();
    const withdrawn = await admin.evaluate(async () => {
      const session = (await (await fetch("/api/workbench/session")).json()).data;
      const source = (await (await fetch("/api/workbench/sources/delivery")).json()).data;
      return (await fetch("/api/workbench/sources/delivery/withdraw", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf, "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ base_version: source.version })
      })).status;
    });
    assert.equal(withdrawn, 200);
    await writer.locator('[data-action="refresh-document"]').click();
    await writer.waitForFunction(() => document.querySelector('[data-action="export-document"]').disabled);
    assert.deepEqual(issues, []);
    console.log("PASS: fictional source-backed document creation, reviewer approval and DOCX download");
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
