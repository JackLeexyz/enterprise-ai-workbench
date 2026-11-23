const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright");

async function main() {
  const [origin, credentialsFile] = process.argv.slice(2);
  assert.equal(new URL(origin).hostname, "127.0.0.1");
  const { accounts } = JSON.parse(fs.readFileSync(credentialsFile, "utf8"));
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.goto(origin);
    await page.locator('input[name="username"]').fill("writer");
    await page.locator('input[name="password"]').fill(accounts.find(a => a.username === "writer").password);
    await page.locator('form[data-form="login"] button[type="submit"]').click();
    await page.locator('[data-action="open-chat"]').first().click();
    await page.locator('[data-action="new-chat"]').first().click();
    await page.locator('.conversation-item.is-current[data-id^="conv_"]').waitFor();
    await page.getByLabel("向助手提问").fill("怎么申请一台电脑");
    await page.getByRole("button", { name: "发送问题" }).click();
    await page.getByRole("button", { name: /员工设备申领制度/ }).first().waitFor();
    await page.getByText(/具体库存与领取时间由行政人员确认/).first().waitFor();
    await page.getByLabel("向助手提问").fill("设备库存还有多少台");
    await page.getByRole("button", { name: "发送问题" }).click();
    await page.getByText("设备库存还有多少台", { exact: true }).waitFor();
    const answer = page.locator(".chat-message").last();
    await answer.getByText(/具体库存与领取时间由行政人员确认/).waitFor();
    assert.equal(await answer.getByText(/不保证包含问题的具体答案/).count(), 1);
    console.log("PASS: constrained alias retrieves cited source and inventory caveat remains visible");
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
