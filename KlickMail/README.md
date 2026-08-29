# KLICK_MAIL // V.1.0.0

**Custom email client, mailing-focused, free.** Runs on your laptop, connects to
a **Phosphor** mail server over the network. HTML composition, contacts list,
mass mailing — nothing else in the way.

```
KLICK_MAIL // CLIENT            PHOSPHOR // SERVER (other machine)
┌──────────────────────┐        ┌─────────────────────────────────┐
│ inbox / compose      │        │ MX :25   inbound mail           │
│ contacts + csv       │  HTTPS │ SMTP :587 submission            │
│ mass mailing         ├───────►│ :8000    JSON API  ◄── you talk │
│ (this laptop)        │  token │ DKIM sign / queue / sequences   │
└──────────────────────┘        └─────────────────────────────────┘
```

The client is a static web app. It stores only your server URL + login token in
the browser's local storage, and sends data to nothing except the Phosphor
endpoint you point it at.

---

## Run it (this laptop)

Needs **Python 3.8+** (that's it — the launcher is a stdlib static file server).

```bash
cd KlickMail
python klickmail.py
```

A browser tab opens at `http://127.0.0.1:8765`. On first run:

1. **Endpoint** — the Phosphor API URL, e.g. `http://192.168.1.50:8000` (LAN),
   `http://100.x.y.z:8000` (Tailscale), or `https://xxxx.trycloudflare.com`.
2. **PING()** — verifies it's a real Phosphor server and shows its domain.
3. **Operator email + password** — the Phosphor operator account. **CONNECT()**.

Flags:

```bash
python klickmail.py --server http://100.100.1.1:8000   # pre-fill the endpoint
python klickmail.py --port 9000
python klickmail.py --no-browser
```

## Optional: package as a desktop app

```bash
cd KlickMail/electron
npm install
npm start          # run the Electron window
npm run dist        # build an installer (Win .exe / Linux AppImage+deb / mac .dmg)
```

---

## What it does

| Tab | |
|-----|--|
| **INBOX** | Per-mailbox folder view (INBOX/Sent/Archive/Spam/Trash), read pane, reply, archive, spam, delete. |
| **COMPOSE** | From/To/Cc/Subject, TEXT or HTML mode with a live preview. Queues through Phosphor's submission path. |
| **CONTACTS** | Add / edit / delete, search, **IMPORT_CSV()** with auto header-mapping; unknown columns become merge fields. |
| **MASS_MAIL** | Pick a list + sending mailbox, write an HTML body with `{{first_name\|there}}` merge tags and `{quick\|brief}` spintax, set a daily cap + delay range, **LAUNCH()**. Runs as a throttled Phosphor campaign; the table below tracks sent / open / reply / bounce and lets you PAUSE()/RESUME(). |
| **SPECS** | The KlickMail spec sheet + live connection info + **DISCONNECT()**. |

Sending, DKIM signing, the outbound queue, open/click tracking, bounce handling
and suppression all happen **server-side in Phosphor** — KlickMail is just the
face.

---

## Connecting the two machines

Phosphor binds `0.0.0.0:8000` and accepts bearer-token API calls from any origin,
so you only need a network path from this laptop to the server:

- **Same Wi-Fi / LAN** — use the server's local IP.
- **Tailscale** *(recommended for two laptops on different networks)* —
  `tailscale up` on both, then use the server's `100.x` address. Encrypted, no
  ports exposed to the internet.
- **Cloudflare Tunnel** — on the server run
  `cloudflared tunnel --url http://localhost:8000`; paste the printed
  `https://<name>.trycloudflare.com` into KlickMail's endpoint field. Good for a
  quick share or phone access.
- **Production** — put Phosphor behind Caddy/nginx on `mail.example.com` with
  real TLS and point KlickMail there.

Once you know the client's origin, lock the server down:
`PHOSPHOR_CORS_ORIGINS=http://localhost:8765` in Phosphor's `.env`.

## License

MIT.
