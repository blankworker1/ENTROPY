#!/bin/bash
# ENTROPY node setup
# Run once after cloning the repo onto the Pi Zero W.
# Creates .env, data directory, LED permissions, and systemd service.

set -e

echo ""
echo "ENTROPY Node Setup"
echo "=================="
echo ""

read -p "Jacket number (01-21): " JACKET_ID
read -p "Peer node URLs (comma-separated, leave blank if none yet): " JACKET_PEERS

INSTALL_DIR="$(pwd)"

# ── Environment file ──────────────────────────────────────────────────────────
cat > .env << ENVEOF
export JACKET_ID="${JACKET_ID}"
export JACKET_DATA_DIR="${INSTALL_DIR}/data"
export JACKET_STATIC_DIR="${INSTALL_DIR}/static"
export JACKET_PEERS="${JACKET_PEERS}"
ENVEOF
echo "✓ .env written"

# ── Data directory ────────────────────────────────────────────────────────────
mkdir -p data
echo "✓ data/ directory created"

# ── LED permissions ───────────────────────────────────────────────────────────
sudo usermod -a -G gpio pi 2>/dev/null || true
echo "✓ GPIO permissions set"

# ── Systemd service ───────────────────────────────────────────────────────────
sudo tee /etc/systemd/system/entropy.service > /dev/null << SVCEOF
[Unit]
Description=ENTROPY Node ${JACKET_ID}
After=network.target

[Service]
User=pi
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable entropy
echo "✓ Systemd service installed and enabled (auto-starts on boot)"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Setup complete."
echo ""
echo "Jacket ${JACKET_ID} node configured at ${INSTALL_DIR}"
echo ""
echo "Next steps:"
echo "  1. Run 'python3 test_chain.py' to verify the software is working"
echo "  2. Run 'source .env && python3 genesis.py' to initialise the chain"
echo "     (founding body only — requires the xpub and founding private key)"
echo "  3. Run 'sudo systemctl start entropy' to start the node"
echo "  4. Access the dashboard at http://$(hostname -I | awk '{print $1}'):5000"
echo ""
