#!/bin/bash
# Configure demo-specific settings: autostart, MOTD, sample data
set -euo pipefail

DEMO_DIR="/home/${DEMO_USER:-demo}/ai-employee"
DEMO_USER="${DEMO_USER:-demo}"

echo "==> Configuring autostart service..."
sudo tee /etc/systemd/system/ai-employee.service > /dev/null <<EOF
[Unit]
Description=AI Employee Demo Stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${DEMO_DIR}
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
User=${DEMO_USER}
Group=docker
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ai-employee.service

echo "==> Writing MOTD..."
sudo tee /etc/motd > /dev/null <<'EOF'

╔═══════════════════════════════════════════════════════════╗
║              AI Employee Demo VM                          ║
║                                                           ║
║  Web UI:    http://localhost:3000  (or host port 3000)    ║
║  API:       http://localhost:8000  (or host port 8000)    ║
║                                                           ║
║  Start:     sudo systemctl start ai-employee              ║
║  Stop:      sudo systemctl stop ai-employee               ║
║  Logs:      cd ~/ai-employee && docker compose logs -f    ║
║  Config:    ~/ai-employee/.env                            ║
║                                                           ║
║  Credentials: demo / demo                                 ║
║  NOTE: Add your ANTHROPIC_API_KEY to ~/ai-employee/.env   ║
╚═══════════════════════════════════════════════════════════╝

EOF

echo "==> Creating helper scripts..."
sudo -u "${DEMO_USER}" tee "/home/${DEMO_USER}/start-demo.sh" > /dev/null <<'SCRIPT'
#!/bin/bash
cd ~/ai-employee
echo "Starting AI Employee..."
docker compose up -d
echo ""
echo "AI Employee is starting. Access it at:"
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:8000"
echo ""
echo "Watch logs: docker compose logs -f"
SCRIPT
chmod +x "/home/${DEMO_USER}/start-demo.sh"

sudo -u "${DEMO_USER}" tee "/home/${DEMO_USER}/stop-demo.sh" > /dev/null <<'SCRIPT'
#!/bin/bash
cd ~/ai-employee
echo "Stopping AI Employee..."
docker compose down
echo "Stopped."
SCRIPT
chmod +x "/home/${DEMO_USER}/stop-demo.sh"

echo "==> Demo configuration complete."
