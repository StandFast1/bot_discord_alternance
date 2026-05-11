#!/usr/bin/env bash
# One-shot VPS bootstrap. Run as root on a fresh Debian/Ubuntu VPS:
#   curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/main/deploy/install.sh | sudo bash
# Or after cloning manually:
#   sudo bash deploy/install.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/StandFast1/bot_discord_alternance.git}"
APP_DIR="/opt/alternance-bot"
DATA_DIR="/var/lib/alternance-bot"
ENV_DIR="/etc/alternance-bot"
SERVICE_USER="alternance"

if [ "$EUID" -ne 0 ]; then
  echo "must run as root" >&2
  exit 1
fi

echo "==> installing system deps"
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip git ca-certificates curl

echo "==> creating service user $SERVICE_USER"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> cloning / updating repo at $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --all --quiet
  git -C "$APP_DIR" reset --hard origin/main --quiet
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "==> setting up python venv"
sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip --quiet
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

echo "==> creating data dir $DATA_DIR"
mkdir -p "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> creating env dir $ENV_DIR"
mkdir -p "$ENV_DIR"
chmod 750 "$ENV_DIR"
if [ ! -f "$ENV_DIR/env" ]; then
  cat > "$ENV_DIR/env" <<'EOF'
# Populated by the GitHub Actions deploy workflow.
# DO NOT edit by hand; deploy will overwrite.
DISCORD_TOKEN=
DISCORD_GUILD_ID=
DISCORD_CHANNEL_ID=
DISCORD_USER_ID=
FRANCE_TRAVAIL_CLIENT_ID=
FRANCE_TRAVAIL_CLIENT_SECRET=
DB_PATH=/var/lib/alternance-bot/offers.db
SCRAPE_INTERVAL_HOURS=2
MAX_OFFERS_PER_CYCLE=30
EOF
  chmod 640 "$ENV_DIR/env"
  chown root:"$SERVICE_USER" "$ENV_DIR/env"
fi

echo "==> installing systemd unit"
install -m 0644 "$APP_DIR/deploy/alternance-bot.service" \
  /etc/systemd/system/alternance-bot.service
systemctl daemon-reload
systemctl enable alternance-bot.service

echo "==> done. Fill $ENV_DIR/env (or let GitHub Actions deploy do it), then:"
echo "      systemctl start alternance-bot"
echo "      journalctl -u alternance-bot -f"
