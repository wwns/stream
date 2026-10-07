#!/bin/bash
# =============================================================
#  RpiStream - skrypt instalacyjny dla Raspberry Pi 3B
#  Uruchom jako root: sudo bash install.sh
# =============================================================

set -e
GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${CYAN}[RpiStream] Instalacja zależności...${NC}"

apt-get update -qq
apt-get install -y ffmpeg python3-pip v4l-utils python3-flask

# pip (Flask może być już z apt)
pip3 install flask --break-system-packages 2>/dev/null || true

# Kopia serwera
cp stream_server.py /opt/stream_server.py
chmod +x /opt/stream_server.py

echo -e "${GREEN}[OK] Pliki skopiowane${NC}"

# Systemd service
cat > /etc/systemd/system/rpistream.service << 'EOF'
[Unit]
Description=RPi HDMI Stream Server
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/stream_server.py
WorkingDirectory=/opt
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
User=pi

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable rpistream
systemctl restart rpistream

IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  RpiStream zainstalowany i uruchomiony!${NC}"
echo -e "${GREEN}  GUI:  http://${IP}:8080${NC}"
echo -e "${GREEN}  HLS:  http://${IP}:8080/hls/stream.m3u8${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""
echo -e "Logi: ${CYAN}journalctl -u rpistream -f${NC}"
