/* ═══════════════════════════════════════════════════════════════════════════
   PHOSPHOR control panel — vanilla SPA
   ═══════════════════════════════════════════════════════════════════════════ */
"use strict";

const TOKEN_KEY = "phosphor.token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";
let ME = null;
const STATE = { mailboxes: [], domains: [], lists: [], templates: [] };

/* ── tiny DOM helper ─────────────────────────────────────────────────────── */
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
const $ = (sel, root = document) => root.querySelector(sel);
const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

/* ── API client ─────────────────────────────────────────────────────────── */
async function api(path, { method, body, form, raw } = {}) {
  method = method || (body !== undefined || form ? "POST" : "GET");
  const headers = {};
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  let payload;
  if (form) { payload = form; }
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }

  const res = await fetch("/api" + path, { method, headers, body: payload });
  if (res.status === 401 && !path.startsWith("/auth/")) { logout(); throw new Error("session expired"); }
  if (res.status === 204) return null;
  const ctype = res.headers.get("content-type") || "";
  const data = raw ? await res.text() : (ctype.includes("json") ? await res.json() : await res.text());
  if (!res.ok) {
    const detail = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    throw new Error(detail);
  }
  return data;
}

/* ── toast + modal ──────────────────────────────────────────────────────── */
function toast(msg, kind = "ok") {
  const t = el("div", { class: `toast ${kind === "ok" ? "" : kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, 3600);
  setTimeout(() => t.remove(), 4000);
}
function fail(e) { console.error(e); toast(e.message || String(e), "err"); }

function modal({ title, body, wide, actions }) {
  const root = $("#modal-root");
  const close = () => clear(root);
  const scrim = el("div", { class: "modal-scrim", onclick: (e) => { if (e.target === scrim) close(); } },
    el("div", { class: "modal" + (wide ? " wide" : "") },
      el("div", { class: "modal-head" }, el("h3", {}, title), el("button", { class: "x", onclick: close }, "×")),
      el("div", { class: "modal-body" }, body),
      actions && el("div", { class: "modal-foot" }, ...actions(close)),
    ),
  );
  clear(root).append(scrim);
  return close;
}
function confirmDialog(msg, onYes, { danger = true, yes = "Confirm" } = {}) {
  modal({
    title: "Confirm", body: el("p", {}, msg),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn " + (danger ? "danger" : "primary"), onclick: async () => { close(); try { await onYes(); } catch (e) { fail(e); } } }, yes),
    ],
  });
}

/* ── formatting ─────────────────────────────────────────────────────────── */
const fmtInt = (n) => (n ?? 0).toLocaleString();
function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s), now = new Date(), diff = (now - d) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function fmtBytes(b) {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(i ? 1 : 0) + " " + u[i];
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function pill(text, kind = "") { return el("span", { class: "pill " + kind }, text); }
function statusPill(s) {
  const map = {
    running: "ok", active: "ok", sent: "ok", completed: "info", delivered: "ok", replied: "ok",
    paused: "warn", deferred: "warn", scheduled: "info", draft: "", pending: "",
    failed: "bad", bounced: "bad", unsubscribed: "bad", stopped_replied: "ok",
    stopped_bounced: "bad", stopped_unsubscribed: "bad",
  };
  return pill((s || "").replace(/_/g, " "), map[s] ?? "");
}

/* ── auth ───────────────────────────────────────────────────────────────── */
function logout() {
  TOKEN = ""; ME = null; localStorage.removeItem(TOKEN_KEY);
  location.hash = ""; renderAuth();
}
function setToken(tok) { TOKEN = tok; localStorage.setItem(TOKEN_KEY, tok); }

async function renderAuth() {
  const app = $("#app");
  let status;
  try { status = await api("/auth/status"); } catch (e) { fail(e); return; }
  const needsSetup = status.needs_setup;

  const emailIn = el("input", { type: "email", placeholder: "you@example.com", autocomplete: "username" });
  const passIn = el("input", { type: "password", placeholder: needsSetup ? "choose a strong password (10+ chars)" : "password", autocomplete: "current-password" });
  const nameIn = needsSetup ? el("input", { type: "text", placeholder: "display name (optional)" }) : null;
  const btn = el("button", { class: "btn primary block" }, needsSetup ? "Create admin account" : "Sign in");

  const submit = async () => {
    btn.disabled = true;
    try {
      const path = needsSetup ? "/auth/setup" : "/auth/login";
      const body = needsSetup
        ? { email: emailIn.value.trim(), password: passIn.value, display_name: nameIn.value.trim() }
        : { email: emailIn.value.trim(), password: passIn.value };
      const r = await api(path, { body });
      setToken(r.access_token);
      toast(needsSetup ? "Admin account created" : "Welcome back");
      await boot();
    } catch (e) { fail(e); btn.disabled = false; }
  };
  btn.addEventListener("click", submit);
  [emailIn, passIn, nameIn].forEach((n) => n && n.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); }));

  clear(app).append(el("div", { class: "login-wrap" },
    el("div", { class: "panel login-card" },
      el("div", { class: "brand" }, el("h1", {}, "Φ Phosphor"), el("div", { class: "tag" }, "mail server · cold outreach")),
      el("div", { class: "panel-body" },
        el("p", { class: "muted small mb" }, needsSetup
          ? "First run — create the operator account that manages this server."
          : "Sign in to the control panel."),
        el("label", { class: "field" }, el("span", {}, "Email"), emailIn),
        nameIn && el("label", { class: "field" }, el("span", {}, "Name"), nameIn),
        el("label", { class: "field" }, el("span", {}, "Password"), passIn),
        btn,
      ),
    ),
  ));
}

/* ── shell ──────────────────────────────────────────────────────────────── */
const NAV = [
  { group: "Outreach", items: [
    { id: "dashboard", label: "Dashboard", ico: "▚" },
    { id: "campaigns", label: "Campaigns", ico: "➤" },
    { id: "contacts", label: "Contacts", ico: "☰" },
    { id: "lists", label: "Lists", ico: "❖" },
    { id: "templates", label: "Templates", ico: "✎" },
    { id: "suppressions", label: "Suppression", ico: "⊘" },
  ]},
  { group: "Mail server", items: [
    { id: "inbox", label: "Inbox", ico: "✉" },
    { id: "domains", label: "Domains", ico: "◈" },
    { id: "mailboxes", label: "Mailboxes", ico: "⊞" },
    { id: "settings", label: "Settings", ico: "⚙" },
  ]},
];

function shell() {
  const nav = el("nav", { class: "nav" });
  for (const g of NAV) {
    nav.append(el("div", { class: "nav-group-label" }, g.group));
    for (const it of g.items) {
      nav.append(el("a", { href: "#/" + it.id, "data-nav": it.id },
        el("span", { class: "ico" }, it.ico), el("span", {}, it.label),
        el("span", { class: "badge hidden", "data-badge": it.id })));
    }
  }
  return el("div", { id: "app" },
    el("aside", { class: "sidebar" },
      el("div", { class: "brand" }, el("h1", {}, "Φ Phosphor"), el("div", { class: "tag" }, "control panel")),
      nav,
      el("div", { class: "sidebar-foot" },
        el("span", {}, ME ? ME.email : ""),
        el("button", { onclick: logout }, "logout")),
    ),
    el("main", { class: "main" },
      el("div", { class: "topbar" },
        el("h2", { id: "view-title" }, "Dashboard"),
        el("span", { class: "crumbs", id: "view-crumbs" }, ""),
        el("span", { class: "spacer" }),
        el("span", { class: "small muted", id: "queue-info" }, ""),
        el("span", { class: "status-dot", id: "health-dot", title: "server ok" }),
      ),
      el("div", { class: "view", id: "view" }, el("div", { class: "loader" }, "loading")),
    ),
  );
}

function setActiveNav(id) {
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === id));
}
function setTitle(t, crumbs = "") { $("#view-title").textContent = t; $("#view-crumbs").textContent = crumbs; }
function viewNode() { return clear($("#view")); }

/* ═══════════════════════════════════════════════════════════════════════════
   VIEWS
   ═══════════════════════════════════════════════════════════════════════════ */
const routes = {};

/* ---- Dashboard --------------------------------------------------------- */
routes.dashboard = async () => {
  setActiveNav("dashboard"); setTitle("Dashboard");
  const v = viewNode();
  const s = await api("/dashboard/stats");
  const card = (k, val, sub, plain) => el("div", { class: "stat" + (plain ? " plain" : "") },
    el("div", { class: "k" }, k), el("div", { class: "v" }, fmtInt(val)), sub && el("div", { class: "sub" }, sub));

  v.append(
    el("div", { class: "cards" },
      card("Campaigns running", s.campaigns_running, "active sequences"),
      card("Queued to send", s.outbound_queued, "in the outbound queue"),
      card("Sent · 24h", s.outbound_sent_24h),
      card("Replies · 7d", s.replies_7d, "across all campaigns"),
      card("Failed · 24h", s.outbound_failed_24h, "hard bounces / errors", true),
      card("Inbound · 24h", s.inbound_24h, "messages received", true),
      card("Contacts", s.contacts, null, true),
      card("Suppressed", s.suppressed, "do-not-contact", true),
    ),
    el("div", { class: "grid mt", style: "grid-template-columns: 1fr 1fr;" },
      el("div", { class: "panel" },
        el("div", { class: "panel-head" }, el("h3", {}, "Infrastructure")),
        el("div", { class: "panel-body" },
          el("div", { class: "spread mb" }, el("span", { class: "muted" }, "Domains configured"),
            el("b", {}, fmtInt(s.domains))),
          el("div", { class: "spread mb" }, el("span", { class: "muted" }, "Mailboxes"),
            el("b", {}, fmtInt(s.mailboxes))),
          el("div", { class: "row mt" },
            el("a", { class: "btn sm", href: "#/domains" }, "Manage domains"),
            el("a", { class: "btn sm", href: "#/mailboxes" }, "Manage mailboxes")),
        )),
      el("div", { class: "panel" },
        el("div", { class: "panel-head" }, el("h3", {}, "Recent activity")),
        el("div", { class: "panel-body tight" }, el("div", { id: "activity", class: "loader" }, "loading")),
      ),
    ),
  );

  const acts = await api("/dashboard/activity?limit=30");
  const box = clear($("#activity"));
  if (!acts.length) { box.append(el("div", { class: "t-empty" }, "no events yet")); return; }
  const tbl = el("table", {}, el("tbody", {}, ...acts.map((a) =>
    el("tr", {}, el("td", {}, statusPill(a.type)),
      el("td", { class: "muted small" }, a.step ? "step " + a.step : ""),
      el("td", { class: "muted small mono truncate", style: "max-width:180px" }, a.url || ""),
      el("td", { class: "muted small nowrap", style: "text-align:right" }, fmtDate(a.at))))));
  box.append(tbl);
};

/* ---- Domains --------------------------------------------------------- */
routes.domains = async () => {
  setActiveNav("domains"); setTitle("Domains", "mail routing + DNS");
  const v = viewNode();
  const domains = await api("/domains");
  STATE.domains = domains;

  const addBtn = el("button", { class: "btn primary", onclick: () => domainModal() }, "+ Add domain");
  v.append(el("div", { class: "spread mb" }, el("div", { class: "section-title" }, `${domains.length} domain(s)`), addBtn));

  if (!domains.length) {
    v.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "◈"),
      el("p", {}, "No domains yet. Add the domain you'll send and receive mail on.")));
    return;
  }

  for (const d of domains) {
    const st = d.dns_status || {};
    const keys = ["mx", "spf", "dkim", "dmarc", "ptr"];
    const allOk = keys.filter(k => k !== "ptr").every((k) => st[k] === true);
    v.append(el("div", { class: "panel mb" },
      el("div", { class: "panel-head" },
        el("div", {}, el("h3", {}, d.name), el("div", { class: "small muted" }, d.dns_last_checked_at ? "checked " + fmtDate(d.dns_last_checked_at) : "not verified yet")),
        el("div", { class: "row" },
          allOk ? pill("dns ok", "ok dot") : pill("dns incomplete", "warn dot"),
          el("button", { class: "btn sm", onclick: () => domainDNS(d.id) }, "DNS setup"),
          el("button", { class: "btn sm ghost", onclick: () => domainMenu(d) }, "⋯"),
        )),
      el("div", { class: "panel-body" },
        el("div", { class: "row wrap" }, ...keys.map((k) =>
          pill(k.toUpperCase(), st[k] === true ? "ok" : (st[k] === false ? "bad" : "")))),
      ),
    ));
  }
};

function domainModal() {
  const name = el("input", { placeholder: "example.com" });
  const sel = el("input", { value: "phosphor" });
  modal({
    title: "Add domain",
    body: el("div", {},
      el("label", { class: "field" }, el("span", {}, "Domain name"), name),
      el("label", { class: "field" }, el("span", {}, "DKIM selector"), sel),
      el("p", { class: "hint" }, "Phosphor generates a 2048-bit DKIM key immediately. You'll get copy-paste DNS records next."),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        try { const d = await api("/domains", { body: { name: name.value.trim(), dkim_selector: sel.value.trim() || "phosphor" } });
          close(); toast("Domain added"); routes.domains(); setTimeout(() => domainDNS(d.id), 300);
        } catch (e) { fail(e); }
      }}, "Add domain"),
    ],
  });
}

function domainMenu(d) {
  modal({
    title: d.name, body: el("div", { class: "stack" },
      el("button", { class: "btn block", onclick: () => { close1(); domainDNS(d.id); } }, "View DNS records"),
      el("button", { class: "btn block", onclick: () => { close1(); confirmDialog("Rotate the DKIM key? You must republish the DKIM TXT record afterwards or outgoing mail will fail authentication.", async () => { await api(`/domains/${d.id}/rotate-dkim`, { method: "POST" }); toast("DKIM key rotated"); routes.domains(); }, { yes: "Rotate key" }); } }, "Rotate DKIM key"),
      el("button", { class: "btn block danger", onclick: () => { close1(); confirmDialog(`Delete ${d.name} and all its mailboxes? This cannot be undone.`, async () => { await api(`/domains/${d.id}`, { method: "DELETE" }); toast("Domain deleted"); routes.domains(); }); } }, "Delete domain"),
    ),
  });
  const close1 = () => clear($("#modal-root"));
}

async function domainDNS(id) {
  setTitle("Domain DNS", "publish these records");
  const v = viewNode();
  v.append(el("div", { class: "row mb" },
    el("a", { class: "btn sm ghost", href: "#/domains" }, "← domains"),
    el("button", { class: "btn sm", id: "verify-btn" }, "↻ Verify now")));
  const host = el("div", { id: "dns-host" }, el("div", { class: "loader" }, "loading records"));
  v.append(host);

  async function load(verify) {
    const data = await api(`/domains/${id}/dns` + (verify ? "?verify=true" : ""));
    const box = clear($("#dns-host"));
    box.append(el("div", { class: "panel" },
      el("div", { class: "panel-head" }, el("h3", {}, data.domain + " — required records"),
        el("span", { class: "small muted" }, "add these at your DNS provider")),
      el("div", { class: "panel-body" }, ...data.records.map(dnsRecordCard)),
    ));
  }
  $("#verify-btn").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "checking…";
    try { await load(true); toast("DNS re-checked"); } catch (err) { fail(err); }
    e.target.disabled = false; e.target.textContent = "↻ Verify now";
  });
  await load(false);
}

function dnsRecordCard(r) {
  const okPill = r.ok === true ? pill("verified", "ok dot")
    : r.ok === false ? pill("not found", "bad dot")
    : pill(r.required ? "required" : "optional", r.required ? "" : "info");
  const copy = el("button", { class: "btn sm ghost copy-btn", onclick: () => {
    navigator.clipboard.writeText(r.value); toast("Copied value");
  }}, "copy");
  return el("div", { class: "dns-rec" },
    el("div", { class: "spread" }, el("b", { class: "mono" }, r.kind.toUpperCase()), okPill),
    el("div", { class: "kv" },
      el("b", {}, "Type"), el("span", { class: "mono" }, r.type + (r.priority != null ? `  (priority ${r.priority})` : "")),
      el("b", {}, "Host"), el("span", { class: "mono" }, r.host)),
    el("div", { class: "spread", style: "margin-bottom:6px" }, el("b", { class: "small muted" }, "Value"), copy),
    el("code", {}, r.value),
    r.detail && el("div", { class: "hint", style: r.ok === false ? "color:var(--red)" : "" }, r.detail),
  );
}

/* ---- Mailboxes ------------------------------------------------------- */
routes.mailboxes = async () => {
  setActiveNav("mailboxes"); setTitle("Mailboxes", "accounts + aliases");
  const v = viewNode();
  const [domains, mailboxes, aliases] = await Promise.all([api("/domains"), api("/mailboxes"), api("/aliases")]);
  STATE.domains = domains; STATE.mailboxes = mailboxes;

  v.append(el("div", { class: "spread mb" }, el("div", { class: "section-title" }, `${mailboxes.length} mailbox(es)`),
    el("div", { class: "row" },
      el("button", { class: "btn", onclick: () => aliasModal(domains) }, "+ Alias"),
      el("button", { class: "btn primary", onclick: () => mailboxModal(domains) }, "+ Mailbox"))));

  if (!domains.length) { v.append(el("div", { class: "empty-state" }, "Add a domain first.")); return; }

  v.append(el("div", { class: "panel" }, el("div", { class: "panel-body tight" },
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Address"), el("th", {}, "Name"), el("th", {}, "Quota"), el("th", {}, "Status"), el("th", {}, ""))),
      el("tbody", {}, ...mailboxes.map((m) => el("tr", {},
        el("td", { class: "mono" }, m.address),
        el("td", {}, m.display_name || el("span", { class: "muted" }, "—")),
        el("td", { class: "muted small" }, fmtBytes(m.quota_bytes)),
        el("td", {}, m.active ? pill("active", "ok") : pill("disabled", "bad")),
        el("td", { style: "text-align:right" },
          el("button", { class: "btn sm ghost", onclick: () => mailboxMenu(m) }, "⋯"))))),
    ))));

  if (aliases.length) {
    v.append(el("div", { class: "panel mt" },
      el("div", { class: "panel-head" }, el("h3", {}, "Forwarding aliases")),
      el("div", { class: "panel-body tight" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "From"), el("th", {}, "Forwards to"), el("th", {}, ""))),
        el("tbody", {}, ...aliases.map((a) => el("tr", {},
          el("td", { class: "mono" }, a.source), el("td", { class: "mono muted" }, a.destination),
          el("td", { style: "text-align:right" }, el("button", { class: "btn sm ghost danger", onclick: () =>
            confirmDialog("Delete this alias?", async () => { await api(`/aliases/${a.id}`, { method: "DELETE" }); toast("Alias deleted"); routes.mailboxes(); }) }, "delete")))))),
      )));
  }
};

function mailboxModal(domains) {
  const local = el("input", { placeholder: "sales" });
  const dom = el("select", {}, ...domains.map((d) => el("option", { value: d.id }, "@" + d.name)));
  const name = el("input", { placeholder: "Sales Team" });
  const pass = el("input", { type: "password", placeholder: "mailbox password (SMTP auth)" });
  const quota = el("input", { type: "number", value: "2048" });
  modal({
    title: "New mailbox", body: el("div", {},
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "Local part"), local),
        el("label", { class: "field" }, el("span", {}, "Domain"), dom)),
      el("label", { class: "field" }, el("span", {}, "Display name"), name),
      el("label", { class: "field" }, el("span", {}, "Password"), pass),
      el("label", { class: "field" }, el("span", {}, "Quota (MB)"), quota),
      el("p", { class: "hint" }, "Use these credentials for SMTP submission on port 587 and as a campaign sending identity."),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        try {
          await api("/mailboxes", { body: {
            domain_id: +dom.value, local_part: local.value.trim(), password: pass.value,
            display_name: name.value.trim(), quota_mb: +quota.value || 2048 } });
          close(); toast("Mailbox created"); routes.mailboxes();
        } catch (e) { fail(e); }
      }}, "Create"),
    ],
  });
}

function mailboxMenu(m) {
  const close1 = () => clear($("#modal-root"));
  modal({ title: m.address, body: el("div", { class: "stack" },
    el("button", { class: "btn block", onclick: () => { close1(); resetPwModal(m); } }, "Reset password"),
    el("button", { class: "btn block", onclick: async () => { close1(); try { await api(`/mailboxes/${m.id}?active=${!m.active}`, { method: "PUT" }); toast("Updated"); routes.mailboxes(); } catch (e) { fail(e); } } }, m.active ? "Disable" : "Enable"),
    el("button", { class: "btn block danger", onclick: () => { close1(); confirmDialog(`Delete ${m.address}? Stored mail is removed.`, async () => { await api(`/mailboxes/${m.id}`, { method: "DELETE" }); toast("Deleted"); routes.mailboxes(); }); } }, "Delete mailbox"),
  )});
}
function resetPwModal(m) {
  const pw = el("input", { type: "password", placeholder: "new password" });
  modal({ title: "Reset password — " + m.address, body: el("label", { class: "field" }, el("span", {}, "New password"), pw),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { try { await api(`/mailboxes/${m.id}/password?password=${encodeURIComponent(pw.value)}`, { method: "PUT" }); close(); toast("Password changed"); } catch (e) { fail(e); } } }, "Save")]});
}
function aliasModal(domains) {
  const src = el("input", { placeholder: "info" });
  const dom = el("select", {}, ...domains.map((d) => el("option", { value: d.id }, "@" + d.name)));
  const dst = el("input", { placeholder: "someone@elsewhere.com" });
  modal({ title: "New alias", body: el("div", {},
    el("div", { class: "field-row" }, el("label", { class: "field" }, el("span", {}, "From (local part)"), src),
      el("label", { class: "field" }, el("span", {}, "On domain"), dom)),
    el("label", { class: "field" }, el("span", {}, "Forward to"), dst),
    el("p", { class: "hint" }, "Use “@domain” as the source for a catch-all forward.")),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { try {
        await api("/aliases", { body: { domain_id: +dom.value, source: src.value.trim(), destination: dst.value.trim() } });
        close(); toast("Alias added"); routes.mailboxes(); } catch (e) { fail(e); } } }, "Add")]});
}

/* ---- Contacts ------------------------------------------------------- */
routes.contacts = async () => {
  setActiveNav("contacts"); setTitle("Contacts");
  const v = viewNode();
  const search = el("input", { placeholder: "search name / email / company", style: "max-width:280px" });
  v.append(el("div", { class: "spread mb" }, search, el("div", { class: "row" },
    el("button", { class: "btn", onclick: importModal }, "⇪ Import CSV"),
    el("button", { class: "btn primary", onclick: () => contactModal() }, "+ Contact"))));
  const host = el("div", { id: "contacts-host" });
  v.append(host);

  const load = async () => {
    const q = search.value.trim();
    const rows = await api("/contacts?limit=300" + (q ? "&q=" + encodeURIComponent(q) : ""));
    const box = clear(host);
    if (!rows.length) { box.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "☰"), el("p", {}, "No contacts. Import a CSV to get started."))); return; }
    box.append(el("div", { class: "panel" }, el("div", { class: "panel-body tight" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Email"), el("th", {}, "Name"), el("th", {}, "Company"), el("th", {}, "Status"), el("th", {}, "Added"), el("th", {}, ""))),
      el("tbody", {}, ...rows.map((c) => el("tr", { class: "clickable", onclick: () => contactModal(c) },
        el("td", { class: "mono" }, c.email),
        el("td", {}, `${c.first_name} ${c.last_name}`.trim() || el("span", { class: "muted" }, "—")),
        el("td", { class: "muted" }, c.company || "—"),
        el("td", {}, statusPill(c.status)),
        el("td", { class: "muted small nowrap" }, fmtDate(c.created_at)),
        el("td", { style: "text-align:right" }, el("button", { class: "btn sm ghost danger", onclick: (e) => { e.stopPropagation(); confirmDialog(`Delete ${c.email}?`, async () => { await api(`/contacts/${c.id}`, { method: "DELETE" }); toast("Deleted"); load(); }); } }, "×")))
      )),
    ))));
  };
  let t; search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  await load();
};

function contactModal(c = null) {
  const f = {};
  const mk = (key, label, ph = "") => { f[key] = el("input", { value: c ? (c[key] || "") : "", placeholder: ph }); return el("label", { class: "field" }, el("span", {}, label), f[key]); };
  const emailInput = el("input", { value: c ? c.email : "", placeholder: "person@company.com", disabled: !!c });
  const notes = el("textarea", { placeholder: "notes" }, c ? (c.notes || "") : "");
  modal({
    title: c ? "Edit contact" : "New contact", wide: true,
    body: el("div", {},
      el("label", { class: "field" }, el("span", {}, "Email"), emailInput),
      el("div", { class: "field-row" }, mk("first_name", "First name"), mk("last_name", "Last name")),
      el("div", { class: "field-row" }, mk("company", "Company"), mk("title", "Title")),
      el("div", { class: "field-row" }, mk("phone", "Phone"), mk("website", "Website")),
      el("label", { class: "field" }, el("span", {}, "Notes"), notes),
      c && el("p", { class: "hint" }, "Status: " + c.status + (c.custom && Object.keys(c.custom).length ? " · custom fields: " + Object.keys(c.custom).join(", ") : "")),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        const body = Object.fromEntries(Object.entries(f).map(([k, n]) => [k, n.value.trim()]));
        body.notes = notes.value.trim();
        try {
          if (c) await api(`/contacts/${c.id}`, { method: "PUT", body });
          else await api("/contacts", { body: { ...body, email: emailInput.value.trim() } });
          close(); toast("Saved"); routes.contacts();
        } catch (e) { fail(e); }
      }}, "Save"),
    ],
  });
}

function importModal() {
  const file = el("input", { type: "file", accept: ".csv,text/csv" });
  const listName = el("input", { placeholder: "optional — add all rows to this list" });
  modal({
    title: "Import contacts from CSV",
    body: el("div", {},
      el("label", { class: "field" }, el("span", {}, "CSV file"), file),
      el("label", { class: "field" }, el("span", {}, "Add to list"), listName),
      el("p", { class: "hint", html: "Needs an <b>email</b> column. Common headers (first name, company, title, website…) are auto-mapped; anything else is stored as a custom field you can use as a merge tag." }),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        if (!file.files[0]) return toast("Choose a file", "warn");
        const fd = new FormData(); fd.append("file", file.files[0]);
        const qs = listName.value.trim() ? "?list_name=" + encodeURIComponent(listName.value.trim()) : "";
        try {
          const r = await api("/contacts/import" + qs, { method: "POST", form: fd });
          close();
          toast(`Imported: ${r.created} new, ${r.updated} updated, ${r.skipped} skipped`);
          if (r.errors && r.errors.length) console.warn("import errors", r.errors);
          routes.contacts();
        } catch (e) { fail(e); }
      }}, "Import"),
    ],
  });
}

/* ---- Lists --------------------------------------------------------- */
routes.lists = async () => {
  setActiveNav("lists"); setTitle("Lists", "audience segments");
  const v = viewNode();
  const lists = await api("/lists");
  STATE.lists = lists;
  v.append(el("div", { class: "spread mb" }, el("div", { class: "section-title" }, `${lists.length} list(s)`),
    el("button", { class: "btn primary", onclick: listModal }, "+ New list")));
  if (!lists.length) { v.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "❖"), el("p", {}, "Create a list, then import contacts into it or add them from the Contacts tab."))); return; }
  v.append(el("div", { class: "cards" }, ...lists.map((l) => el("div", { class: "panel" },
    el("div", { class: "panel-body" },
      el("div", { class: "spread" }, el("b", {}, l.name), pill(fmtInt(l.member_count) + " members", "info")),
      l.description && el("p", { class: "muted small mt" }, l.description),
      el("div", { class: "row mt" },
        el("a", { class: "btn sm", href: "#/contacts" }, "view contacts"),
        el("button", { class: "btn sm ghost danger", onclick: () => confirmDialog(`Delete list “${l.name}”? Contacts are kept.`, async () => { await api(`/lists/${l.id}`, { method: "DELETE" }); toast("List deleted"); routes.lists(); }) }, "delete")),
    )))));
};
function listModal() {
  const name = el("input", { placeholder: "Q3 SaaS founders" });
  const desc = el("input", { placeholder: "description (optional)" });
  modal({ title: "New list", body: el("div", {},
    el("label", { class: "field" }, el("span", {}, "Name"), name),
    el("label", { class: "field" }, el("span", {}, "Description"), desc)),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { try { await api("/lists", { body: { name: name.value.trim(), description: desc.value.trim() } }); close(); toast("List created"); routes.lists(); } catch (e) { fail(e); } } }, "Create")]});
}

/* ---- Templates ---------------------------------------------------- */
routes.templates = async () => {
  setActiveNav("templates"); setTitle("Templates");
  const v = viewNode();
  const tpls = await api("/templates");
  STATE.templates = tpls;
  v.append(el("div", { class: "spread mb" }, el("div", { class: "section-title" }, `${tpls.length} template(s)`),
    el("button", { class: "btn primary", onclick: () => templateEditor() }, "+ New template")));
  if (!tpls.length) { v.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "✎"), el("p", {}, "Templates hold reusable subject + body copy with {{merge}} tags."))); return; }
  v.append(el("div", { class: "panel" }, el("div", { class: "panel-body tight" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", {}, "Subject"), el("th", {}, "Updated"), el("th", {}, ""))),
    el("tbody", {}, ...tpls.map((t) => el("tr", { class: "clickable", onclick: () => templateEditor(t) },
      el("td", {}, t.name), el("td", { class: "muted truncate", style: "max-width:360px" }, t.subject || "—"),
      el("td", { class: "muted small nowrap" }, fmtDate(t.updated_at)),
      el("td", { style: "text-align:right" }, el("button", { class: "btn sm ghost danger", onclick: (e) => { e.stopPropagation(); confirmDialog(`Delete “${t.name}”?`, async () => { await api(`/templates/${t.id}`, { method: "DELETE" }); toast("Deleted"); routes.templates(); }); } }, "×")))))))));
};

function templateEditor(t = null) {
  const name = el("input", { value: t ? t.name : "", placeholder: "Template name" });
  const subject = el("input", { value: t ? t.subject : "", placeholder: "Quick question about {{company}}" });
  const bodyHtml = el("textarea", { style: "min-height:200px", placeholder: "<p>Hi {{first_name|there}},</p>" }, t ? t.body_html : "");
  const preview = el("div", { class: "panel", style: "background:var(--bg-1)" });
  const doPreview = async () => {
    try {
      const r = await api("/templates/preview", { body: { subject: subject.value, body_html: bodyHtml.value, body_text: "" } });
      clear(preview).append(
        el("div", { class: "panel-head" }, el("h3", {}, "Preview (sample contact)"),
          r.missing_tags.length ? pill(r.missing_tags.length + " unfilled tag(s)", "warn") : pill("all tags resolve", "ok")),
        el("div", { class: "panel-body" },
          el("div", { class: "small muted mb" }, "Subject: " + esc(r.subject)),
          el("div", { style: "background:#fff;color:#111;padding:14px;border-radius:8px", html: r.body_html || "<em>(empty)</em>" })),
      );
    } catch (e) { fail(e); }
  };
  bodyHtml.addEventListener("input", () => { clearTimeout(templateEditor._t); templateEditor._t = setTimeout(doPreview, 400); });
  subject.addEventListener("input", () => { clearTimeout(templateEditor._t); templateEditor._t = setTimeout(doPreview, 400); });

  modal({
    title: t ? "Edit template" : "New template", wide: true,
    body: el("div", {},
      el("label", { class: "field" }, el("span", {}, "Name"), name),
      el("label", { class: "field" }, el("span", {}, "Subject"), subject),
      el("label", { class: "field" }, el("span", {}, "Body (HTML)"), bodyHtml),
      el("p", { class: "hint mb", html: "Merge tags: <span class='kbd'>{{first_name}}</span> <span class='kbd'>{{first_name|there}}</span> <span class='kbd'>{{company}}</span> &nbsp;·&nbsp; spintax: <span class='kbd'>{quick|brief} note</span>" }),
      preview,
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        const body = { name: name.value.trim(), subject: subject.value, body_html: bodyHtml.value, body_text: "" };
        try { if (t) await api(`/templates/${t.id}`, { method: "PUT", body }); else await api("/templates", { body });
          close(); toast("Saved"); routes.templates(); } catch (e) { fail(e); }
      }}, "Save template"),
    ],
  });
  setTimeout(doPreview, 100);
}

/* ---- Suppressions ------------------------------------------------- */
routes.suppressions = async () => {
  setActiveNav("suppressions"); setTitle("Suppression list", "global do-not-contact");
  const v = viewNode();
  const search = el("input", { placeholder: "search email", style: "max-width:260px" });
  v.append(el("div", { class: "spread mb" }, search, el("div", { class: "row" },
    el("a", { class: "btn sm ghost", href: "/api/suppressions/export", target: "_blank" }, "export"),
    el("button", { class: "btn", onclick: suppImportModal }, "⇪ Import"),
    el("button", { class: "btn primary", onclick: suppAddModal }, "+ Add"))));
  const host = el("div", { id: "supp-host" });
  v.append(host);
  const load = async () => {
    const rows = await api("/suppressions?limit=500" + (search.value.trim() ? "&q=" + encodeURIComponent(search.value.trim()) : ""));
    const box = clear(host);
    if (!rows.length) { box.append(el("div", { class: "empty-state" }, "Nothing suppressed. Unsubscribes and hard bounces land here automatically.")); return; }
    box.append(el("div", { class: "panel" }, el("div", { class: "panel-body tight" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Email"), el("th", {}, "Reason"), el("th", {}, "When"), el("th", {}, ""))),
      el("tbody", {}, ...rows.map((s) => el("tr", {},
        el("td", { class: "mono" }, s.email), el("td", {}, statusPill(s.reason)),
        el("td", { class: "muted small nowrap" }, fmtDate(s.created_at)),
        el("td", { style: "text-align:right" }, el("button", { class: "btn sm ghost", onclick: () => confirmDialog(`Remove ${s.email} from suppression? They can be contacted again.`, async () => { await api(`/suppressions/${encodeURIComponent(s.email)}`, { method: "DELETE" }); toast("Removed"); load(); }, { danger: false, yes: "Remove" }) }, "unsuppress")))))))));
  };
  let t; search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  await load();
};
function suppAddModal() {
  const email = el("input", { placeholder: "person@company.com" });
  modal({ title: "Add to suppression", body: el("label", { class: "field" }, el("span", {}, "Email"), email),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { try { await api("/suppressions", { body: { email: email.value.trim(), reason: "manual" } }); close(); toast("Added"); routes.suppressions(); } catch (e) { fail(e); } } }, "Add")]});
}
function suppImportModal() {
  const file = el("input", { type: "file", accept: ".csv,.txt" });
  modal({ title: "Import suppression list", body: el("div", {}, el("label", { class: "field" }, el("span", {}, "File (one email per line)"), file)),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { if (!file.files[0]) return; const fd = new FormData(); fd.append("file", file.files[0]); try { const r = await api("/suppressions/import", { method: "POST", form: fd }); close(); toast(`Added ${r.added}`); routes.suppressions(); } catch (e) { fail(e); } } }, "Import")]});
}

/* ---- Campaigns (list) ------------------------------------------- */
routes.campaigns = async () => {
  setActiveNav("campaigns"); setTitle("Campaigns");
  const v = viewNode();
  const [campaigns] = await Promise.all([api("/campaigns")]);
  v.append(el("div", { class: "spread mb" }, el("div", { class: "section-title" }, `${campaigns.length} campaign(s)`),
    el("button", { class: "btn primary", onclick: campaignCreateModal }, "+ New campaign")));
  if (!campaigns.length) { v.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "➤"), el("p", {}, "No campaigns yet. A campaign = a contact list + an email sequence + sending rules."))); return; }
  const grid = el("div", { class: "stack" });
  v.append(grid);
  for (const c of campaigns) {
    const stat = await api(`/campaigns/${c.id}/stats`).catch(() => null);
    grid.append(el("div", { class: "panel", style: "cursor:pointer", onclick: () => { location.hash = "#/campaign/" + c.id; } },
      el("div", { class: "panel-head" },
        el("div", {}, el("h3", {}, c.name), el("div", { class: "small muted" }, "created " + fmtDate(c.created_at))),
        statusPill(c.status)),
      stat && el("div", { class: "panel-body" }, el("div", { class: "row wrap", style: "gap:26px" },
        miniStat("recipients", stat.recipients_total),
        miniStat("sent", stat.sent),
        miniStat("open rate", stat.open_rate + "%"),
        miniStat("reply rate", stat.reply_rate + "%"),
        miniStat("bounces", stat.bounces),
      )),
    ));
  }
};
function miniStat(k, v) { return el("div", {}, el("div", { class: "mono", style: "font-size:20px;color:var(--green)" }, String(v)), el("div", { class: "small muted" }, k)); }

function campaignCreateModal() {
  const name = el("input", { placeholder: "Q3 outbound — SaaS founders" });
  modal({ title: "New campaign", body: el("div", {},
    el("label", { class: "field" }, el("span", {}, "Campaign name"), name),
    el("p", { class: "hint" }, "You'll pick the list, sender, sequence and sending window on the next screen.")),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { try { const c = await api("/campaigns", { body: { name: name.value.trim() } }); close(); location.hash = "#/campaign/" + c.id; } catch (e) { fail(e); } } }, "Create")]});
}

/* ---- Campaign detail ------------------------------------------- */
routes.campaign = async (id) => {
  setActiveNav("campaigns");
  const v = viewNode();
  v.append(el("div", { class: "loader" }, "loading campaign"));
  const [c, steps, stats, win, mailboxes, lists] = await Promise.all([
    api(`/campaigns/${id}`), api(`/campaigns/${id}/steps`), api(`/campaigns/${id}/stats`),
    api(`/campaigns/${id}/window`), api("/mailboxes"), api("/lists"),
  ]);
  STATE.mailboxes = mailboxes; STATE.lists = lists;
  setTitle(c.name, "campaign #" + id);
  clear(v);

  /* header / controls */
  const controls = el("div", { class: "row" });
  const refresh = () => routes.campaign(id);
  if (c.status === "draft" || c.status === "paused" || c.status === "scheduled") {
    controls.append(el("button", { class: "btn primary", onclick: async () => {
      try { await api(`/campaigns/${id}/start`, { method: "POST" }); toast("Campaign started"); refresh(); } catch (e) { fail(e); } } }, "▶ Start sending"));
  }
  if (c.status === "running") {
    controls.append(el("button", { class: "btn", onclick: async () => { await api(`/campaigns/${id}/pause`, { method: "POST" }); toast("Paused"); refresh(); } }, "❚❚ Pause"));
  }
  controls.append(el("button", { class: "btn ghost", onclick: () => testSendModal(id, steps) }, "✉ Test send"));
  controls.append(el("a", { class: "btn ghost", href: "#/campaigns" }, "← all"));

  v.append(el("div", { class: "spread mb" }, el("div", { class: "row" }, statusPill(c.status),
    win.in_window_now ? pill("in sending window", "ok dot") : pill("outside window · next " + fmtDate(win.next_window_open), "warn dot")), controls));

  /* stats strip */
  v.append(el("div", { class: "cards mb" },
    stCard("Recipients", stats.recipients_total, `${stats.recipients_active} active · ${stats.recipients_completed} done`),
    stCard("Sent", stats.sent, `${stats.delivered} delivered`),
    stCard("Open rate", stats.open_rate + "%", `${stats.unique_opens} unique`),
    stCard("Click rate", stats.click_rate + "%", `${stats.unique_clicks} unique`),
    stCard("Reply rate", stats.reply_rate + "%", `${stats.replies} replies`),
    stCard("Bounce rate", stats.bounce_rate + "%", `${stats.bounces} bounced`, true),
  ));

  /* two columns: settings + sequence */
  const settingsPanel = campaignSettingsPanel(c, mailboxes, lists, refresh);
  const seqPanel = sequencePanel(c, steps, refresh);
  v.append(el("div", { class: "grid", style: "grid-template-columns: 340px 1fr" }, settingsPanel, seqPanel));

  /* recipients + timeline */
  const rHost = el("div", { class: "panel mt" }, el("div", { class: "panel-head" }, el("h3", {}, "Recipients"),
    el("button", { class: "btn sm ghost", onclick: async () => { const r = await api(`/campaigns/${id}/enroll`, { method: "POST" }); toast(`+${r.added} enrolled (${r.recipients_total} total)`); refresh(); } }, "↻ sync from list")),
    el("div", { class: "panel-body tight", id: "rcpt-host" }, el("div", { class: "loader" }, "loading")));
  v.append(rHost);
  const rcpts = await api(`/campaigns/${id}/recipients?limit=200`);
  const rb = clear($("#rcpt-host"));
  if (!rcpts.length) rb.append(el("div", { class: "t-empty" }, "No recipients. Assign a list in settings, then “sync from list”."));
  else rb.append(el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Email"), el("th", {}, "Name"), el("th", {}, "Company"), el("th", {}, "Step"), el("th", {}, "Status"), el("th", {}, "Next"))),
    el("tbody", {}, ...rcpts.map((r) => el("tr", {}, el("td", { class: "mono" }, r.email), el("td", {}, r.name || "—"),
      el("td", { class: "muted" }, r.company || "—"), el("td", { class: "mono" }, r.current_step || "—"),
      el("td", {}, statusPill(r.status)), el("td", { class: "muted small nowrap" }, r.next_action_at ? fmtDate(r.next_action_at) : "—"))))));

  const tl = await api(`/campaigns/${id}/timeline?limit=60`);
  if (tl.length) {
    v.append(el("div", { class: "panel mt" }, el("div", { class: "panel-head" }, el("h3", {}, "Event timeline")),
      el("div", { class: "panel-body tight" }, el("table", {}, el("tbody", {}, ...tl.map((e) => el("tr", {},
        el("td", {}, statusPill(e.type)), el("td", { class: "mono small" }, e.email || "—"),
        el("td", { class: "muted small" }, e.step ? "step " + e.step : ""),
        el("td", { class: "muted small truncate", style: "max-width:220px" }, e.url || ""),
        el("td", { class: "muted small nowrap", style: "text-align:right" }, fmtDate(e.at)))))))));
  }
};
function stCard(k, v, sub, plain) { return el("div", { class: "stat" + (plain ? " plain" : "") }, el("div", { class: "k" }, k), el("div", { class: "v" }, String(v)), sub && el("div", { class: "sub" }, sub)); }

function campaignSettingsPanel(c, mailboxes, lists, refresh) {
  const mb = el("select", {}, el("option", { value: "" }, "— choose sending mailbox —"),
    ...mailboxes.map((m) => el("option", { value: m.id, selected: m.id === c.from_mailbox_id }, m.address)));
  const list = el("select", {}, el("option", { value: "" }, "— choose contact list —"),
    ...lists.map((l) => el("option", { value: l.id, selected: l.id === c.list_id }, `${l.name} (${l.member_count})`)));
  const replyTo = el("input", { value: c.reply_to || "", placeholder: "reply-to (optional)" });
  const tz = el("input", { value: c.timezone || "UTC" });
  const startH = el("input", { type: "number", min: 0, max: 23, value: c.window_start_hour });
  const endH = el("input", { type: "number", min: 0, max: 23, value: c.window_end_hour });
  const cap = el("input", { type: "number", min: 1, value: c.daily_cap });
  const dMin = el("input", { type: "number", min: 0, value: c.min_delay_seconds });
  const dMax = el("input", { type: "number", min: 0, value: c.max_delay_seconds });
  const days = [0,1,2,3,4,5,6].map((d) => {
    const cb = el("input", { type: "checkbox", checked: (c.send_days || []).includes(d) });
    cb.dataset.day = d;
    return el("label", { class: "row small", style: "gap:5px" }, cb, ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]);
  });
  const trackO = el("input", { type: "checkbox", checked: c.track_opens });
  const trackC = el("input", { type: "checkbox", checked: c.track_clicks });
  const stopR = el("input", { type: "checkbox", checked: c.stop_on_reply });

  const save = async () => {
    const body = {
      from_mailbox_id: mb.value ? +mb.value : null, list_id: list.value ? +list.value : null,
      reply_to: replyTo.value.trim(), timezone: tz.value.trim() || "UTC",
      window_start_hour: +startH.value, window_end_hour: +endH.value, daily_cap: +cap.value,
      min_delay_seconds: +dMin.value, max_delay_seconds: +dMax.value,
      send_days: days.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => +i.dataset.day),
      track_opens: trackO.checked, track_clicks: trackC.checked, stop_on_reply: stopR.checked,
    };
    try { await api(`/campaigns/${c.id}`, { method: "PUT", body }); toast("Settings saved"); refresh(); } catch (e) { fail(e); }
  };

  return el("div", { class: "panel" }, el("div", { class: "panel-head" }, el("h3", {}, "Settings")),
    el("div", { class: "panel-body" },
      el("label", { class: "field" }, el("span", {}, "Send from"), mb),
      el("label", { class: "field" }, el("span", {}, "Contact list"), list),
      el("label", { class: "field" }, el("span", {}, "Reply-To"), replyTo),
      el("div", { class: "divider" }),
      el("label", { class: "field" }, el("span", {}, "Timezone"), tz),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "Window start (h)"), startH),
        el("label", { class: "field" }, el("span", {}, "Window end (h)"), endH)),
      el("div", { class: "field", }, el("span", { class: "small muted" }, "Send days"), el("div", { class: "row wrap", style: "gap:10px;margin-top:6px" }, ...days)),
      el("label", { class: "field" }, el("span", {}, "Daily cap"), cap),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", {}, "Min delay (s)"), dMin),
        el("label", { class: "field" }, el("span", {}, "Max delay (s)"), dMax)),
      el("div", { class: "divider" }),
      el("label", { class: "row small mb" }, trackO, "Track opens"),
      el("label", { class: "row small mb" }, trackC, "Track clicks"),
      el("label", { class: "row small mb" }, stopR, "Stop sequence when they reply"),
      el("button", { class: "btn primary block mt", onclick: save }, "Save settings"),
    ));
}

function sequencePanel(c, steps, refresh) {
  const list = el("div", {});
  const working = steps.map((s) => ({ ...s }));

  function draw() {
    clear(list);
    working.forEach((s, i) => {
      const subj = el("input", { value: s.subject || "", placeholder: i === 0 ? "Subject line" : "(blank = reply in thread)" });
      const body = el("textarea", { placeholder: "<p>Hi {{first_name|there}},</p>" }, s.body_html || "");
      const wait = el("input", { type: "number", min: 0, value: s.wait_days, style: "width:70px" });
      const cond = el("select", {},
        ...["always", "if_no_reply", "if_no_open"].map((x) => el("option", { value: x, selected: s.condition === x }, x.replace(/_/g, " "))));
      subj.addEventListener("input", () => s.subject = subj.value);
      body.addEventListener("input", () => s.body_html = body.value);
      wait.addEventListener("input", () => s.wait_days = +wait.value);
      cond.addEventListener("change", () => s.condition = cond.value);
      list.append(el("div", { class: "step-card" },
        el("div", { class: "sc-head" },
          el("span", { class: "step-num" }, "STEP " + (i + 1)),
          el("div", { class: "row" },
            i > 0 && el("span", { class: "wait-tag" }, "wait"),
            i > 0 && wait, i > 0 && el("span", { class: "wait-tag" }, "days ·"),
            i > 0 && cond,
            el("button", { class: "btn sm ghost danger", onclick: () => { working.splice(i, 1); draw(); } }, "×"))),
        el("div", { class: "sc-body" },
          el("label", { class: "field" }, el("span", {}, "Subject"), subj),
          el("label", { class: "field" }, el("span", {}, "Body (HTML)"), body)),
      ));
    });
  }
  draw();

  return el("div", { class: "panel" }, el("div", { class: "panel-head" }, el("h3", {}, "Email sequence"),
    el("div", { class: "row" },
      el("button", { class: "btn sm", onclick: () => { working.push({ subject: "", body_html: "", wait_days: 3, condition: "if_no_reply", same_thread: true }); draw(); } }, "+ step"),
      el("button", { class: "btn sm primary", onclick: async () => {
        const payload = working.map((s, i) => ({ step_order: i + 1, template_id: null, subject: s.subject || "", body_html: s.body_html || "", body_text: "", wait_days: +s.wait_days || 0, condition: s.condition || "if_no_reply", same_thread: true }));
        try { await api(`/campaigns/${c.id}/steps`, { method: "PUT", body: payload }); toast("Sequence saved"); refresh(); } catch (e) { fail(e); }
      }}, "save sequence"))),
    el("div", { class: "panel-body" },
      el("p", { class: "hint mb" }, "Step 1 opens the thread. Later steps reply into it automatically (leave their subject blank). Merge tags + spintax supported."),
      list),
  );
}

function testSendModal(id, steps) {
  const to = el("input", { placeholder: "you@yourdomain.com" });
  const step = el("select", {}, ...steps.map((s) => el("option", { value: s.step_order }, "Step " + s.step_order)));
  modal({ title: "Send a test", body: el("div", {},
    el("label", { class: "field" }, el("span", {}, "Send to"), to),
    steps.length > 1 && el("label", { class: "field" }, el("span", {}, "Which step"), step),
    el("p", { class: "hint" }, "Rendered with a sample contact (Jordan Lee / Acme). Subject is prefixed with [TEST].")),
    actions: (close) => [el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => { try { const r = await api(`/campaigns/${id}/test-send`, { body: { to: to.value.trim(), step_order: +step.value || 1 } }); close(); toast("Test queued → " + r.queued_to); } catch (e) { fail(e); } } }, "Send test")]});
}

/* ---- Inbox ------------------------------------------------------ */
let INBOX = { mailboxId: null, folder: "INBOX", messageId: null };
routes.inbox = async () => {
  setActiveNav("inbox"); setTitle("Inbox", "webmail");
  const v = viewNode(); $("#view").classList.add("pad-0");
  const mailboxes = await api("/mailboxes");
  STATE.mailboxes = mailboxes;
  if (!mailboxes.length) { $("#view").classList.remove("pad-0"); v.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "✉"), el("p", {}, "Create a mailbox first (Mailboxes tab)."))); return; }
  if (!INBOX.mailboxId || !mailboxes.find((m) => m.id === INBOX.mailboxId)) INBOX.mailboxId = mailboxes[0].id;

  const mbSelect = el("select", { style: "max-width:220px", onchange: (e) => { INBOX.mailboxId = +e.target.value; INBOX.messageId = null; drawInbox(); } },
    ...mailboxes.map((m) => el("option", { value: m.id, selected: m.id === INBOX.mailboxId }, m.address)));
  const composeBtn = el("button", { class: "btn primary sm", onclick: () => composeModal() }, "✎ Compose");

  v.append(el("div", { class: "row", style: "padding:12px 16px;border-bottom:1px solid var(--line);background:var(--bg-1)" },
    mbSelect, el("span", { class: "spacer", style: "flex:1" }), composeBtn));
  v.append(el("div", { class: "mail-layout", id: "mail-layout", style: "height:calc(100% - 53px)" }));
  await drawInbox();
};

async function drawInbox() {
  const root = $("#mail-layout"); if (!root) return;
  clear(root);
  const folders = await api(`/mail/${INBOX.mailboxId}/folders`);
  const FOLDERS = ["INBOX", "Sent", "Archive", "Spam", "Trash", "Drafts"];
  const fCol = el("div", { class: "mail-folders" }, ...FOLDERS.map((f) => {
    const c = folders[f] || { total: 0, unread: 0 };
    return el("a", { href: "#", class: INBOX.folder === f ? "active" : "", onclick: (e) => { e.preventDefault(); INBOX.folder = f; INBOX.messageId = null; drawInbox(); } },
      el("span", {}, f), el("span", { class: "muted small" }, c.unread ? `${c.unread}/${c.total}` : (c.total || "")));
  }));
  const listCol = el("div", { class: "mail-list", id: "mail-list" }, el("div", { class: "loader" }, "loading"));
  const readCol = el("div", { class: "mail-read", id: "mail-read" }, el("div", { class: "empty-state" }, "Select a message"));
  root.append(fCol, listCol, readCol);

  const msgs = await api(`/mail/${INBOX.mailboxId}/messages?folder=${INBOX.folder}&limit=100`);
  clear(listCol);
  if (!msgs.length) { listCol.append(el("div", { class: "t-empty" }, "empty folder")); return; }
  for (const m of msgs) {
    listCol.append(el("div", { class: "mail-item" + (m.is_read ? "" : " unread") + (m.id === INBOX.messageId ? " active" : ""), onclick: () => openMessage(m.id) },
      el("div", { class: "mi-from" }, el("span", { class: "truncate" }, m.from_name || m.from_addr || "(unknown)"),
        el("span", { class: "muted small nowrap" }, fmtDate(m.received_at))),
      el("div", { class: "mi-subj truncate" }, m.subject || "(no subject)"),
      el("div", { class: "mi-snip truncate" }, m.snippet || ""),
      m.spam_score >= 5 ? pill("spam " + m.spam_score, "bad") : null));
  }
}

async function openMessage(mid) {
  INBOX.messageId = mid;
  document.querySelectorAll(".mail-item").forEach((n) => n.classList.remove("active"));
  const read = $("#mail-read"); clear(read).append(el("div", { class: "loader" }, "opening"));
  const m = await api(`/mail/${INBOX.mailboxId}/messages/${mid}`);
  drawInbox._refresh = true;

  const bodyFrame = el("iframe", { sandbox: "" });
  const actions = el("div", { class: "row mb" },
    el("button", { class: "btn sm", onclick: () => composeModal({
      to: [m.from_addr], subject: (m.subject || "").match(/^re:/i) ? m.subject : "Re: " + (m.subject || ""),
      in_reply_to: m.message_id, references: (m.references || "") + " " + m.message_id,
      body_html: `<br><br><blockquote style="border-left:2px solid #ccc;padding-left:10px;color:#666">${esc(m.snippet)}</blockquote>`,
    }) }, "↩ Reply"),
    el("button", { class: "btn sm ghost", onclick: async () => { await api(`/mail/${INBOX.mailboxId}/messages/${mid}/move?folder=Archive`, { method: "POST" }); toast("Archived"); drawInbox(); clear(read).append(el("div", { class: "empty-state" }, "Select a message")); } }, "Archive"),
    el("button", { class: "btn sm ghost", onclick: async () => { await api(`/mail/${INBOX.mailboxId}/messages/${mid}/move?folder=Spam`, { method: "POST" }); toast("Marked spam"); drawInbox(); } }, "Spam"),
    el("button", { class: "btn sm ghost danger", onclick: async () => { await api(`/mail/${INBOX.mailboxId}/messages/${mid}`, { method: "DELETE" }); toast("Deleted"); drawInbox(); clear(read).append(el("div", { class: "empty-state" }, "Select a message")); } }, "Delete"),
    el("a", { class: "btn sm ghost", href: `/api/mail/${INBOX.mailboxId}/messages/${mid}/raw`, target: "_blank" }, "Raw"),
  );
  clear(read).append(actions,
    el("div", { class: "mr-head" },
      el("div", { class: "mr-subj" }, m.subject || "(no subject)"),
      el("div", { class: "small muted" }, `From ${esc(m.from_name || "")} <${esc(m.from_addr)}>`),
      el("div", { class: "small muted" }, `To ${(m.to_addrs || []).map(esc).join(", ")}`),
      el("div", { class: "small muted" }, fmtDate(m.received_at))),
    bodyFrame);
  const html = m.body_html || ("<pre style='white-space:pre-wrap;font-family:inherit'>" + esc(m.body_text || "(empty)") + "</pre>");
  bodyFrame.srcdoc = `<!doctype html><meta charset=utf-8><base target=_blank><style>body{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#111;padding:6px}</style>${html}`;
  drawInbox();
}

function composeModal(pre = {}) {
  const mb = el("select", {}, ...STATE.mailboxes.map((m) => el("option", { value: m.id, selected: m.id === INBOX.mailboxId }, m.address)));
  const to = el("input", { value: (pre.to || []).join(", "), placeholder: "to@example.com, other@example.com" });
  const cc = el("input", { placeholder: "cc (optional)" });
  const subject = el("input", { value: pre.subject || "" });
  const body = el("textarea", { style: "min-height:220px" }, pre.body_html || "");
  modal({
    title: "Compose", wide: true,
    body: el("div", {},
      el("label", { class: "field" }, el("span", {}, "From"), mb),
      el("label", { class: "field" }, el("span", {}, "To"), to),
      el("label", { class: "field" }, el("span", {}, "Cc"), cc),
      el("label", { class: "field" }, el("span", {}, "Subject"), subject),
      el("label", { class: "field" }, el("span", {}, "Body (HTML)"), body),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        const parse = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
        try {
          await api(`/mail/${mb.value}/send`, { body: {
            from_mailbox_id: +mb.value, to: parse(to.value), cc: parse(cc.value), bcc: [],
            subject: subject.value, body_html: body.value, body_text: "",
            in_reply_to: pre.in_reply_to || "", references: pre.references || "" } });
          close(); toast("Message queued"); if (routes.inbox && location.hash.includes("inbox")) drawInbox();
        } catch (e) { fail(e); }
      }}, "Send"),
    ],
  });
}

