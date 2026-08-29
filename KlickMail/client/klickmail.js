/* ═══════════════════════════════════════════════════════════════════════════
   KLICK_MAIL // V.1.0.0
   Desktop mail client for a Phosphor server. Talks to {ENDPOINT}/api/* with a
   bearer token. No build step, no framework.
   ═══════════════════════════════════════════════════════════════════════════ */
"use strict";

const LS = {
  endpoint: "klickmail.endpoint",
  token: "klickmail.token",
  identity: "klickmail.identity",
  mailbox: "klickmail.mailbox",
};

let ENDPOINT = localStorage.getItem(LS.endpoint) || "";
let TOKEN = localStorage.getItem(LS.token) || "";
let IDENTITY = safeParse(localStorage.getItem(LS.identity)) || null;
const CACHE = { mailboxes: [], lists: [], templates: [] };

function safeParse(s) { try { return JSON.parse(s); } catch { return null; } }

/* ── DOM helper ─────────────────────────────────────────────────────────── */
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (k === "value") n.value = v;
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const $ = (s, r = document) => r.querySelector(s);
const clear = (n) => { while (n && n.firstChild) n.removeChild(n.firstChild); return n; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ── API ───────────────────────────────────────────────────────────────── */
function apiBase() { return (ENDPOINT || "").replace(/\/+$/, ""); }

async function api(path, { method, body, form, raw, base } = {}) {
  method = method || (body !== undefined || form ? "POST" : "GET");
  const headers = {};
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }

  let res;
  try {
    res = await fetch((base || apiBase()) + "/api" + path, { method, headers, body: payload });
  } catch (e) {
    throw new Error("network unreachable — is the Phosphor endpoint correct and running?");
  }
  if (res.status === 401 && !path.startsWith("/auth/")) { disconnect(true); throw new Error("session expired"); }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  const data = raw ? await res.text() : (ct.includes("json") ? await res.json() : await res.text());
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : (d ? JSON.stringify(d) : res.statusText || ("HTTP " + res.status)));
  }
  return data;
}

/* ── toast / modal ─────────────────────────────────────────────────────── */
function toast(msg, kind = "ok") {
  const t = el("div", { class: "toast " + kind }, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; }, 3400);
  setTimeout(() => t.remove(), 3800);
}
function fail(e) { console.error(e); toast(e.message || String(e), "err"); }

function modal({ title, body, wide, actions }) {
  const root = $("#modal-root");
  const close = () => clear(root);
  const scrim = el("div", { class: "scrim", onclick: (e) => { if (e.target === scrim) close(); } },
    el("div", { class: "modal" + (wide ? " wide" : "") },
      el("div", { class: "modal-head" }, el("span", {}, title), el("button", { class: "x", onclick: close }, "×")),
      el("div", { class: "modal-body" }, body),
      actions && el("div", { class: "modal-foot" }, ...actions(close)),
    ));
  clear(root).append(scrim);
  return close;
}
function confirmDialog(msg, onYes, { yes = "CONFIRM()", danger = true } = {}) {
  modal({
    title: "CONFIRM", body: el("p", {}, msg),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "CANCEL()"),
      el("button", { class: "btn " + (danger ? "danger" : ""), onclick: async () => { close(); try { await onYes(); } catch (e) { fail(e); } } }, yes),
    ],
  });
}

/* ── format ────────────────────────────────────────────────────────────── */
const fmtInt = (n) => (n ?? 0).toLocaleString();
function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s), diff = (Date.now() - d) / 1000;
  if (diff < 60) return "now";
  if (diff < 3600) return Math.floor(diff / 60) + "m";
  if (diff < 86400) return Math.floor(diff / 3600) + "h";
  if (diff < 604800) return Math.floor(diff / 86400) + "d";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function pill(t, k = "") { return el("span", { class: "pill " + k }, t); }
function statusPill(s) {
  const map = { running: "info", active: "info", sent: "ok", delivered: "ok", completed: "ok", replied: "ok",
    paused: "warn", deferred: "warn", queued: "warn", scheduled: "warn", draft: "", pending: "",
    failed: "bad", bounced: "bad", unsubscribed: "bad", stopped_replied: "ok",
    stopped_bounced: "bad", stopped_unsubscribed: "bad", stopped_error: "bad" };
  return pill((s || "").replace(/_/g, " "), map[s] ?? "");
}

