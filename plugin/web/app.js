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
    const add = (c) => {
      if (c === null || c === undefined || c === false) return;
      if (Array.isArray(c)) c.forEach(add);
      else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    };
    children.forEach(add);
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
    nav.replaceChildren(...panels.map((p) => {
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
    el.replaceChildren();
    window.scrollTo(0, 0);
    try { cleanup = await p.render(el, arg); } catch (e) {
      el.replaceChildren(h("div", { class: "empty" }, h("span", { class: "big" }, "⚠️"), e.message));
    }
  }

  function go(hash) {
    if (location.hash === hash) render(); else location.hash = hash;
  }

  window.claudysha = { panel, api, h, toast, act, date, datetime, size, copy, go, refreshNav: renderNav };

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
        archiveList.replaceChildren(list.length ? h("table", { class: "list" },
          h("tr", {}, h("th", {}, "Archive"), h("th", {}, "Kind"), h("th", {}, "Date"), h("th", {}, "Size"), h("th", {})),
          list.map((a) => h("tr", {},
            h("td", { class: "mono" }, a.name), h("td", {}, h("span", { class: "pill" + (a.kind === "archive" ? "" : " warn") }, a.kind)),
            h("td", {}, datetime(a.modified)), h("td", {}, size(a.size)),
            h("td", {}, h("button", { class: "small ghost", onclick: (e) => showPreview(e.currentTarget, a.name) }, "Restore…")))))
          : h("p", { class: "muted card-body" }, "No archive yet."));
      };
      const showPreview = (btn, name) => act(btn, async () => {
        const p = await api(`/api/plugin/load-preview?name=${encodeURIComponent(name)}`);
        preview.replaceChildren(h("div", { class: "card card-body" },
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

  window.addEventListener("hashchange", render);
  document.addEventListener("DOMContentLoaded", () => {
    panels.sort((a, b) => a.order - b.order);
    if (!readToken()) {
      main().replaceChildren(h("div", { class: "empty" }, h("span", { class: "big" }, "🔒"),
        "Open the link that claudysha dashboard printed in your terminal: it carries the key to this page."));
      return;
    }
    render();
  });
})();
