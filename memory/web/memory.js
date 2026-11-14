// Memory module: the review queue and the memory browser (routes in memory/panel.py).
"use strict";
(() => {
  const { panel, api, h, fill, toast, act, date, datetime, refreshNav } = window.claudysha;
  let summaryCache = null;

  async function summary(fresh) {
    if (!summaryCache || fresh) summaryCache = await api("/api/memory/summary");
    return summaryCache;
  }

  const typePill = (t) => h("span", { class: `pill t-${t}` }, t.replace("_", " "));
  const scopePill = (s) => h("span", { class: "pill scope", title: s }, s);
  function statusPill(s) {
    const cls = { active: "ok", proposed: "accent", superseded: "", rejected: "bad" }[s];
    return h("span", { class: `pill ${cls}` }, s);
  }

  function select(options, value) {
    return h("select", {}, options.map((o) => {
      const [v, label] = Array.isArray(o) ? o : [o, o];
      return h("option", { value: v, selected: v === value }, label);
    }));
  }

  function scopeInput(value, scopes) {
    const id = `scopes-${Math.random().toString(36).slice(2)}`;
    return [h("input", { type: "text", value, list: id, placeholder: "scope (global for every session)" }),
      h("datalist", { id }, scopes.map((s) => h("option", { value: s.scope })))];
  }

  // The edit form shared by both panels. onSave gets the updated memory.
  function editForm(m, s, onSave, onCancel, full) {
    const text = h("textarea", { rows: 4 }, m.text);
    const type = select(s.types, m.type);
    const [scope, list] = scopeInput(m.scope, s.scopes);
    const tags = full ? h("input", { type: "text", value: m.tags, placeholder: "tags" }) : null;
    const conf = full ? h("input", { type: "number", min: 0, max: 1, step: 0.1, value: m.confidence }) : null;
    const save = h("button", { class: "primary small", onclick: () => act(save, async () => {
      const body = { id: m.id };
      if (text.value.trim() !== m.text) body.text = text.value;
      if (type.value !== m.type) body.type = type.value;
      if (scope.value.trim() !== m.scope) body.scope = scope.value;
      if (tags && tags.value !== m.tags) body.tags = tags.value;
      if (conf && Number(conf.value) !== m.confidence) body.confidence = Number(conf.value);
      if (Object.keys(body).length === 1) { onCancel(); return; }
      const r = await api("/api/memory/edit", body);
      toast(`#${m.id} saved`);
      onSave(r.item);
    }) }, "Save");
    return h("div", { class: "edit-box" }, text,
      h("div", { class: "edit-row" }, type, scope, list, tags, conf),
      h("div", { class: "row-actions" }, save, h("button", { class: "small ghost", onclick: onCancel }, "Cancel")));
  }

  async function setStatus(ids, status) {
    await api("/api/memory/status", { ids, status });
    summaryCache = null;
    refreshNav();
  }

  // ---------------------------------------------------------------- review queue
  panel({
    id: "review", title: "Review", icon: "📥", order: 10,
    async badge() { return (await summary(true)).counts.proposed; },
    async render(el) {
      const [data, s] = await Promise.all([api("/api/memory/proposed"), summary(true)]);
      let items = data.items;
      const selected = new Set();
      const head = h("div", { class: "page-head" });
      const body = h("div");

      const bulkApprove = h("button", { class: "ok", onclick: () => bulk(bulkApprove, "active") });
      const bulkReject = h("button", { class: "bad", onclick: () => bulk(bulkReject, "rejected") });
      const selectAll = h("input", { type: "checkbox", onchange: () => {
        items.forEach((m) => (selectAll.checked ? selected.add(m.id) : selected.delete(m.id)));
        draw();
      } });

      function drawHead() {
        const n = selected.size;
        bulkApprove.textContent = `Approve ${n || ""}`.trim();
        bulkReject.textContent = `Reject ${n || ""}`.trim();
        bulkApprove.disabled = bulkReject.disabled = !n;
        selectAll.checked = n > 0 && n === items.length;
        fill(head, 
          h("div", {}, h("h1", {}, "Review"),
            h("p", {}, items.length
              ? `${items.length} proposed memor${items.length === 1 ? "y" : "ies"} about how Claude should work with you. Nothing here is used until you approve it.`
              : "Proposed memories about how Claude should work with you wait here for your OK.")),
          items.length ? h("div", { class: "row-actions" }, h("label", { class: "check" }, selectAll, "All"), bulkApprove, bulkReject) : null);
      }

      async function bulk(btn, status) {
        const ids = [...selected];
        await act(btn, async () => {
          await setStatus(ids, status);
          toast(`${status === "active" ? "Approved" : "Rejected"} ${ids.length}`);
          leave(ids);
        });
      }

      function leave(ids) {
        ids.forEach((id) => { selected.delete(id); body.querySelector(`[data-id="${id}"]`)?.classList.add("leaving"); });
        setTimeout(() => { items = items.filter((m) => !ids.includes(m.id)); draw(); }, 250);
      }

      function card(m) {
        const box = h("div", { class: "card mem" + (selected.has(m.id) ? " selected" : ""), "data-id": m.id });
        const paint = (editing) => {
          const check = h("input", { type: "checkbox", checked: selected.has(m.id), onchange: () => {
            check.checked ? selected.add(m.id) : selected.delete(m.id);
            box.classList.toggle("selected", check.checked);
            drawHead();
          } });
          const approve = h("button", { class: "ok small", onclick: () => act(approve, async () => { await setStatus([m.id], "active"); toast(`#${m.id} approved`); leave([m.id]); }) }, "✓ Approve");
          const reject = h("button", { class: "bad small", onclick: () => act(reject, async () => { await setStatus([m.id], "rejected"); toast(`#${m.id} rejected`); leave([m.id]); }) }, "✗ Reject");
          fill(box, 
            h("div", { class: "mem-head" }, check, h("span", { class: "id" }, `#${m.id}`), typePill(m.type), scopePill(m.scope),
              h("span", {}, date(m.created_at)), h("span", { title: "confidence" }, `c${m.confidence.toFixed(1)}`)),
            editing
              ? editForm(m, s, (updated) => { Object.assign(m, updated); paint(false); }, () => paint(false))
              : h("div", { class: "mem-text" }, m.text),
            editing ? null : h("div", { class: "mem-foot" }, approve, reject,
              h("button", { class: "small ghost", onclick: () => paint(true) }, "✎ Edit"),
              h("span", { class: "spacer" }), h("span", { class: "mem-source" }, `from ${m.source_label}`)),
            m.related.length && !editing ? h("div", { class: "related" }, h("div", { class: "related-title" }, "Looks related"),
              m.related.map((r) => h("div", { class: "related-item" },
                h("a", { class: "id", href: `#/memories/${r.id}` }, `#${r.id}`), statusPill(r.status), h("span", {}, r.text)))) : null);
        };
        paint(false);
        return box;
      }

      function draw() {
        drawHead();
        if (!items.length) {
          fill(body, h("div", { class: "card empty" }, h("span", { class: "big" }, "🎉"), "Nothing to review."));
          return;
        }
        const groups = new Map();
        items.forEach((m) => { if (!groups.has(m.scope)) groups.set(m.scope, []); groups.get(m.scope).push(m); });
        fill(body, ...[...groups].map(([scope, ms]) => h("section", { class: "scope-group" },
          h("h2", {}, scopePill(scope), h("span", { class: "muted small" },
            scope === s.global ? "applies to every session" : "applies to sessions in this scope")),
          h("div", { class: "mem-list" }, ms.map(card)))));
      }

      draw();
      el.append(head, body);
    },
  });

  // ---------------------------------------------------------------- browser
  const filters = { q: "", scope: "", type: "", status: "current", source: "", unused: false };

  panel({
    id: "memories", title: "Memories", icon: "🔎", order: 20,
    async render(el, arg) {
      const s = await summary(true);
      let offset = 0;
      let current = arg ? Number(arg) : null;
      const layout = h("div", { class: "browser" });
      const listBox = h("div");
      const detail = h("div", { class: "card detail hidden" });
      layout.append(listBox, detail);

      const q = h("input", { type: "search", value: filters.q, placeholder: "Search memories…" });
      const scope = select([["", "All scopes"], ...s.scopes.map((x) => [x.scope, `${x.scope} (${x.count})`])], filters.scope);
      const type = select([["", "All types"], ...s.types.map((t) => [t, t.replace("_", " ")])], filters.type);
      const status = select([["current", "Active + proposed"], "active", "proposed", "superseded", "rejected", ["all", "Any status"]], filters.status);
      const source = select([["", "Any source"], ["extract", "From sessions"], ["manual", "Added by hand"]], filters.source);
      const unused = h("input", { type: "checkbox", checked: filters.unused });
      let timer = null;
      const changed = () => {
        Object.assign(filters, { q: q.value, scope: scope.value, type: type.value, status: status.value, source: source.value, unused: unused.checked });
        offset = 0;
        load();
      };
      q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(changed, 250); });
      [scope, type, status, source, unused].forEach((x) => x.addEventListener("change", changed));

      async function load(more) {
        const params = new URLSearchParams({ q: filters.q, scope: filters.scope, type: filters.type, status: filters.status,
          source: filters.source, offset: String(offset), limit: "50" });
        if (filters.unused) params.set("unused_days", "30");
        const r = await act(null, () => api(`/api/memory/list?${params}`));
        if (!r) return;
        const rows = r.items.map((m) => h("tr", { class: "mem-row" + (m.id === current ? " current" : ""), "data-id": m.id, onclick: () => open(m.id) },
          h("td", { class: "meta mono" }, `#${m.id}`),
          h("td", { class: "col-text" }, h("div", { class: "text" }, m.text)),
          h("td", { class: "meta" }, typePill(m.type), " ", m.status !== "active" ? statusPill(m.status) : null),
          h("td", { class: "meta col-scope" }, scopePill(m.scope)),
          h("td", { class: "meta col-when", title: m.last_used_at ? `last used ${datetime(m.last_used_at)}` : "never used" }, date(m.created_at), h("br"), `used ${m.use_count}×`)));
        let table = listBox.querySelector("tbody");
        if (!more || !table) {
          table = h("tbody");
          fill(listBox, 
            h("p", { class: "muted small" }, r.total ? `${r.total} memor${r.total === 1 ? "y" : "ies"}${r.ranked ? ", best matches first" : ", newest first"}` : ""),
            r.total ? h("div", { class: "card" }, h("table", { class: "list" }, table)) : h("div", { class: "card empty" }, h("span", { class: "big" }, "🫥"), "No memory matches."));
        }
        table.append(...rows);
        listBox.querySelector(".more")?.remove();
        offset += r.items.length;
        if (offset < r.total) {
          const btn = h("button", { class: "more", onclick: () => load(true) }, `Show more (${r.total - offset} left)`);
          listBox.append(h("div", { class: "toolbar" }, btn));
        }
      }

      async function open(id) {
        current = id;
        listBox.querySelectorAll(".mem-row").forEach((tr) => tr.classList.toggle("current", Number(tr.dataset.id) === id));
        const d = await act(null, () => api(`/api/memory/item?id=${id}`));
        if (!d) return;
        layout.classList.add("with-detail");
        detail.classList.remove("hidden");
        drawDetail(d);
      }

      function close() {
        current = null;
        layout.classList.remove("with-detail");
        detail.classList.add("hidden");
        listBox.querySelectorAll(".mem-row.current").forEach((tr) => tr.classList.remove("current"));
      }

      async function after(msg, id) {
        toast(msg);
        summaryCache = null;
        refreshNav();
        offset = 0;
        await load();
        await open(id);
      }

      function drawDetail(d, mode) {
        const m = d.item;
        const link = (x, prefix) => h("a", { href: "#", onclick: (e) => { e.preventDefault(); open(x.id); } },
          h("span", { class: "mono muted" }, `${prefix} #${x.id} `), x.text);
        const btn = (label, cls, fn) => { const b = h("button", { class: `small ${cls}`, onclick: () => act(b, fn) }, label); return b; };
        const actions = [];
        if (m.status === "proposed") actions.push(btn("✓ Approve", "ok", async () => { await setStatus([m.id], "active"); await after(`#${m.id} approved`, m.id); }));
        if (m.status === "rejected") actions.push(btn("↶ Restore", "ok", async () => { await setStatus([m.id], "active"); await after(`#${m.id} restored`, m.id); }));
        if (m.status !== "superseded") {
          actions.push(h("button", { class: "small", onclick: () => drawDetail(d, "edit") }, "✎ Edit"));
          if (m.status !== "rejected") actions.push(h("button", { class: "small", onclick: () => drawDetail(d, "supersede") }, "⇢ Replace"));
        }
        if (m.status === "active" || m.status === "proposed") actions.push(btn(m.status === "active" ? "✗ Forget" : "✗ Reject", "bad", async () => { await setStatus([m.id], "rejected"); await after(`#${m.id} rejected`, m.id); }));

        let main;
        if (mode === "edit") {
          main = editForm(m, s, (u) => after(`#${u.id} saved`, u.id), () => drawDetail(d), true);
        } else if (mode === "supersede") {
          const text = h("textarea", { rows: 5 }, m.text);
          const save = btn("Replace", "primary", async () => {
            const r = await api("/api/memory/supersede", { id: m.id, text: text.value });
            await after(`#${r.old} replaced by #${r.new}`, r.new);
          });
          main = h("div", { class: "edit-box" },
            h("p", { class: "muted small" }, "Write the up-to-date version. The old one is kept, marked as replaced, so its history stays."),
            text, h("div", { class: "row-actions" }, save, h("button", { class: "small ghost", onclick: () => drawDetail(d) }, "Cancel")));
        } else {
          main = h("div", { class: "mem-text" }, m.text);
        }

        fill(detail, h("div", { class: "card-body" },
          h("div", { class: "mem-head" }, h("span", { class: "id" }, `#${m.id}`), typePill(m.type), statusPill(m.status),
            h("span", { class: "spacer" }), h("button", { class: "small ghost", title: "Close", onclick: close }, "✕")),
          main,
          mode ? null : h("div", { class: "row-actions mem-foot" }, actions),
          mode ? null : h("dl", { class: "kv small spaced" },
            h("dt", {}, "Scope"), h("dd", {}, scopePill(m.scope)),
            h("dt", {}, "Tags"), h("dd", {}, m.tags || h("span", { class: "muted" }, "none")),
            h("dt", {}, "Confidence"), h("dd", {}, m.confidence.toFixed(2)),
            h("dt", {}, "From"), h("dd", {}, m.source_label),
            h("dt", {}, "Created"), h("dd", {}, datetime(m.created_at)),
            h("dt", {}, "Used"), h("dd", {}, `${m.use_count}× by sessions`, m.last_used_at ? `, last ${datetime(m.last_used_at)}` : "")),
          d.superseded_by ? [h("h2", {}, "Replaced by"), h("div", { class: "chain" }, link(d.superseded_by, "→"))] : null,
          d.supersedes.length ? [h("h2", {}, "Replaces"), h("div", { class: "chain" }, d.supersedes.map((x) => link(x, "←")))] : null,
          d.related.length && !mode ? [h("h2", {}, "Related"), h("div", { class: "chain" }, d.related.map((x) => link(x, "")))] : null));
      }

      el.append(
        h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Memories"),
          h("p", {}, `${s.counts.active} active, ${s.counts.proposed} proposed, ${s.counts.superseded} replaced, ${s.counts.rejected} rejected.`))),
        h("div", { class: "toolbar" }, q, scope, type, status, source, h("label", { class: "check", title: "never used by a session, older than 30 days" }, unused, "Never used")),
        layout);
      await load();
      if (current) await open(current);
      q.focus();
    },
  });
})();