/* ── connection lifecycle ──────────────────────────────────────────────── */
function persistConn(endpoint, token, identity) {
  ENDPOINT = endpoint; TOKEN = token; IDENTITY = identity;
  localStorage.setItem(LS.endpoint, endpoint);
  localStorage.setItem(LS.token, token);
  localStorage.setItem(LS.identity, JSON.stringify(identity || {}));
}
function disconnect(expired) {
  TOKEN = "";
  localStorage.removeItem(LS.token);
  if (expired) toast("session expired — reconnect", "warn");
  renderConnect();
}

/* ═══ CONNECT SCREEN ═══════════════════════════════════════════════════════ */
async function renderConnect() {
  const params = new URLSearchParams(location.search);
  const presetServer = params.get("server") || ENDPOINT || "http://localhost:8000";

  const epIn = el("input", { value: presetServer, placeholder: "http://server-ip:8000", autocapitalize: "off", autocorrect: "off", spellcheck: "false" });
  const info = el("div", { class: "hint mt-s" }, "");
  const emailIn = el("input", { type: "email", placeholder: "operator@yourdomain", autocomplete: "username" });
  const passIn = el("input", { type: "password", placeholder: "password", autocomplete: "current-password" });
  const loginBox = el("div", { class: "hidden" },
    el("div", { class: "divider" }),
    el("label", { class: "f" }, el("span", {}, "Operator email"), emailIn),
    el("label", { class: "f" }, el("span", {}, "Password"), passIn),
  );
  const probeBtn = el("button", { class: "btn block" }, "PING()");
  const loginBtn = el("button", { class: "btn block hidden" }, "CONNECT()");

  let verified = null;

  async function probe() {
    probeBtn.disabled = true; probeBtn.textContent = "PING…";
    info.textContent = "";
    const base = epIn.value.trim().replace(/\/+$/, "");
    try {
      const h = await api("/health", { base });
      if (!h || h.app !== "phosphor") throw new Error("that endpoint answered but it isn't a Phosphor server");
      verified = { base, health: h };
      info.innerHTML = `<span style="color:var(--ok)">■</span> PHOSPHOR ${esc(h.version)} // domain: ${esc(h.primary_domain)} // host: ${esc(h.server_hostname)}`;
      loginBox.classList.remove("hidden");
      loginBtn.classList.remove("hidden");
      probeBtn.classList.add("hidden");
      emailIn.focus();
    } catch (e) {
      verified = null;
      info.innerHTML = `<span style="color:var(--bad)">■</span> ${esc(e.message)}`;
    } finally {
      probeBtn.disabled = false; probeBtn.textContent = "PING()";
    }
  }

  async function login() {
    if (!verified) return probe();
    loginBtn.disabled = true; loginBtn.textContent = "CONNECT…";
    try {
      const r = await api("/auth/login", { base: verified.base, body: { email: emailIn.value.trim(), password: passIn.value } });
      persistConn(verified.base, r.access_token, {
        email: emailIn.value.trim(),
        domain: verified.health.primary_domain,
        host: verified.health.server_hostname,
        version: verified.health.version,
        outbound: verified.health.outbound_enabled,
      });
      toast("connected // " + verified.base, "ok");
      boot();
    } catch (e) { fail(e); loginBtn.disabled = false; loginBtn.textContent = "CONNECT()"; }
  }

  probeBtn.addEventListener("click", probe);
  loginBtn.addEventListener("click", login);
  epIn.addEventListener("keydown", (e) => { if (e.key === "Enter") probe(); });
  passIn.addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
  epIn.addEventListener("input", () => {
    verified = null; loginBox.classList.add("hidden"); loginBtn.classList.add("hidden");
    probeBtn.classList.remove("hidden"); info.textContent = "";
  });

  clear($("#app")).append(el("div", { class: "connect-wrap" },
    el("div", { class: "connect" },
      el("div", { class: "cx-head" },
        el("div", { class: "big-mark" }, "KLICK_MAIL // V.1.0.0"),
        el("h1", { class: "mt-s tight" }, "Klick", el("br"), "Mail"),
      ),
      el("div", { class: "cx-body" },
        el("p", { class: "tokens mb" }, "CONNECT TO YOUR ", el("b", {}, "PHOSPHOR"), " MAIL SERVER"),
        el("label", { class: "f" }, el("span", {}, "Phosphor API endpoint"), epIn),
        info,
        loginBox,
        el("div", { class: "mt" }, probeBtn, loginBtn),
        el("p", { class: "hint mt" }, "Runs against a Phosphor server on your network, Tailscale, or a Cloudflare tunnel. Nothing is sent anywhere else."),
      ),
    ),
  ));
  epIn.focus();
}

