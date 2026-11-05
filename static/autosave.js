globalThis.WORKBENCH_AUTOSAVE = (() => {
  "use strict";
  const copy = value => JSON.parse(JSON.stringify(value));
  function create({ write, onState = () => {}, delay = 1000, schedule = setTimeout, cancel = clearTimeout, makeKey = () => crypto.randomUUID() }) {
    let generation = 0, context = null, latest = "", acknowledged = "", intent = null, active = null, timer;
    let phase = "idle", error = null, hasDraft = false, composing = false, paused = false;
    const safe = () => Boolean(context && latest === acknowledged && !intent && !active && !paused && !composing && !["conflict", "expired", "error"].includes(phase));
    const state = () => ({ phase, error: error?.message || "", version: context?.version || 0, hasDraft, composing, safe: safe() });
    const emit = () => onState(state());
    const stopTimer = () => { cancel(timer); timer = undefined; };
    function arm() {
      stopTimer();
      if (context && latest !== acknowledged && !active && !paused && !composing && phase === "waiting") timer = schedule(() => { flush().catch(() => {}); }, delay);
    }
    function reset(value) {
      generation++; stopTimer(); context = value ? { id: value.id, baseRevision: value.baseRevision, version: value.version } : null;
      latest = acknowledged = value ? JSON.stringify(value.snapshot) : "";
      hasDraft = Boolean(value?.hasDraft); intent = active = null; error = null; paused = composing = false;
      phase = value ? hasDraft ? "saved" : "clean" : "idle"; emit();
    }
    function change(value) {
      if (!context) return;
      latest = JSON.stringify(value);
      if (!["conflict", "expired", "error"].includes(phase)) phase = active ? "saving" : latest === acknowledged ? hasDraft ? "saved" : "clean" : "waiting";
      emit(); arm();
    }
    async function attempt() {
      const epoch = generation;
      if (!intent) intent = { key: makeKey(), snapshot: latest, payload: { ...JSON.parse(latest), base_revision: context.baseRevision, base_draft_version: context.version } };
      const request = intent, target = { ...context };
      phase = "saving"; error = null;
      active = Promise.resolve().then(() => write(target.id, copy(request.payload), request.key));
      emit();
      try {
        const result = await active;
        if (epoch !== generation) return;
        if (result.id !== target.id || result.base_revision !== target.baseRevision || !Number.isInteger(result.draft_version) || result.draft_version <= target.version) throw Object.assign(new Error("草稿同步回执不匹配，请重新读取后核对。"), { code: "DRAFT_CONFLICT" });
        context.version = result.draft_version; acknowledged = request.snapshot; intent = null; hasDraft = true;
        phase = latest === acknowledged ? "saved" : "waiting";
      } catch (problem) {
        if (epoch !== generation) return;
        error = problem;
        phase = problem.status === 401 ? "expired" : ["DRAFT_CONFLICT", "REVISION_CONFLICT", "FORMAT_DOWNGRADE"].includes(problem.code) || problem.status === 404 ? "conflict" : "error";
        if (phase === "error" && problem.status >= 400 && problem.status < 500) intent = null;
        throw problem;
      } finally {
        if (epoch === generation) { active = null; emit(); arm(); }
      }
    }
    async function flush({ retry = false } = {}) {
      stopTimer();
      const epoch = generation;
      if (!context) return;
      if (composing || paused) throw new Error("草稿同步暂缓，请完成输入或处理当前提示后继续。");
      if (["conflict", "expired"].includes(phase) || phase === "error" && !retry) throw error || new Error("请先处理草稿同步状态。");
      if (phase === "error" && retry) { phase = "waiting"; error = null; }
      while (epoch === generation && (active || intent || latest !== acknowledged)) {
        if (active) await active;
        else await attempt();
        stopTimer();
      }
      return state();
    }
    return Object.freeze({ reset, change, flush, state, safe,
      pause(value) { paused = value; if (value) stopTimer(); else arm(); },
      composing(value) { composing = value; if (value) stopTimer(); else arm(); },
      async settled() { try { if (active) await active; } catch {} },
      reauthenticated() { if (phase === "expired") { phase = "error"; error = new Error("已重新登录，请手动重试同步。"); emit(); } }
    });
  }
  return Object.freeze({ create });
})();
