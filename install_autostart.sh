#!/bin/bash
# Installs MulseCube as two systemd services, so the dashboard and the sensor
# loop both start by themselves every time the Pi boots.
#
#   Install (from inside your MulseCube folder):  sudo bash install_autostart.sh
#   Undo:                                         sudo bash install_autostart.sh --uninstall

set -e

UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"   # only overridden when testing

if [ "$EUID" -ne 0 ]; then
  echo "Please run this with sudo:  sudo bash install_autostart.sh"
  exit 1
fi

if [ "$1" = "--uninstall" ]; then
  systemctl disable --now mulsecube-main.service mulsecube-hmi.service 2>/dev/null || true
  rm -f "$UNIT_DIR/mulsecube-main.service" "$UNIT_DIR/mulsecube-hmi.service"
  systemctl daemon-reload
  echo "Auto-start removed. You can launch things by hand again."
  exit 0
fi

# The project folder is wherever this script lives; the dashboard runs as
# the normal user, the sensor loop as root (it needs it for GPIO overlays).
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$PROJECT_DIR")}"
PYTHON_BIN="$(command -v python3)"

# Make sure we're in the right folder before writing anything
for item in main.py hmi_server.py static/fonts drivers profiles; do
  if [ ! -e "$PROJECT_DIR/$item" ]; then
    echo "Can't find '$item' in $PROJECT_DIR"
    echo "Copy this script into your MulseCube folder and run it from there."
    exit 1
  fi
done

# Warn (don't stop) if a package the services need isn't visible to them
if ! "$PYTHON_BIN" -c "import requests, serial, smbus2, gpiozero" 2>/dev/null; then
  echo "WARNING: as root, python3 can't import one of: requests, pyserial, smbus2, gpiozero."
  echo "         Fix with:  sudo pip install requests pyserial smbus2 --break-system-packages"
fi
if ! sudo -u "$RUN_USER" "$PYTHON_BIN" -c "import flask" 2>/dev/null; then
  echo "WARNING: user '$RUN_USER' can't import flask."
  echo "         Fix with:  pip install flask --break-system-packages"
fi

# Warn if something is already holding port 5000 (e.g. a hand-started dashboard)
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ':5000 '; then
  echo "NOTE: something is already listening on port 5000 right now."
  echo "      Stop any hand-started 'python3 hmi_server.py' before testing the service."
fi

cat > "$UNIT_DIR/mulsecube-hmi.service" << EOF
[Unit]
Description=MulseCube HMI dashboard
After=local-fs.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON_BIN $PROJECT_DIR/hmi_server.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > "$UNIT_DIR/mulsecube-main.service" << EOF
[Unit]
Description=MulseCube sensor detection and reading loop
After=mulsecube-hmi.service
Wants=mulsecube-hmi.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON_BIN $PROJECT_DIR/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mulsecube-hmi.service mulsecube-main.service

echo
echo "Installed. Project folder: $PROJECT_DIR   Dashboard user: $RUN_USER"
echo "Reboot to try it:  sudo reboot"
echo "Then open  http://localhost:5000  and answer the sensor questions."