/* ═══ SHELL ════════════════════════════════════════════════════════════════ */
const TABS = ["inbox", "compose", "contacts", "mass_mail", "specs"];
let TAB = "inbox";

function shell() {
  const tabs = el("div", { class: "tabs" }, ...TABS.map((t) =>
    el("button", { class: "tab" + (t === TAB ? " active" : ""), "data-tab": t, onclick: () => go(t) },
      t.replace("_", "_"))));
  return el("div", { id: "app" },
    el("div", { id: "shell" },
      el("div", { class: "topbar" },
        el("span", { class: "brand" }, "KLICK_MAIL"),
        el("span", { class: "ver" }, "V.1.0.0"),
        tabs,
        el("span", { class: "spacer" }),
        el("span", { class: "ver", id: "conn-tag" }, IDENTITY ? IDENTITY.domain : ""),
      ),
      el("div", { class: "main" }, el("div", { class: "view", id: "view" }, el("div", { class: "loader" }, "load"))),
    ),
  );
}

function setTabActive(t) {
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
}
function viewNode() { const v = $("#view"); v.classList.remove("pad-0"); return clear(v); }

let _seq = 0;
async function go(t) {
  if (!VIEWS[t]) t = "inbox";
  TAB = t; setTabActive(t);
  const seq = ++_seq;
  try { await VIEWS[t](); }
  catch (e) { if (seq === _seq) { fail(e); viewNode().append(el("div", { class: "empty" }, "load failed // " + esc(e.message))); } }
}

async function ensureRefs() {
  if (!CACHE.mailboxes.length) CACHE.mailboxes = await api("/mailboxes").catch(() => []);
}

/* ═══ VIEWS ════════════════════════════════════════════════════════════════ */
const VIEWS = {};

/* ---- INBOX ----------------------------------------------------------- */
let INBOX = { mailboxId: null, folder: "INBOX", messageId: null };

VIEWS.inbox = async () => {
  await ensureRefs();
  const v = viewNode(); v.classList.add("pad-0");
  if (!CACHE.mailboxes.length) {
    v.classList.remove("pad-0");
    v.append(el("div", { class: "empty" }, el("div", { class: "mk" }, "∅"),
      "no mailboxes on this server", el("div", { class: "hint mt" }, "create one in the Phosphor control panel first")));
    return;
  }
  if (!INBOX.mailboxId || !CACHE.mailboxes.find((m) => m.id === INBOX.mailboxId)) {
    INBOX.mailboxId = +(localStorage.getItem(LS.mailbox) || 0) || CACHE.mailboxes[0].id;
  }

  const sel = el("select", { style: "max-width:260px", onchange: (e) => {
    INBOX.mailboxId = +e.target.value; INBOX.messageId = null;
    localStorage.setItem(LS.mailbox, e.target.value); drawMail();
  } }, ...CACHE.mailboxes.map((m) => el("option", { value: m.id, selected: m.id === INBOX.mailboxId }, m.address)));

  v.append(
    el("div", { class: "row", style: "padding:12px var(--pad);border-bottom:1px solid var(--line)" },
      el("span", { class: "section-label", style: "margin:0" }, "INBOX"),
      sel, el("span", { class: "spacer" }),
      el("button", { class: "btn sm", onclick: () => { go("compose"); } }, "COMPOSE()")),
    el("div", { class: "mail", id: "mail", style: "height:calc(100% - 49px)" }),
  );
  await drawMail();
};

async function drawMail() {
  const root = $("#mail"); if (!root) return;
  clear(root);
  const [folders, msgs] = await Promise.all([
    api(`/mail/${INBOX.mailboxId}/folders`),
    api(`/mail/${INBOX.mailboxId}/messages?folder=${INBOX.folder}&limit=100`),
  ]);
  const FOLDERS = ["INBOX", "Sent", "Archive", "Spam", "Trash", "Drafts"];
  root.append(
    el("div", { class: "mail-folders" }, ...FOLDERS.map((f) => {
      const c = folders[f] || { total: 0, unread: 0 };
      return el("div", { class: "mf" + (f === INBOX.folder ? " active" : ""), onclick: () => { INBOX.folder = f; INBOX.messageId = null; drawMail(); } },
        el("span", {}, f), el("span", { class: "dimmer" }, c.unread ? `${c.unread}/${c.total}` : (c.total || "")));
    })),
    el("div", { class: "mail-list", id: "mlist" }),
    el("div", { class: "mail-read", id: "mread" }, el("div", { class: "empty" }, "select a message")),
  );
  const list = $("#mlist");
  if (!msgs.length) { list.append(el("div", { class: "t-empty" }, "empty")); return; }
  for (const m of msgs) {
    list.append(el("div", { class: "mi" + (m.is_read ? "" : " unread") + (m.id === INBOX.messageId ? " sel" : ""), onclick: () => openMsg(m.id) },
      el("div", { class: "mi-top" }, el("span", { class: "truncate" }, m.from_name || m.from_addr || "(unknown)"), el("span", { class: "dimmer nowrap" }, fmtDate(m.received_at))),
      el("div", { class: "mi-sub truncate" }, m.subject || "(no subject)"),
      el("div", { class: "mi-snip truncate" }, m.snippet || ""),
      m.spam_score >= 5 && pill("spam", "bad")));
  }
}