/* ---- Settings ------------------------------------------------- */
routes.settings = async () => {
  setActiveNav("settings"); setTitle("Settings");
  const v = viewNode();
  const health = await api("/health");
  v.append(
    el("div", { class: "panel mb" }, el("div", { class: "panel-head" }, el("h3", {}, "This server")),
      el("div", { class: "panel-body" },
        kv("Version", health.version),
        kv("Operator", ME ? ME.email : "—"),
        kv("Control panel URL", location.origin),
        el("p", { class: "hint mt", html: "Server hostname, ports, sending caps and TLS paths are set in <span class='kbd'>.env</span> and require a restart. See the README." }),
      )),
    el("div", { class: "panel" }, el("div", { class: "panel-head" }, el("h3", {}, "Deliverability checklist")),
      el("div", { class: "panel-body" }, el("ul", { style: "line-height:2;padding-left:18px" },
        el("li", {}, "Every sending domain shows MX / SPF / DKIM / DMARC = verified on the Domains tab"),
        el("li", {}, "Reverse DNS (PTR) for this server's IP matches its hostname"),
        el("li", {}, "Warm up: start at a low daily cap (20–40) and ramp over 2–3 weeks"),
        el("li", {}, "Keep bounce rate under 3% and honour every unsubscribe (Phosphor does this automatically)"),
        el("li", {}, "Send during business hours in the recipient's timezone; keep 90–300s between messages"),
      ))),
  );
};
function kv(k, val) { return el("div", { class: "spread mb" }, el("span", { class: "muted" }, k), el("b", { class: "mono" }, val)); }

