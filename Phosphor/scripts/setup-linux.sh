#!/usr/bin/env bash
# Phosphor — one-shot Linux setup.
#   ./scripts/setup-linux.sh            # venv + deps + .env
#   ./scripts/setup-linux.sh --caps     # also grant the venv python cap_net_bind_service (ports 25/587)
#   ./scripts/setup-linux.sh --systemd  # also install & enable the systemd unit
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "python3 not found"; exit 1; }

echo "==> Creating virtualenv (.venv)"
"$PY" -m venv .venv
./.venv/bin/pip install --upgrade pip -q
echo "==> Installing dependencies"
./.venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
  cp .env.example .env
  SECRET="$(./.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(48))')"
  sed -i "s|^PHOSPHOR_SECRET_KEY=.*|PHOSPHOR_SECRET_KEY=${SECRET}|" .env
  echo "==> Wrote .env with a fresh PHOSPHOR_SECRET_KEY — edit PRIMARY_DOMAIN / SERVER_HOSTNAME / PUBLIC_URL now."
else
  echo "==> .env already exists, leaving it alone."
fi

for arg in "$@"; do
  case "$arg" in
    --caps)
      REAL_PY="$(readlink -f ./.venv/bin/python)"
      echo "==> setcap cap_net_bind_service on ${REAL_PY} (needs sudo)"
      sudo setcap 'cap_net_bind_service=+ep' "$REAL_PY"
      ;;
    --systemd)
      echo "==> Installing systemd unit (needs sudo)"
      SVC=/etc/systemd/system/phosphor.service
      sudo cp scripts/phosphor.service "$SVC"
      sudo sed -i "s|/opt/phosphor|${HERE}|g" "$SVC"
      sudo sed -i "s|^User=.*|User=$(id -un)|" "$SVC"
      sudo systemctl daemon-reload
      sudo systemctl enable --now phosphor
      sudo systemctl --no-pager status phosphor | head -12
      ;;
  esac
done

echo
echo "Done. Start it with:  ./.venv/bin/python run.py"
echo "Or API only:          ./.venv/bin/python run.py --api-only"