async function openMsg(id) {
  INBOX.messageId = id;
  document.querySelectorAll(".mi").forEach((n) => n.classList.remove("sel"));
  const read = clear($("#mread")); read.append(el("div", { class: "loader" }, "open"));
  const m = await api(`/mail/${INBOX.mailboxId}/messages/${id}`);
  const frame = el("iframe", { sandbox: "" });
  read.replaceChildren(
    el("div", { class: "row mb" },
      el("button", { class: "btn sm", onclick: () => replyTo(m) }, "REPLY()"),
      el("button", { class: "btn sm ghost", onclick: () => moveMsg(id, "Archive") }, "ARCHIVE()"),
      el("button", { class: "btn sm ghost", onclick: () => moveMsg(id, "Spam") }, "SPAM()"),
      el("button", { class: "btn sm danger", onclick: () => delMsg(id) }, "DELETE()")),
    el("div", { class: "mr-head" },
      el("div", { class: "mr-subj" }, m.subject || "(no subject)"),
      el("div", { class: "mr-meta" }, `FROM ${esc(m.from_name || "")} <${esc(m.from_addr)}>`),
      el("div", { class: "mr-meta" }, `TO ${(m.to_addrs || []).map(esc).join(", ") || "—"}`),
      el("div", { class: "mr-meta" }, new Date(m.received_at).toLocaleString())),
    frame,
  );
  const html = m.body_html || ("<pre style='white-space:pre-wrap;font-family:monospace'>" + esc(m.body_text || "(empty)") + "</pre>");
  frame.srcdoc = `<!doctype html><meta charset=utf-8><base target=_blank><style>body{font:13px/1.6 ui-monospace,Menlo,Consolas,monospace;color:#000;padding:10px}</style>${html}`;
  drawMail();
}
async function moveMsg(id, folder) { await api(`/mail/${INBOX.mailboxId}/messages/${id}/move?folder=${folder}`, { method: "POST" }); toast(folder.toUpperCase(), "ok"); INBOX.messageId = null; drawMail(); }
async function delMsg(id) { await api(`/mail/${INBOX.mailboxId}/messages/${id}`, { method: "DELETE" }); toast("DELETED", "ok"); INBOX.messageId = null; drawMail(); }
function replyTo(m) {
  COMPOSE_PREFILL = {
    from_mailbox_id: INBOX.mailboxId,
    to: m.from_addr,
    subject: /^re:/i.test(m.subject || "") ? m.subject : "Re: " + (m.subject || ""),
    in_reply_to: m.message_id,
    references: ((m.references || "") + " " + m.message_id).trim(),
    body_html: `<br><br><blockquote style="border-left:2px solid #999;padding-left:10px;color:#555">${esc(m.snippet || "")}</blockquote>`,
    mode: "html",
  };
  go("compose");
}

/* ---- COMPOSE ------------------------------------------------------------ */
let COMPOSE_PREFILL = null;

