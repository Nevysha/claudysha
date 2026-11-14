// claudysha dashboard: the page shell (navigation, API calls, helpers) and the plugin's overview.
// Modules add panels from their own scripts with claudysha.panel({...}); see plugin/dashboard.py.
"use strict";
(() => {
  // ---------------------------------------------------------------- token
  // The link printed by `claudysha dashboard` carries it in the #fragment, which never leaves
  // the browser. Kept for this tab only, then the fragment is replaced by the page route.
  const TOKEN_KEY = "claudysha-token";
  let memoryToken = "";
  function readToken() {
    const m = location.hash.match(/token=([\w-]+)/);
    if (m) {
      memoryToken = m[1];
      try { sessionStorage.setItem(TOKEN_KEY, m[1]); } catch (e) { /* private mode: memory only */ }
      // Keep a route given with it (#/review&token=...), else the overview.
      const rest = location.hash.replace(/^#/, "").replace(/&?token=[\w-]+/, "");
      history.replaceState(null, "", location.pathname + "#" + (rest || "/overview"));
    }
    if (!memoryToken) {
      try { memoryToken = sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { /* ignore */ }
    }
    return memoryToken;
  }

  // ---------------------------------------------------------------- helpers
  async function api(path, body) {
    const opts = { headers: { "X-Claudysha-Token": readToken() } };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    return data;
  }

  // h("div", {class: "x", onclick: fn}, child, "text", [more])
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (k === "value") el.value = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    addTo(el, children);
    return el;
  }

  // Append children like h() does: arrays are flattened, null/undefined/false skipped.
  function addTo(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach((x) => addTo(el, x));
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }

  // Replace an element's content. Use this, never the browser's replaceChildren/append directly:
  // they turn an array into "[object ...]" text and null into "null".
  function fill(el, ...children) {
    el.replaceChildren();
    addTo(el, children);
    return el;
  }

  function toast(msg, error) {
    const t = h("div", { class: "toast" + (error ? " error" : "") }, msg);
    document.getElementById("toasts").append(t);
    setTimeout(() => t.remove(), error ? 6000 : 3000);
  }

  // Run an async action from a button: disable it meanwhile, show errors as toasts.
  async function act(button, fn) {
    if (button) button.disabled = true;
    try { return await fn(); } catch (e) { toast(e.message, true); return undefined; } finally { if (button) button.disabled = false; }
  }

  function date(iso) { return iso ? iso.slice(0, 10) : ""; }
  function datetime(iso) { return iso ? iso.slice(0, 16).replace("T", " ") : ""; }
  function size(bytes) {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return `${bytes.toFixed(i ? 1 : 0)} ${units[i]}`;
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { toast("Couldn't copy: select the text instead", true); }
  }

  // ---------------------------------------------------------------- panels and routing
  const panels = [];
  let cleanup = null;
  const main = () => document.getElementById("main");

  function panel(p) { panels.push({ order: 50, ...p }); }

  function route() {
    const [id, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
    return { id: id || "overview", arg: rest.join("/") };
  }

  async function renderNav() {
    const nav = document.getElementById("nav");
    const { id } = route();
    fill(nav, ...panels.map((p) => {
      const badge = h("span", { class: "badge hidden" });
      if (p.badge) p.badge().then((n) => { if (n) { badge.textContent = n; badge.classList.remove("hidden"); } }).catch(() => {});
      return h("a", { class: "nav-item" + (p.id === id ? " active" : ""), href: `#/${p.id}` },
        h("span", {}, p.icon || "•"), h("span", {}, p.title), badge);
    }));
  }

  async function render() {
    if (typeof cleanup === "function") cleanup();
    cleanup = null;
    const { id, arg } = route();
    const p = panels.find((x) => x.id === id) || panels[0];
    renderNav();
    const el = main();
    fill(el);
    window.scrollTo(0, 0);
    try { cleanup = await p.render(el, arg); } catch (e) {
      fill(el, h("div", { class: "empty" }, h("span", { class: "big" }, "⚠️"), e.message));
    }
  }

  function go(hash) {
    if (location.hash === hash) render(); else location.hash = hash;
  }

  window.claudysha = { panel, api, h, fill, toast, act, date, datetime, size, copy, go, refreshNav: renderNav };

  // ---------------------------------------------------------------- the plugin's overview
  panel({
    id: "overview", title: "Overview", icon: "🏠", order: 0,
    async render(el) {
      const o = await api("/api/plugin/overview");
      document.getElementById("who").textContent = `${o.user.name} (${o.user.pronouns})`;
      const cards = o.cards.map((c) => {
        const body = [h("div", { class: "label" }, c.label), h("div", { class: "value" }, c.value),
          c.hint && h("div", { class: "hint" }, c.hint)];
        const cls = "card stat" + (c.accent ? " accent" : "");
        return c.panel ? h("a", { class: cls, href: `#/${c.panel}` }, body) : h("div", { class: cls }, body);
      });

      const settings = h("dl", { class: "kv" },
        Object.entries(o.settings).flatMap(([k, v]) => [h("dt", {}, k), h("dd", { class: "mono" }, v)]));
      const status = h("dl", { class: "kv" },
        h("dt", {}, "Settings file"), h("dd", {}, h("span", { class: "mono" }, o.config.path), o.config.exists ? "" : h("span", { class: "pill warn" }, "missing: defaults")),
        h("dt", {}, "Data folder"), h("dd", {}, h("span", { class: "mono" }, o.data.path), " ", h("span", { class: "muted" }, size(o.data.size))),
        h("dt", {}, "Dev mode"), h("dd", {}, o.dev_mode ? h("span", { class: "pill warn" }, "on") : h("span", { class: "pill" }, "off"),
          h("span", { class: "muted small" }, "  changed only in your terminal: claudysha dev on/off")),
        h("dt", {}, "Guard"), h("dd", {}, o.guard.paused_until
          ? h("span", { class: "pill warn" }, `paused until ${o.guard.paused_until.slice(11)}`)
          : h("span", { class: "pill ok" }, "on")));

      const archiveList = h("div");
      const preview = h("div");
      const showArchives = (list) => {
        fill(archiveList, list.length ? h("table", { class: "list" },
          h("tr", {}, h("th", {}, "Archive"), h("th", {}, "Kind"), h("th", {}, "Date"), h("th", {}, "Size"), h("th", {})),
          list.map((a) => h("tr", {},
            h("td", { class: "mono" }, a.name), h("td", {}, h("span", { class: "pill" + (a.kind === "archive" ? "" : " warn") }, a.kind)),
            h("td", {}, datetime(a.modified)), h("td", {}, size(a.size)),
            h("td", {}, h("button", { class: "small ghost", onclick: (e) => showPreview(e.currentTarget, a.name) }, "Restore…")))))
          : h("p", { class: "muted card-body" }, "No archive yet."));
      };
      const showPreview = (btn, name) => act(btn, async () => {
        const p = await api(`/api/plugin/load-preview?name=${encodeURIComponent(name)}`);
        fill(preview, h("div", { class: "card card-body" },
          h("h2", {}, `Restore ${p.archive.name}`),
          h("p", {}, `Made ${datetime(p.created)}, ${p.files} files. Restoring replaces all of claudysha's data with it`,
            p.erased.length ? `, and erases ${p.erased.length} current file(s) that aren't in it:` : "."),
          p.erased.length ? h("pre", { class: "cmd" }, p.erased.slice(0, 20).join("\n") + (p.erased.length > 20 ? `\n… and ${p.erased.length - 20} more` : "")) : null,
          h("p", { class: "note" }, "Restoring is never done from this page: it needs you to type erase in your own terminal, ",
            "so no program (and no Claude session) can do it for you. Your current data is saved first."),
          h("div", { class: "toolbar" }, h("pre", { class: "cmd spacer" }, p.command),
            h("button", { onclick: () => copy(p.command) }, "Copy"))));
        preview.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      showArchives(o.archives);

      el.append(
        h("div", { class: "page-head" }, h("div", {}, h("h1", {}, `Hello, ${o.user.name}`),
          h("p", {}, "What claudysha remembers, and how it's set up."))),
        h("div", { class: "grid-cards" }, cards),
        h("h2", {}, "Settings"),
        h("div", { class: "card card-body" }, settings, h("p", { class: "muted small" }, "Edit them in the settings file, or with claudysha setup.")),
        h("h2", {}, "Status"),
        h("div", { class: "card card-body" }, status),
        h("div", { class: "page-head" },
          h("h2", {}, "Archives"),
          h("button", { class: "primary", onclick: (e) => act(e.currentTarget, async () => {
            const r = await api("/api/plugin/archive", {});
            showArchives(r.archives);
            toast(`Archived to ${r.path}`);
          }) }, "Archive now")),
        h("div", { class: "card" }, archiveList),
        h("p", { class: "muted small" }, `New archives go to ${o.archives_dir}. Copy them somewhere safe for a real backup.`),
        preview);
    },
  });

  // ---------------------------------------------------------------- session preview
  panel({
    id: "session", title: "Session preview", icon: "🪄", order: 30,
    async render(el) {
      const body = h("div");
      const picker = h("select", {});
      let loaded = false;

      async function load() {
        fill(body, h("p", { class: "muted" }, "Running the session start…"));
        const p = await act(null, () => api(`/api/plugin/session-preview?scope=${encodeURIComponent(picker.value)}`));
        if (!p) { fill(body); return; }
        if (!loaded) {
          fill(picker, h("option", { value: "" }, "A new folder (fresh session)"),
            p.scopes.filter((s) => s.scope !== "global").map((s) => h("option", { value: s.scope }, `${s.scope} (${s.count})`)));
          loaded = true;
        }
        const max = Math.max(1, ...p.sections.map((s) => s.tokens));
        const rows = p.sections.map((s) => h("tr", {},
          h("td", {}, h("a", { href: `#sec-${s.source}`, onclick: (e) => { e.preventDefault(); document.getElementById(`sec-${s.source}`)?.scrollIntoView({ behavior: "smooth" }); } }, s.title)),
          h("td", { class: "bar-cell", title: `${s.tokens} tokens` }, h("div", { class: "bar-track" }, sized(h("div", { class: "bar" }), s.tokens / max))),
          h("td", { class: "num" }, s.tokens ? `≈ ${s.tokens.toLocaleString()}` : h("span", { class: "muted" }, "nothing")),
          h("td", { class: "num muted" }, p.total ? `${Math.round((100 * s.tokens) / p.total)}%` : "")));

        fill(body, 
          h("div", { class: "grid-cards" },
            h("div", { class: "card stat accent" }, h("div", { class: "label" }, "Added to every session start"),
              h("div", { class: "value" }, `≈ ${p.total.toLocaleString()}`), h("div", { class: "hint" }, "tokens, estimated")),
            h("div", { class: "card stat" }, h("div", { class: "label" }, "Scope shown"),
              h("div", { class: "value small-value mono" }, p.scope || "new folder"),
              h("div", { class: "hint" }, p.scope ? "a session working in this repository or folder" : `a folder claudysha has never seen (${p.shown_scope})`))),
          h("h2", {}, "Where the tokens go"),
          h("div", { class: "card" }, h("table", { class: "list" },
            h("tr", {}, h("th", {}, "Part"), h("th", {}, ""), h("th", { class: "num" }, "Tokens"), h("th", { class: "num" }, "Share")), rows)),
          h("p", { class: "muted small" }, "Estimates: claudysha has no tokenizer, so counts can be off by about 15%. ",
            "Claude Code adds its own system prompt and tools on top; this is only what claudysha adds. ",
            "A skill's full instructions load only when it's used."),
          ...p.sections.map((s) => h("section", { id: `sec-${s.source}` },
            h("h2", {}, s.title, " ", h("span", { class: "pill" }, s.tokens ? `≈ ${s.tokens} tokens` : "prints nothing"),
              " ", h("span", { class: "muted small mono" }, s.source)),
            s.error ? h("p", { class: "note" }, `This part failed: ${s.error}`) : null,
            s.text ? h("pre", { class: "cmd injected" }, s.text)
              : h("p", { class: "muted small" }, s.kind === "hook" ? "Nothing printed for this session (for example, no backlog item is due, or dev mode is off)." : "No skill listed."))),
          h("div", { class: "page-head" }, h("div", {}, h("h1", { class: "sub" }, "Loaded when used"),
            h("p", {}, "Not part of the session start, so not in the total above. A skill's full instructions join the session ",
              "the first time it's used, and stay for the rest of it. This is where sessions learn the memory commands."))),
          p.on_demand.length ? h("div", { class: "card" }, h("table", { class: "list" },
            h("tr", {}, h("th", {}, "Skill"), h("th", {}, "Loads"), h("th", { class: "num" }, "Tokens")),
            p.on_demand.map((s) => h("tr", {}, h("td", {}, h("a", { href: `#sec-${s.source}`, onclick: (e) => { e.preventDefault(); document.getElementById(`sec-${s.source}`)?.scrollIntoView({ behavior: "smooth" }); } }, s.title)),
              h("td", { class: "muted" }, s.when), h("td", { class: "num" }, `≈ ${s.tokens.toLocaleString()}`))))) : null,
          p.on_demand.map((s) => h("section", { id: `sec-${s.source}` },
            h("h2", {}, s.title, " ", h("span", { class: "pill" }, `≈ ${s.tokens} tokens`), " ", h("span", { class: "muted small" }, s.when)),
            h("pre", { class: "cmd injected" }, s.text))));
      }
      function sized(bar, ratio) { bar.style.width = `${Math.max(ratio * 100, ratio > 0 ? 1 : 0)}%`; return bar; }

      picker.addEventListener("change", load);
      el.append(
        h("div", { class: "page-head" },
          h("div", {}, h("h1", {}, "Session preview"),
            h("p", {}, "What claudysha adds when a Claude Code session starts, exactly as the session receives it.")),
          h("label", { class: "check" }, "Session in ", picker)),
        body);
      await load();
    },
  });

  window.addEventListener("hashchange", render);
  document.addEventListener("DOMContentLoaded", () => {
    panels.sort((a, b) => a.order - b.order);
    if (!readToken()) {
      fill(main(), h("div", { class: "empty" }, h("span", { class: "big" }, "🔒"),
        "Open the link that claudysha dashboard printed in your terminal: it carries the key to this page."));
      return;
    }
    render();
  });
})();
