"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const path = require("node:path");
const context = vm.createContext({ setTimeout, clearTimeout });
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/autosave.js"), "utf8"), context);
const tick = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const ack = (payload, id = "a") => ({ id, base_revision: payload.base_revision, draft_version: payload.base_draft_version + 1 });
let passed = 0;
async function check(name, work) { await work(); passed++; console.log("PASS " + name); }
function setup(write) {
  const calls = [], states = [], timers = new Map(); let sequence = 0;
  const queue = context.WORKBENCH_AUTOSAVE.create({ write: async (id, payload, key) => { calls.push({ id, payload, key }); return write ? write(id, payload, key) : ack(payload, id); }, onState: value => states.push({ ...value }), makeKey: () => "key_" + (++sequence), schedule: fn => { const id = ++sequence; timers.set(id, fn); return id; }, cancel: id => timers.delete(id) });
  queue.reset({ id: "a", baseRevision: 1, version: 0, snapshot: { title: "原稿" } });
  return { queue, calls, states, timers, fire: async () => { const entries = [...timers.values()]; timers.clear(); entries.forEach(fn => fn()); await tick(); } };
}
async function main() {
  await check("debounce keeps latest snapshot and has no revision writes", async () => {
    const h = setup(); h.queue.change({ title: "甲" }); h.queue.change({ title: "乙" });
    assert.equal(h.timers.size, 1); await h.fire();
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].payload.title, "乙"); assert.equal(h.queue.state().version, 1); assert.equal(h.queue.safe(), true);
  });
  await check("in-flight edits are serialized with the acknowledged draft version", async () => {
    const wait = deferred(); const h = setup((id, data) => h.calls.length === 1 ? wait.promise : ack(data, id));
    h.queue.change({ title: "甲" }); const saving = h.queue.flush(); await tick(); h.queue.change({ title: "乙" }); await h.fire();
    assert.equal(h.calls.length, 1); wait.resolve(ack(h.calls[0].payload)); await saving;
    assert.equal(h.calls.length, 2); assert.equal(h.calls[1].payload.title, "乙"); assert.equal(h.calls[1].payload.base_draft_version, 1); assert.equal(h.queue.safe(), true);
  });
  await check("a lost response retries identical intent before the newer content", async () => {
    let fail = true; const h = setup((id, data) => { if (fail) { fail = false; throw new Error("offline after write"); } return ack(data, id); });
    h.queue.change({ title: "甲" }); await assert.rejects(h.queue.flush()); h.queue.change({ title: "乙" });
    assert.equal(h.timers.size, 0); await h.queue.flush({ retry: true });
    assert.equal(h.calls.length, 3); assert.equal(h.calls[0].key, h.calls[1].key); assert.equal(h.calls[1].payload.title, "甲"); assert.equal(h.calls[2].payload.title, "乙"); assert.notEqual(h.calls[1].key, h.calls[2].key);
  });
  await check("network errors do not trigger an automatic retry loop", async () => {
    const h = setup(() => { throw new Error("offline"); }); h.queue.change({ title: "甲" }); await assert.rejects(h.queue.flush());
    await h.fire(); assert.equal(h.calls.length, 1); assert.equal(h.queue.safe(), false); assert.equal(h.queue.state().phase, "error");
  });
  await check("draft conflict blocks retries even after new typing", async () => {
    const h = setup(() => { throw Object.assign(new Error("conflict"), { status: 409, code: "DRAFT_CONFLICT" }); });
    h.queue.change({ title: "甲" }); await assert.rejects(h.queue.flush()); h.queue.change({ title: "乙" }); await assert.rejects(h.queue.flush({ retry: true }));
    assert.equal(h.calls.length, 1); assert.equal(h.queue.state().phase, "conflict");
  });
  await check("a late old-document response cannot change the new queue", async () => {
    const wait = deferred(); const h = setup(() => wait.promise); h.queue.change({ title: "旧文档" }); const old = h.queue.flush(); await tick();
    h.queue.reset({ id: "b", baseRevision: 7, version: 9, snapshot: { title: "新文档" } }); wait.resolve(ack(h.calls[0].payload)); await old;
    assert.equal(h.queue.state().version, 9); assert.equal(h.queue.state().phase, "clean"); assert.equal(h.queue.safe(), true);
  });
  await check("a late old-document error cannot poison a new queue", async () => {
    const wait = deferred(); const h = setup(() => wait.promise); h.queue.change({ title: "旧" }); const old = h.queue.flush(); await tick();
    h.queue.reset({ id: "b", baseRevision: 2, version: 3, snapshot: { title: "新" } }); wait.reject(new Error("old failure")); await old;
    assert.equal(h.queue.state().phase, "clean"); assert.equal(h.queue.state().version, 3);
  });
  await check("Chinese composition defers scheduling until composition end", async () => {
    const h = setup(); h.queue.composing(true); h.queue.change({ title: "拼音未确认" }); assert.equal(h.timers.size, 0);
    await assert.rejects(h.queue.flush()); h.queue.composing(false); assert.equal(h.timers.size, 1); await h.fire(); assert.equal(h.calls.length, 1);
  });
  await check("pause preserves changes without scheduling or writes", async () => {
    const h = setup(); h.queue.change({ title: "甲" }); h.queue.pause(true); assert.equal(h.timers.size, 0); await assert.rejects(h.queue.flush());
    h.queue.pause(false); await h.queue.flush(); assert.equal(h.queue.safe(), true);
  });
  await check("expired session requires same-user reauthentication plus manual retry", async () => {
    let expired = true; const h = setup((id, data) => { if (expired) throw Object.assign(new Error("expired"), { status: 401 }); return ack(data, id); });
    h.queue.change({ title: "甲" }); await assert.rejects(h.queue.flush()); assert.equal(h.queue.state().phase, "expired");
    await assert.rejects(h.queue.flush({ retry: true })); expired = false; h.queue.reauthenticated(); assert.equal(h.timers.size, 0);
    await h.queue.flush({ retry: true }); assert.equal(h.calls[0].key, h.calls[1].key); assert.equal(h.queue.safe(), true);
  });
  await check("validation rejection can be corrected with a fresh operation key", async () => {
    const h = setup((id, data) => { if (!data.title) throw Object.assign(new Error("title required"), { status: 422 }); return ack(data, id); });
    h.queue.change({ title: "" }); await assert.rejects(h.queue.flush()); h.queue.change({ title: "已修正" }); await h.queue.flush({ retry: true });
    assert.notEqual(h.calls[0].key, h.calls[1].key); assert.equal(h.queue.safe(), true);
  });
  await check("unmatched acknowledgement becomes a conflict not a saved indicator", async () => {
    const h = setup(() => ({ id: "wrong", base_revision: 1, draft_version: 1 })); h.queue.change({ title: "甲" }); await assert.rejects(h.queue.flush());
    assert.equal(h.queue.state().phase, "conflict"); assert.equal(h.queue.safe(), false);
  });
  await check("a rejected unavailable source can be removed without retrying its obsolete payload", async () => {
    const h = setup((id, data) => { if (data.citations?.length) throw Object.assign(new Error("source unavailable"), { status: 409, code: "SOURCE_UNAVAILABLE" }); return ack(data, id); });
    h.queue.change({ title: "甲", citations: ["withdrawn"] }); await assert.rejects(h.queue.flush());
    h.queue.change({ title: "甲", citations: [] }); await h.queue.flush({ retry: true });
    assert.equal(h.calls.length, 2); assert.notEqual(h.calls[0].key, h.calls[1].key); assert.equal(h.queue.safe(), true);
  });
  await check("snapshot capture is immutable against caller changes", async () => {
    const h = setup(); const value = { title: "甲", fields: { project: "原值" } }; h.queue.change(value); value.fields.project = "外部修改"; await h.queue.flush();
    assert.equal(h.calls[0].payload.fields.project, "原值");
  });
  await check("unchanged content and repeated flushes do not add writes", async () => {
    const h = setup(); h.queue.change({ title: "原稿" }); await h.queue.flush(); assert.equal(h.calls.length, 0);
    h.queue.change({ title: "甲" }); await h.queue.flush(); await h.queue.flush(); assert.equal(h.calls.length, 1);
  });
  await check("simultaneous flush callers share one in-flight request", async () => {
    const wait = deferred(); const h = setup(() => wait.promise); h.queue.change({ title: "甲" }); const a = h.queue.flush(), b = h.queue.flush(); await tick();
    assert.equal(h.calls.length, 1); wait.resolve(ack(h.calls[0].payload)); await Promise.all([a, b]); assert.equal(h.calls.length, 1);
  });
  await check("reset on logout releases pending snapshots and timers", async () => {
    const h = setup(); h.queue.change({ title: "私有内容" }); h.queue.reset(null); await h.fire(); await h.queue.flush();
    assert.equal(h.calls.length, 0); assert.equal(h.queue.state().phase, "idle"); assert.equal(h.queue.state().hasDraft, false);
  });
  await check("recovered copy starts acknowledged but still has an uncommitted draft", async () => {
    const h = setup(); h.queue.reset({ id: "a", baseRevision: 3, version: 4, snapshot: { title: "恢复内容" }, hasDraft: true });
    assert.equal(h.queue.safe(), true); assert.equal(h.queue.state().hasDraft, true); assert.equal(h.queue.state().phase, "saved"); await h.queue.flush(); assert.equal(h.calls.length, 0);
  });
  console.log(`${passed} deterministic autosave queue checks passed; not real DOM, IME or network QA.`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