VIEWS.compose = async () => {
  await ensureRefs();
  const v = viewNode();
  const pre = COMPOSE_PREFILL || {}; COMPOSE_PREFILL = null;

  if (!CACHE.mailboxes.length) { v.append(el("div", { class: "empty" }, "no mailboxes")); return; }

  const from = el("select", {}, ...CACHE.mailboxes.map((m) =>
    el("option", { value: m.id, selected: m.id === (pre.from_mailbox_id || INBOX.mailboxId) }, m.address)));
  const to = el("input", { value: pre.to || "", placeholder: "someone@example.com, other@example.com" });
  const cc = el("input", { placeholder: "cc (optional)" });
  const subject = el("input", { value: pre.subject || "" });
  const body = el("textarea", { style: "min-height:320px" }, pre.body_html || pre.body_text || "");
  let mode = pre.mode || "text";

  const preview = el("iframe", { class: "preview-frame hidden", sandbox: "" });
  const modeBtn = el("button", { class: "btn sm ghost", onclick: () => { mode = mode === "html" ? "text" : "html"; syncMode(); } });
  function syncMode() {
    modeBtn.textContent = mode === "html" ? "MODE: HTML" : "MODE: TEXT";
    preview.classList.toggle("hidden", mode !== "html");
    if (mode === "html") renderPreview();
  }
  function renderPreview() {
    preview.srcdoc = `<!doctype html><meta charset=utf-8><base target=_blank><style>body{font:13px/1.6 system-ui,sans-serif;color:#000;padding:10px}</style>${body.value}`;
  }
  body.addEventListener("input", () => { if (mode === "html") renderPreview(); });

  const sendBtn = el("button", { class: "btn", onclick: send }, "SEND()");
  async function send() {
    const parse = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const tos = parse(to.value);
    if (!tos.length) return toast("no recipients", "warn");
    sendBtn.disabled = true; sendBtn.textContent = "SEND…";
    try {
      await api(`/mail/${from.value}/send`, { body: {
        from_mailbox_id: +from.value, to: tos, cc: parse(cc.value), bcc: [],
        subject: subject.value,
        body_text: mode === "text" ? body.value : "",
        body_html: mode === "html" ? body.value : "",
        in_reply_to: pre.in_reply_to || "", references: pre.references || "",
      } });
      toast("QUEUED // " + tos.length + " RCPT", "ok");
      to.value = cc.value = subject.value = body.value = ""; renderPreview();
    } catch (e) { fail(e); }
    finally { sendBtn.disabled = false; sendBtn.textContent = "SEND()"; }
  }

  v.append(
    el("div", { class: "section-label" }, "COMPOSE"),
    el("div", { class: "panel" }, el("div", { class: "panel-body" },
      el("div", { class: "f2" },
        el("label", { class: "f" }, el("span", {}, "From"), from),
        el("label", { class: "f" }, el("span", {}, "To"), to)),
      el("label", { class: "f" }, el("span", {}, "Cc"), cc),
      el("label", { class: "f" }, el("span", {}, "Subject"), subject),
      el("div", { class: "spread mb" }, el("span", { class: "dimmer up", style: "font-size:10px" }, "Body"), modeBtn),
      el("div", { class: "split" }, body, preview),
      el("div", { class: "mt" }, sendBtn),
    )),
  );
  syncMode();
};

/* ---- CONTACTS --------------------------------------------------------- */
VIEWS.contacts = async () => {
  const v = viewNode();
  const search = el("input", { placeholder: "search", style: "max-width:280px" });
  v.append(el("div", { class: "section-label" }, "CONTACTS_LIST"),
    el("div", { class: "spread mb" }, search, el("div", { class: "row" },
      el("button", { class: "btn sm", onclick: importContacts }, "IMPORT_CSV()"),
      el("button", { class: "btn sm", onclick: () => contactForm() }, "ADD()"))));
  const host = el("div", { id: "chost" }); v.append(host);

  const load = async () => {
    const q = search.value.trim();
    const rows = await api("/contacts?limit=300" + (q ? "&q=" + encodeURIComponent(q) : ""));
    const box = clear(host);
    if (!rows.length) { box.append(el("div", { class: "empty" }, el("div", { class: "mk" }, "0x00"), "no contacts // import a csv")); return; }
    box.append(el("div", { class: "panel" }, el("div", { class: "panel-body tight" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Email"), el("th", {}, "Name"), el("th", {}, "Company"), el("th", {}, "Status"), el("th", {}, ""))),
      el("tbody", {}, ...rows.map((c) => el("tr", { class: "clickable", onclick: () => contactForm(c) },
        el("td", {}, c.email),
        el("td", { class: "dim" }, `${c.first_name} ${c.last_name}`.trim() || "—"),
        el("td", { class: "dim" }, c.company || "—"),
        el("td", {}, statusPill(c.status)),
        el("td", { style: "text-align:right" }, el("button", { class: "btn sm danger", onclick: (e) => { e.stopPropagation(); confirmDialog(`Delete ${c.email}?`, async () => { await api(`/contacts/${c.id}`, { method: "DELETE" }); toast("DELETED", "ok"); load(); }); } }, "×")))),
    )))));
  };
  let t; search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 220); });
  await load();
};

