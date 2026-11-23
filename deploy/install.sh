#!/bin/sh
# Installs (or updates) the Haav telemetry stack on the Raspberry Pi. Safe to re-run.
#   sudo ./deploy/install.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo" >&2
  exit 1
fi

HERE=$(cd "$(dirname "$0")" && pwd)

# --- Grafana Alloy (official apt repo, arm64) -------------------------------
if ! command -v alloy >/dev/null 2>&1; then
  apt-get update
  apt-get install -y gpg curl
  mkdir -p /etc/apt/keyrings
  curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
    > /etc/apt/sources.list.d/grafana.list
  apt-get update
  apt-get install -y alloy
fi

# --- Grafana Cloud credentials ----------------------------------------------
mkdir -p /etc/haav
if [ ! -f /etc/haav/grafana-cloud.env ]; then
  install -m 600 "$HERE/grafana-cloud.env.example" /etc/haav/grafana-cloud.env
  echo ">>> Fill in /etc/haav/grafana-cloud.env, then re-run this script." >&2
  exit 1
fi
if grep -q REPLACE_ME /etc/haav/grafana-cloud.env; then
  echo ">>> /etc/haav/grafana-cloud.env still has placeholder values." >&2
  exit 1
fi
for var in GRAFANA_CLOUD_PROM_URL GRAFANA_CLOUD_PROM_USER GRAFANA_CLOUD_LOKI_URL GRAFANA_CLOUD_LOKI_USER GRAFANA_CLOUD_API_TOKEN; do
  if ! grep -Eq "^${var}=.+" /etc/haav/grafana-cloud.env; then
    echo ">>> /etc/haav/grafana-cloud.env is missing ${var} (see deploy/grafana-cloud.env.example)." >&2
    exit 1
  fi
done

# Let Alloy read systemd-journald for Loki log shipping.
usermod -aG systemd-journal alloy

# Feed the credentials to the alloy service without touching the package's /etc/default/alloy.
mkdir -p /etc/systemd/system/alloy.service.d
cat > /etc/systemd/system/alloy.service.d/haav.conf <<'EOF'
[Service]
EnvironmentFile=/etc/haav/grafana-cloud.env
EOF

# --- Pi firmware health -> textfile collector -------------------------------
install -d -m 755 /usr/local/lib/haav /var/lib/haav/textfile
install -m 755 "$HERE/pi-health/pi-health.sh" /usr/local/lib/haav/pi-health.sh
install -m 644 "$HERE/systemd/haav-pi-health.service" /etc/systemd/system/
install -m 644 "$HERE/systemd/haav-pi-health.timer" /etc/systemd/system/

# --- Pixhawk MAVLink -> metrics + events ------------------------------------
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3-venv
fi
install -d -m 755 /opt/haav
# Check for pip, not python: an interrupted venv creation leaves bin/python without bin/pip.
if [ ! -x /opt/haav/mavlink-venv/bin/pip ]; then
  python3 -m venv --clear /opt/haav/mavlink-venv
fi
/opt/haav/mavlink-venv/bin/pip install --quiet --disable-pip-version-check -r "$HERE/mavlink/requirements.txt"
install -m 755 "$HERE/mavlink/haav-mavlink.py" /usr/local/lib/haav/haav-mavlink.py
if [ ! -f /etc/haav/mavlink.env ]; then
  install -m 644 "$HERE/mavlink.env.example" /etc/haav/mavlink.env
  echo ">>> Created /etc/haav/mavlink.env; set HAAV_MAV_DEVICE to your radio/Pixhawk port." >&2
fi
install -m 644 "$HERE/systemd/haav-mavlink.service" /etc/systemd/system/

# --- Alloy config -----------------------------------------------------------
install -m 644 "$HERE/alloy/config.alloy" /etc/alloy/config.alloy

systemctl daemon-reload
systemctl enable --now haav-pi-health.timer
systemctl enable haav-mavlink
systemctl restart haav-mavlink
systemctl enable alloy
systemctl restart alloy

echo "Installed. Check: systemctl status alloy   |   Alloy UI: http://localhost:12345"