/* ── router ─────────────────────────────────────────────────────────────── */
let _navSeq = 0;
async function route() {
  const seq = ++_navSeq;
  const raw = (location.hash || "#/dashboard").slice(2);
  const [name, arg] = raw.split("/");
  const fn = routes[name] || routes.dashboard;
  if (!$("#view")) return;
  $("#view").classList.remove("pad-0");
  try {
    await fn(arg);
  } catch (e) {
    if (seq !== _navSeq) return;
    fail(e);
    viewNode().append(el("div", { class: "empty-state" }, "Failed to load: " + (e.message || e)));
  }
  if (seq === _navSeq) updateBadges().catch(() => {});
}

async function updateBadges() {
  try {
    const s = await api("/dashboard/stats");
    const set = (id, n) => { const b = document.querySelector(`[data-badge="${id}"]`); if (!b) return; if (n) { b.textContent = n; b.classList.remove("hidden"); } else b.classList.add("hidden"); };
    set("campaigns", s.campaigns_running);
    $("#queue-info").textContent = s.outbound_queued ? `${s.outbound_queued} queued` : "";
    const dot = $("#health-dot");
    if (dot) dot.className = "status-dot" + (s.outbound_failed_24h > 5 ? " warn" : "");
  } catch {}
}

/* ── boot ───────────────────────────────────────────────────────────────── */
let _booted = false;
async function boot() {
  try {
    ME = await api("/auth/me");
  } catch (e) {
    return renderAuth();
  }
  $("#app").replaceWith(shell());
  if (!_booted) {
    window.addEventListener("hashchange", route);
    setInterval(updateBadges, 20000);
    _booted = true;
  }
  // replaceState (not `location.hash = …`) so we don't fire an extra hashchange
  // that would render the first view twice.
  if (!location.hash) history.replaceState(null, "", "#/dashboard");
  await route();
}

function start() {
  if (TOKEN) boot(); else renderAuth();
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