function contactForm(c = null) {
  const f = {};
  const mk = (k, lbl) => { f[k] = el("input", { value: c ? (c[k] || "") : "" }); return el("label", { class: "f" }, el("span", {}, lbl), f[k]); };
  const email = el("input", { value: c ? c.email : "", disabled: !!c, placeholder: "person@company.com" });
  modal({
    title: c ? "EDIT CONTACT" : "ADD CONTACT", wide: true,
    body: el("div", {},
      el("label", { class: "f" }, el("span", {}, "Email"), email),
      el("div", { class: "f2" }, mk("first_name", "First name"), mk("last_name", "Last name")),
      el("div", { class: "f2" }, mk("company", "Company"), mk("title", "Title")),
      el("div", { class: "f2" }, mk("phone", "Phone"), mk("website", "Website")),
      c && el("p", { class: "hint" }, "status: " + c.status + (c.custom && Object.keys(c.custom).length ? " // custom: " + Object.keys(c.custom).join(", ") : "")),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "CANCEL()"),
      el("button", { class: "btn", onclick: async () => {
        const b = Object.fromEntries(Object.entries(f).map(([k, n]) => [k, n.value.trim()]));
        try {
          if (c) await api(`/contacts/${c.id}`, { method: "PUT", body: b });
          else await api("/contacts", { body: { ...b, email: email.value.trim() } });
          close(); toast("SAVED", "ok"); go("contacts");
        } catch (e) { fail(e); }
      } }, "SAVE()"),
    ],
  });
}

function importContacts() {
  const file = el("input", { type: "file", accept: ".csv,text/csv" });
  const listName = el("input", { placeholder: "optional — drop rows into this list" });
  modal({
    title: "IMPORT_CSV()",
    body: el("div", {},
      el("label", { class: "f" }, el("span", {}, "CSV file"), file),
      el("label", { class: "f" }, el("span", {}, "Add to list"), listName),
      el("p", { class: "hint", html: "Needs an <span class='kbd'>email</span> column. first name / company / title / website are auto-mapped; other columns become merge fields." }),
    ),
    actions: (close) => [
      el("button", { class: "btn ghost", onclick: close }, "CANCEL()"),
      el("button", { class: "btn", onclick: async () => {
        if (!file.files[0]) return toast("pick a file", "warn");
        const fd = new FormData(); fd.append("file", file.files[0]);
        const qs = listName.value.trim() ? "?list_name=" + encodeURIComponent(listName.value.trim()) : "";
        try { const r = await api("/contacts/import" + qs, { method: "POST", form: fd });
          close(); toast(`+${r.created} NEW / ${r.updated} UPD / ${r.skipped} SKIP`, "ok"); go("contacts");
        } catch (e) { fail(e); }
      } }, "IMPORT()"),
    ],
  });
}

