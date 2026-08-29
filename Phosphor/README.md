# Φ Phosphor

**A self-hosted mail server and cold-outreach engine in one app.**
Black-and-green control panel. Runs from a single Python process.

Phosphor gives you two things that normally take five services to assemble:

1. **A real mail server for your domain** — inbound MX, authenticated submission,
   per-domain DKIM, SPF/DMARC record generation and live verification, mailboxes
   with Maildir storage, forwarding aliases, catch-all, a built-in webmail client,
   and an outbound queue with retry/backoff.
2. **A cold-email client** — contacts + CSV import with automatic field mapping,
   reusable templates with `{{merge}}` tags and spintax, multi-step follow-up
   sequences that reply into the original thread, per-campaign sending windows,
   daily caps and human-like delays, open/click tracking, automatic
   reply-detection, bounce handling, and a one-click unsubscribe flow with a
   global suppression list.

> ⚠️ Running a mail server that the big inboxes will trust takes correct DNS, a
> clean IP with matching reverse DNS, and TLS. Phosphor automates the software;
> it can't fix a blacklisted IP. Read **Production setup** below.

---

## What's inside

```
phosphor/
├── app.py              FastAPI app: JSON API + tracking endpoints + SPA
├── config.py           all settings (env / .env, PHOSPHOR_ prefix)
├── database.py         SQLAlchemy engine (SQLite by default, WAL-tuned)
├── models.py           domains, mailboxes, messages, contacts, campaigns, events…
├── worker.py           APScheduler: outbound queue + campaign sequencer + DNS recheck
├── auth.py             JWT operator auth
├── crypto/             Argon2 passwords, RSA/DKIM keygen + signing
├── mailstore/          Maildir read/write (Dovecot-compatible layout) + MIME parser
├── dns/                DNS record generation + live check (MX/SPF/DKIM/DMARC/PTR)
├── smtp/
│   ├── server.py       inbound MX (25) + authenticated submission (587), via aiosmtpd
│   ├── sender.py       outbound delivery: MX lookup, STARTTLS, DKIM sign, retry/backoff
│   ├── reply_detect.py classifies inbound as reply / bounce / normal
│   └── spam.py         small heuristic scorer
├── outreach/
│   ├── personalization.py  {{tag|fallback}} + {spin|tax}
│   ├── mime_builder.py     builds outbound messages
│   ├── tracking.py         open pixel + link wrapping
│   ├── throttle.py         sending window / caps / jitter
│   ├── suppression.py      do-not-contact enforcement
│   ├── campaigns.py        enrolment, sequencing, queueing
│   └── stats.py            campaign analytics
└── web/                the single-page control panel (vanilla JS, no build step)
```

---

## Quick start (local, no real mail)

Requires **Python 3.11+**.

**Linux (the recommended host):**

```bash
cd Phosphor
./scripts/setup-linux.sh              # venv + deps + .env with a fresh secret
./scripts/setup-linux.sh --caps       # ...and allow binding :25 / :587 without root
./scripts/setup-linux.sh --systemd    # ...and install + start the systemd service
./.venv/bin/python run.py             # or run it in the foreground
```

**Windows / macOS (manual):**

```bash
cd Phosphor
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # edit PHOSPHOR_SECRET_KEY at least
python run.py --api-only      # API + control panel only, no SMTP listeners
```

Open **http://localhost:8000** and create the operator account on first load.

> Phosphor is pure Python and runs the same on Linux, macOS and Windows. The one
> platform note: on Windows the Maildir filename separator is auto-switched from
> `:` to `!` (NTFS forbids colons); Linux keeps the standard Dovecot layout.

`--api-only` skips the SMTP servers and the background worker, which is what you
want for poking at the UI. Drop the flag to run the whole thing.

---

## Using the KlickMail client (separate machine)

The bundled control panel is the admin surface. For day-to-day mailing there's a
standalone desktop client, **KlickMail** (see `../KlickMail/`), that runs on your
own laptop and talks to this server over its JSON API.

The server already allows it: the API binds `0.0.0.0:8000`, CORS is open
(bearer-token auth, no cookies), and `GET /api/health` advertises the server so
the client can pair with it. To reach it from another machine:

| Method | How | When |
|--------|-----|------|
| **Same LAN** | Point KlickMail at `http://<server-lan-ip>:8000` | Both machines on one network |
| **Tailscale** *(recommended)* | `tailscale up` on both boxes → use `http://<server-tailscale-ip>:8000` | Different networks, zero public exposure |
| **Cloudflare Tunnel** | `cloudflared tunnel --url http://localhost:8000` → use the printed `https://*.trycloudflare.com` URL | Quick public HTTPS, no account needed for the quick tunnel |
| **Reverse proxy** | nginx/Caddy on `mail.example.com` → `127.0.0.1:8000` | Permanent public deployment (do this + real TLS in production anyway) |

Lock CORS to the client origin once you know it:
`PHOSPHOR_CORS_ORIGINS=http://localhost:8765`

### Ports

| Port | Purpose | Needs |
|------|---------|-------|
| 8000 | Control panel + JSON API + tracking links | — |
| 25   | Inbound mail (MX) | root/`CAP_NET_BIND_SERVICE`, open in firewall |
| 587  | Authenticated submission (for the webmail + external clients) | TLS strongly recommended |

Bind ports below 1024 on Linux without root:

```bash
sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python))
```

---

## Production setup

### 1. DNS

Add the domain in **Domains → Add domain**. Phosphor generates a 2048-bit DKIM
key on the spot, then shows every record you need with a **copy** button and a
**Verify now** button that does live lookups.

For `example.com` sending through `mail.example.com`:

| Type | Host | Value |
|------|------|-------|
| A | `mail.example.com` | `<your server's public IPv4>` |
| MX | `example.com` | `10 mail.example.com.` |
| TXT | `example.com` | `v=spf1 mx a:mail.example.com -all` |
| TXT | `phosphor._domainkey.example.com` | `v=DKIM1; h=sha256; k=rsa; p=MIIBIj...` (shown in the UI) |
| TXT | `_dmarc.example.com` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@example.com; adkim=s; aspf=s; fo=1` |
| PTR | *(reverse zone of your IP)* | `mail.example.com.` — set this at your VPS/hosting provider |

The **PTR / reverse DNS** record is the one people forget. If your IP's PTR
doesn't resolve to `mail.example.com`, most large providers will reject or spam-
folder everything. Set it in your hosting control panel, not your DNS zone.

### 2. TLS

Put valid certs in front of both the web app and the SMTP submission port. Get
them with certbot:

```bash
sudo certbot certonly --standalone -d mail.example.com
```

Then in `.env`:

```
PHOSPHOR_TLS_CERT_FILE=/etc/letsencrypt/live/mail.example.com/fullchain.pem
PHOSPHOR_TLS_KEY_FILE=/etc/letsencrypt/live/mail.example.com/privkey.pem
PHOSPHOR_PUBLIC_URL=https://mail.example.com
```

For the control panel, run Phosphor behind nginx/Caddy terminating HTTPS on 443
and proxying to `127.0.0.1:8000`. (Caddy example: `mail.example.com { reverse_proxy 127.0.0.1:8000 }`.)

### 3. Run it as a service

```ini
# /etc/systemd/system/phosphor.service
[Unit]
Description=Phosphor
After=network.target

[Service]
WorkingDirectory=/opt/phosphor
ExecStart=/opt/phosphor/.venv/bin/python run.py
Restart=always
AmbientCapabilities=CAP_NET_BIND_SERVICE
User=phosphor

[Install]
WantedBy=multi-user.target
```

### 4. First admin without the browser

```bash
python -m phosphor.cli create-admin you@example.com
```

---

## Using the cold-email side

1. **Mailboxes** → create the address you'll send from (e.g. `outreach@example.com`).
   The password you set is also its SMTP-submission password.
2. **Contacts → Import CSV.** Any `email` column works; `first name`, `company`,
   `title`, `website`, … are auto-mapped. Unknown columns become custom merge
   fields. Point the import at a **List**.
3. **Templates** (optional) — or just write copy straight into the sequence.
   Tags: `{{first_name}}`, `{{first_name|there}}`, `{{company}}`, custom fields by
   name. Spintax: `{quick|brief} question`.
4. **Campaigns → New campaign.** Pick the sending mailbox and list. Build the
   sequence: step 1 sets the subject; later steps reply into that thread (leave
   their subject blank) after `wait_days`, and only fire `if_no_reply` /
   `if_no_open` if you choose.
5. Set the **sending window** (days + hours in the recipient timezone), a
   **daily cap**, and a **min/max delay** so messages trickle out.
6. **Test send** to yourself, then **Start sending**. The worker sends at most one
   message per campaign per tick, spaced by your jitter delay, and only inside the
   window.

Opens, clicks, replies and bounces show up on the campaign page. A reply or a
hard bounce stops that contact's sequence automatically and (for bounces/
unsubscribes) adds them to the global suppression list.

---

## Responsible use — read this

Phosphor is built for **legitimate B2B outreach** and transactional mail. It is
not a spam cannon and won't perform like one.

- Only email people you have a plausible reason to contact. Don't buy lists.
- Every campaign email carries a working `List-Unsubscribe` header **and** a
  visible unsubscribe link. Unsubscribes and hard bounces are honoured globally
  and permanently — do not work around that.
- Cold commercial email is regulated: **CAN-SPAM** (US) requires a real physical
  postal address and honest headers; **GDPR/PECR** (EU/UK) and **CASL** (Canada)
  are stricter and may require prior consent. Add your postal address to your
  templates and know which regime applies to your recipients.
- Keep volume low and steady, especially on a new domain/IP. Bounce rate above
  ~3–5% or spam-complaint rate above ~0.1% will get you blocked.

You are responsible for how you use it.

---

## Limitations / roadmap

- **No IMAP/POP3 daemon.** Storage is a Dovecot-compatible Maildir tree at
  `data/maildirs/<address>/`. Point Dovecot at it if you want desktop/mobile
  clients; the built-in webmail covers day-to-day use.
- Spam filtering is a light heuristic, not rspamd. Hook a real scanner into
  `smtp/spam.py` if you need one.
- Single-node, SQLite by default. Set `PHOSPHOR_DATABASE_URL` to a Postgres URL
  for larger installs (`postgresql+psycopg://…`).
- Greylisting, ARC sealing, and inbound DKIM/SPF *enforcement* are not yet
  implemented (inbound signatures are scored, not blocked).
- No built-in queue dashboard beyond the counters; inspect `outbound_messages`
  directly if delivery stalls.

## Development

```bash
# both suites are standalone scripts (run them separately — each pins its own
# throwaway data dir via env before importing the app)
python tests/test_smoke.py              # API flow: auth, domains, DKIM, contacts,
                                        # CSV import, templates, campaigns, stats
python tests/test_smtp_integration.py   # real SMTP: inbound routing + storage,
                                        # RCPT rejection, authenticated submission
```

Code layout is in **What's inside** above. The three moving parts —
FastAPI app, `aiosmtpd` listeners, and the APScheduler worker — all run in one
process and share one SQLite database (WAL mode). `run.py --api-only` starts just
the web tier, which is the fast path for UI work.

## License

MIT — see `LICENSE`.
