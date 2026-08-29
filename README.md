# Phosphor Suite

Two halves of a self-hosted mailing setup:

| Folder | What | Runs on |
|--------|------|---------|
| [`Phosphor/`](Phosphor/) | Mail **server** — inbound MX, authenticated submission, per-domain DKIM, DNS record generation, mailbox storage, outbound queue, cold-email campaign engine, web control panel. | A Linux box (recommended) |
| [`KlickMail/`](KlickMail/) | Mail **client** — brutalist black/white desktop app that talks to a Phosphor server over its JSON API. Inbox, compose, contacts, mass mailing. | Your laptop |

## Quick start (server, Linux)

```bash
cd Phosphor
./scripts/setup-linux.sh --caps --systemd
# edit .env: PHOSPHOR_PRIMARY_DOMAIN / PHOSPHOR_SERVER_HOSTNAME / PHOSPHOR_PUBLIC_URL
./.venv/bin/python run.py            # or: systemctl status phosphor
```

Open `http://<host>:8000`, create the operator account, add your domain (DKIM is
generated), publish the DNS records it shows.

## Quick start (client)

```bash
cd KlickMail
python klickmail.py                  # opens http://127.0.0.1:8765
```

Point it at the Phosphor endpoint, log in with the operator account.

Full docs in each folder's `README.md`.