/* ---- MASS_MAIL ------------------------------------------------------- */
VIEWS.mass_mail = async () => {
  await ensureRefs();
  const v = viewNode();
  const [lists, campaigns] = await Promise.all([api("/lists").catch(() => []), api("/campaigns").catch(() => [])]);
  CACHE.lists = lists;

  v.append(el("div", { class: "section-label" }, "MASS_MAILING"));

  if (!CACHE.mailboxes.length) { v.append(el("div", { class: "empty" }, "no sending mailbox on this server")); return; }

  /* ── new blast form ── */
  const from = el("select", {}, ...CACHE.mailboxes.map((m) => el("option", { value: m.id }, m.address)));
  const list = el("select", {}, ...(lists.length ? lists.map((l) => el("option", { value: l.id }, `${l.name} (${l.member_count})`))
    : [el("option", { value: "" }, "— no lists — import contacts into one first —")]));
  const subject = el("input", { placeholder: "Quick question about {{company}}" });
  const bodyHtml = el("textarea", { style: "min-height:240px", placeholder: "<p>Hi {{first_name|there}},</p>" });
  const preview = el("iframe", { class: "preview-frame", sandbox: "" });
  const cap = el("input", { type: "number", value: "40", min: "1" });
  const dMin = el("input", { type: "number", value: "90", min: "0" });
  const dMax = el("input", { type: "number", value: "300", min: "0" });
  const trackO = el("input", { type: "checkbox", checked: true });
  const trackC = el("input", { type: "checkbox", checked: true });

  function rp() { preview.srcdoc = `<!doctype html><meta charset=utf-8><style>body{font:13px/1.6 system-ui,sans-serif;color:#000;padding:10px}</style>${bodyHtml.value || "<em>(empty)</em>"}`; }
  bodyHtml.addEventListener("input", rp); rp();

  const launch = el("button", { class: "btn", onclick: doLaunch }, "LAUNCH()");
  async function doLaunch() {
    if (!list.value) return toast("no list selected", "warn");
    if (!subject.value.trim() || !bodyHtml.value.trim()) return toast("subject + body required", "warn");
    launch.disabled = true; launch.textContent = "LAUNCH…";
    try {
      const c = await api("/campaigns", { body: {
        name: subject.value.trim().slice(0, 60) + " // " + new Date().toISOString().slice(0, 10),
        from_mailbox_id: +from.value, list_id: +list.value,
        daily_cap: +cap.value || 40, min_delay_seconds: +dMin.value || 60, max_delay_seconds: +dMax.value || 240,
        track_opens: trackO.checked, track_clicks: trackC.checked,
        window_start_hour: 0, window_end_hour: 0, send_days: [0, 1, 2, 3, 4, 5, 6],
      } });
      await api(`/campaigns/${c.id}/steps`, { method: "PUT", body: [
        { step_order: 1, template_id: null, subject: subject.value, body_html: bodyHtml.value, body_text: "", wait_days: 0, condition: "always", same_thread: true },
      ] });
      await api(`/campaigns/${c.id}/start`, { method: "POST" });
      toast("LAUNCHED // CAMPAIGN #" + c.id, "ok");
      go("mass_mail");
    } catch (e) { fail(e); launch.disabled = false; launch.textContent = "LAUNCH()"; }
  }

  v.append(el("div", { class: "panel" },
    el("div", { class: "panel-head" }, el("span", {}, "NEW BLAST"), el("span", { class: "dimmer" }, "sends now, throttled")),
    el("div", { class: "panel-body" },
      el("div", { class: "f2" },
        el("label", { class: "f" }, el("span", {}, "Send from"), from),
        el("label", { class: "f" }, el("span", {}, "Audience list"), list)),
      el("label", { class: "f" }, el("span", {}, "Subject"), subject),
      el("p", { class: "hint mb", html: "merge: <span class='kbd'>{{first_name|there}}</span> <span class='kbd'>{{company}}</span> &nbsp; spintax: <span class='kbd'>{quick|brief}</span>" }),
      el("div", { class: "split" },
        el("div", {}, el("label", { class: "f" }, el("span", {}, "HTML body"), bodyHtml)),
        el("div", {}, el("span", { class: "dimmer up", style: "font-size:10px" }, "Preview (sample contact)"), preview)),
      el("div", { class: "divider" }),
      el("div", { class: "f3" },
        el("label", { class: "f" }, el("span", {}, "Daily cap"), cap),
        el("label", { class: "f" }, el("span", {}, "Min delay (s)"), dMin),
        el("label", { class: "f" }, el("span", {}, "Max delay (s)"), dMax)),
      el("div", { class: "row" },
        el("label", { class: "row", style: "gap:6px;font-size:11px" }, trackO, "TRACK OPENS"),
        el("label", { class: "row", style: "gap:6px;font-size:11px" }, trackC, "TRACK CLICKS")),
      el("div", { class: "mt" }, launch),
    )));

  /* ── running / past blasts ── */
  const box = el("div", { class: "panel mt", id: "cmp-host" }, el("div", { class: "panel-head" }, el("span", {}, "CAMPAIGNS"), el("span", { class: "dimmer" }, campaigns.length + "")),
    el("div", { class: "panel-body tight" }, el("div", { class: "loader" }, "load")));
  v.append(box);
  await drawCampaigns();
};

