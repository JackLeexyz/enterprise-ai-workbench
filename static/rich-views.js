globalThis.WORKBENCH_RICH_VIEWS = (() => {
  "use strict";
  const structured = doc => doc?.content_json?.schema_version === 2;
  const editable = (doc, history = false) => Boolean(doc?.can_edit && !history && doc.content_json?.schema_version === 1);
  function safeHref(value) {
    if (typeof value !== "string" || value.length > 2048 || /[\x00-\x20\x7f\\]/.test(value)) return false;
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname) && !url.username && !url.password;
    } catch { return false; }
  }
  function inline(ui, fragments) {
    return (fragments || []).map(fragment => {
      let html = ui.esc(fragment.text);
      for (const mark of fragment.marks || []) {
        if (mark.type === "bold") html = `<strong>${html}</strong>`;
        if (mark.type === "italic") html = `<em>${html}</em>`;
        if (mark.type === "link" && safeHref(mark.href)) html = ui.preview ? `<span class="rich-link">${html}</span>` : `<a class="rich-link" href="${ui.esc(mark.href)}" target="_blank" rel="noopener noreferrer">${html}</a>`;
      }
      return html;
    }).join("");
  }
  function body(ui, content) {
    if (content.schema_version !== 2) return '<p class="notice warning">此内容格式暂不支持预览。</p>';
    return content.blocks.map(block => {
      const attrs = `data-block="${ui.esc(block.id)}"`;
      if (block.type === "table") {
        const row = (cells, header) => `<tr>${cells.map(cell => `<${header ? "th scope=\"col\"" : "td"}>${inline(ui, cell.runs)}</${header ? "th" : "td"}>`).join("")}</tr>`;
        return `<div class="rich-table-scroll" ${attrs} role="region" aria-label="稿件表格" tabindex="0"><table class="rich-table">${block.header ? `<thead>${row(block.rows[0], true)}</thead>` : ""}<tbody>${block.rows.slice(block.header ? 1 : 0).map(cells => row(cells, false)).join("")}</tbody></table></div>`;
      }
      if (block.type === "heading") {
        const level = [1, 2, 3].includes(block.level) ? block.level : 1;
        return `<h${level + 1} class="rich-heading" data-level="${level}" ${attrs}>${inline(ui, block.runs)}</h${level + 1}>`;
      }
      const html = inline(ui, block.runs) || "<br>";
      return block.type === "bullet" ? `<ul class="rich-bullet" ${attrs}><li>${html}</li></ul>` : `<p class="rich-paragraph" ${attrs}>${html}</p>`;
    }).join("");
  }
  return Object.freeze({ structured, editable, safeHref, inline, body });
})();