async function drawCampaigns() {
  const host = $("#cmp-host .panel-body"); if (!host) return;
  const campaigns = await api("/campaigns");
  clear(host);
  if (!campaigns.length) { host.append(el("div", { class: "t-empty" }, "no blasts yet")); return; }
  const rows = [];
  for (const c of campaigns.slice(0, 30)) {
    const s = await api(`/campaigns/${c.id}/stats`).catch(() => null);
    rows.push(el("tr", {},
      el("td", { class: "truncate", style: "max-width:260px" }, c.name),
      el("td", {}, statusPill(c.status)),
      el("td", { class: "dim" }, s ? `${s.sent}/${s.recipients_total}` : "—"),
      el("td", { class: "dim" }, s ? s.open_rate + "%" : "—"),
      el("td", { class: "dim" }, s ? s.reply_rate + "%" : "—"),
      el("td", { class: "dim" }, s ? s.bounces : "—"),
      el("td", { style: "text-align:right" },
        (c.status === "running")
          ? el("button", { class: "btn sm ghost", onclick: async () => { await api(`/campaigns/${c.id}/pause`, { method: "POST" }); toast("PAUSED", "warn"); drawCampaigns(); } }, "PAUSE()")
          : (c.status === "paused"
            ? el("button", { class: "btn sm", onclick: async () => { await api(`/campaigns/${c.id}/resume`, { method: "POST" }); toast("RESUMED", "ok"); drawCampaigns(); } }, "RESUME()")
            : "")),
    ));
  }
  host.append(el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Blast"), el("th", {}, "State"), el("th", {}, "Sent"), el("th", {}, "Open"), el("th", {}, "Reply"), el("th", {}, "Bnc"), el("th", {}, ""))),
    el("tbody", {}, ...rows)));
}

/* ---- SPECS --------------------------------------------------------- */
VIEWS.specs = async () => {
  const v = viewNode();
  const id = IDENTITY || {};
  let dash = null;
  try { dash = await api("/dashboard/stats"); } catch { /* older server */ }

  const spec = (n, k, val) => el("div", { class: "spec" }, el("div", { class: "n" }, n), el("div", { class: "k" }, k), el("div", { class: "v" }, val));

  v.append(
    el("div", { class: "section-label" }, "KLICK_MAIL // SPECS"),
    el("p", { class: "tokens mb" }, "CUSTOM_EMAIL_CLIENT // MAILING_FOCUSED // FREE"),
    el("div", { class: "specs" },
      spec("01", "Type", "Custom Email Client"),
      spec("02", "Pricing", "FREE"),
      spec("03", "Focus", "Mailing & Communication"),
      spec("04", "HTML Composition", "Custom HTML email bodies + live preview"),
      spec("05", "Contacts List", "Built-in, CSV import, merge fields"),
      spec("06", "Mass Mailing", "Throttled blasts via Phosphor campaigns"),
      spec("07", "Transport", "Phosphor JSON API (SMTP/DKIM handled server-side)"),
      spec("08", "Security", "Bearer token over TLS; no cookies, no third parties"),
      spec("09", "Privacy", "No tracking, no ads, no data mining"),
    ),
    el("div", { class: "section-label mt" }, "CONNECTION"),
    el("div", { class: "specs" },
      spec("→", "Endpoint", ENDPOINT || "—"),
      spec("→", "Operator", id.email || "—"),
      spec("→", "Domain", id.domain || "—"),
      spec("→", "Server host", id.host || "—"),
      spec("→", "Phosphor", id.version || "—"),
      spec("→", "Outbound", id.outbound === false ? "DISABLED" : "ENABLED"),
    ),
    dash && el("div", { class: "grid3 mt" },
      cell("Mailboxes", dash.mailboxes), cell("Contacts", dash.contacts),
      cell("Queued", dash.outbound_queued), cell("Sent 24h", dash.outbound_sent_24h),
      cell("Replies 7d", dash.replies_7d), cell("Suppressed", dash.suppressed)),
    el("div", { class: "mt" },
      el("button", { class: "btn danger", onclick: () => confirmDialog("Disconnect from this Phosphor server?", () => { localStorage.removeItem(LS.token); localStorage.removeItem(LS.identity); location.reload(); }, { yes: "DISCONNECT()" }) }, "DISCONNECT()")),
  );
};
function cell(k, val) { return el("div", { class: "cell" }, el("div", { class: "k" }, k), el("div", { class: "v" }, fmtInt(val))); }

/* ═══ BOOT ════════════════════════════════════════════════════════════════ */
let _wired = false;
async function boot() {
  if (!ENDPOINT || !TOKEN) return renderConnect();
  try { await api("/auth/me"); }
  catch { return renderConnect(); }

  $("#app").replaceWith(shell());
  const tag = $("#conn-tag");
  if (tag && IDENTITY) tag.textContent = IDENTITY.domain + " // " + (IDENTITY.email || "");
  if (!_wired) {
    setInterval(() => {
      if (TAB === "mass_mail" && $("#cmp-host")) drawCampaigns().catch(() => {});
      if (TAB === "inbox" && $("#mail")) { /* light: only refresh folder counts silently */ }
    }, 15000);
    _wired = true;
  }
  go(TAB);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
