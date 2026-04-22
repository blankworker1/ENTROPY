# ENTROPY

**The Entropy Collection** — 21 suits. No owners. One system.

A ceremonial open-front waistcoat that functions as a physical interface to a permanent civic record. Each suit holds a Bitcoin wallet, carries NFC buttons that open live web interfaces, and accumulates a chain-verified history of custody, patches, and everyone who has ever worn it.

> *A ledger you can wear. A ceremony you can scan.*

---

## What this is

ENTROPY is a system of 21 numbered suits, each paired with a node — a small hardware device that stores and serves the suit's permanent record. The record is append-only, cryptographically linked, and replicated across all 21 nodes. No central server. No operator. No private key.

**Proof of Work / Wear** — the project name.  
**Built to be Worn** — the purpose.

---

## The suit — physical interfaces

### QR code (centre back)

Embroidered into the centre of the black canvas back panel, above the martingale strap. Scan with any phone camera — no app required.

**Opens:** `http://[NODE_IP]:5000/` — the suit's full public dashboard.  
**Shows:** Bitcoin balance (live from blockchain), custodian history, event log, portrait gallery of everyone who has worn it.

---

### NFC buttons — what each tap opens

The suit carries five buttons. Four contain NFC tags. One is uncoded (reserved for future amendment).

| Button | Position | Tap opens | Auth |
|---|---|---|---|
| **Right chest panel** | Exterior — hair-on-hide | `/` Full dashboard | None |
| **Left chest panel** | Exterior — hair-on-hide | `mempool.space/xpub/[XPUB]` Raw blockchain | None |
| **Right front opening edge** | Exterior — front edge | `/worn` Selfie / WORN capture | None |
| **Interior lining, left chest** | Interior — hidden | `/admin` Custodian write access | PIN required |
| **Back yoke join, centre** | Exterior — back | Uncoded — no tag | — |

**Right chest** — anyone taps to see the full civic record: custody history, patches, events, Bitcoin balance.

**Left chest** — resolves directly to mempool.space showing the raw xpub wallet. The sceptic's button. No dashboard, no framing — just the blockchain.

**Right front opening edge** — opens the WORN selfie page. The person taps while wearing the suit, photographs themselves with the front-facing camera, enters name and location, confirms consent. The entry is held pending custodian review.

**Interior lining** — the custodian's private write button. Not visible when the suit is worn. Tap + PIN opens the admin screen for writing TRANSFER, PATCH, NOTE events and reviewing WORN submissions.

---

### Programming the NFC buttons

Each button uses an NTAG215 tag programmed with a URL record (NDEF Type URI). Use any Android NFC writing app (NFC Tools is recommended) before the tags are embedded.

```
Right chest:   http://[NODE_IP]:5000/
Left chest:    https://mempool.space/xpub/[XPUB_FOR_THIS_JACKET]
Front edge:    http://[NODE_IP]:5000/worn?jacket=[JACKET_NUMBER]
Inner lining:  http://[NODE_IP]:5000/admin?jacket=[JACKET_NUMBER]
```

Replace `[NODE_IP]` with the Pi's local network address (e.g. `192.168.1.42`).

> **HTTPS note:** The camera API used by the WORN selfie page (`/worn`) requires HTTPS when accessed from a remote device. HTTP works fine on the local network during development. For public events, expose the node temporarily via HTTPS tunnel (e.g. `cloudflared tunnel --url http://localhost:5000`) and update the front-edge NFC tag URL for the duration of the event.

---

## Hardware

Each node runs on:

- **Raspberry Pi Zero W** — single-board computer with onboard WiFi
- **MicroSD card** (16GB minimum) — contains OS, node software, and chain data
- **USB power supply** — standard 5V micro-USB, draws under 1 watt
- **Custom numbered enclosure** — aluminium faceplate, silicone chassis, numbered 01–21

The onboard ACT LED indicates node status:

| Pattern | Meaning |
|---|---|
| Three rapid blinks | Boot confirmation |
| Solid on | Synced — healthy, peers connected |
| Slow pulse (1s on / 1s off) | Syncing — catching up with peers |
| Double blink, pause, repeat | Solo — no peers reachable |
| SOS (··· --- ···) | Chain integrity failure — act immediately |

---

## Repository contents

```
chain.py              Core chain logic — events, hashing, verification
crypto.py             Key derivation (PBKDF2), signing (secp256k1)
genesis.py            Founding tool — writes genesis block (run once per jacket)
led.py                ACT LED controller — flash patterns for node status
server.py             Flask server — all HTTP endpoints
sync.py               Background sync daemon — peer gossip
test_chain.py         Integration tests
requirements.txt      Python dependencies
setup.sh              Interactive setup script for the Pi

static/
  dashboard.html      Public jacket record
  admin.html          Custodian write interface (PIN protected)
  system.html         Fleet overview — all 21 jackets
  protocol.html       The full Jacket Protocol
  worn.html           Public WORN selfie capture
  entropy_mark_black.svg
  entropy_mark_gold.svg
  entropy_mark_white.svg

resources/
  jacket_protocol.docx    The Jacket Protocol — governing document
```

---

## Setup on Pi Zero W

### 1. Flash the OS

Download **Raspberry Pi OS Lite (32-bit)** from raspberrypi.com and flash to microSD using Raspberry Pi Imager. In the imager's advanced settings, enable SSH and pre-configure your WiFi credentials before writing.

### 2. SSH into the Pi and install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip git
pip3 install -r requirements.txt --break-system-packages
```

### 3. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/entropy.git
cd entropy
```

### 4. Run the setup script

```bash
chmod +x setup.sh
./setup.sh
```

The script prompts for the jacket number and peer addresses, writes the `.env` file, creates the data directory, sets LED permissions, and installs the systemd service. The node will auto-start on every boot after this.

### 5. Initialise the chain (founding body only — once per jacket)

```bash
source .env
python3 genesis.py
```

You will need the jacket's xpub, the jacket number, the first custodian's name and PIN, and the founding private key. The founding key must be destroyed immediately after all 21 genesis blocks are written and verified across all nodes.

### 6. Run the integration tests

```bash
python3 test_chain.py
```

All tests must pass before going live.

### 7. Start the node

```bash
sudo systemctl start entropy
sudo systemctl status entropy
```

Dashboard at `http://[PI_IP]:5000`

---

## setup.sh

```bash
#!/bin/bash
# ENTROPY node setup — run once after cloning the repo

set -e

echo ""
echo "ENTROPY Node Setup"
echo "=================="
echo ""

read -p "Jacket number (01-21): " JACKET_ID
read -p "Peer URLs (comma-separated, blank if none yet): " JACKET_PEERS

INSTALL_DIR="$(pwd)"

# Write environment file
cat > .env << ENVEOF
export JACKET_ID="${JACKET_ID}"
export JACKET_DATA_DIR="${INSTALL_DIR}/data"
export JACKET_STATIC_DIR="${INSTALL_DIR}/static"
export JACKET_PEERS="${JACKET_PEERS}"
ENVEOF

echo ".env written."

# Data directory
mkdir -p data
echo "data/ directory created."

# LED permissions
sudo usermod -a -G gpio pi 2>/dev/null || true
echo "GPIO permissions set."

# Systemd service
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

echo ""
echo "Setup complete. Jacket ${JACKET_ID} node configured."
echo "Next: run 'python3 genesis.py' to initialise the chain (founding body only)."
echo "Then: run 'sudo systemctl start entropy' to start the node."
echo ""
```

Add this file to the repo root as `setup.sh`.

---

## LED permissions (manual)

If the LED does not respond after setup:

```bash
sudo usermod -a -G gpio pi
sudo chmod g+w /sys/class/leds/ACT/brightness
sudo chmod g+w /sys/class/leds/ACT/trigger
```

Test all LED patterns on the Pi directly:

```bash
python3 led.py
```

---

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/admin` | Admin screen |
| GET | `/system` | Fleet overview |
| GET | `/protocol` | Full protocol document |
| GET | `/worn` | Public WORN selfie capture |
| GET | `/api/jacket/<id>` | Chain summary |
| GET | `/api/jacket/<id>/events` | All events |
| GET | `/api/jacket/<id>/tip` | Latest hash and chain length |
| GET | `/api/all` | All jackets on this node |
| POST | `/api/jacket/<id>/write` | Write PATCH or NOTE (custodian auth) |
| POST | `/api/jacket/<id>/transfer/initiate` | Initiate transfer |
| POST | `/api/jacket/<id>/transfer/confirm` | Confirm transfer |
| POST | `/api/jacket/<id>/worn/submit` | Public WORN submission |
| GET | `/api/jacket/<id>/worn/pending` | Pending WORN queue |
| POST | `/api/jacket/<id>/worn/approve` | Approve WORN submission |
| POST | `/api/jacket/<id>/worn/discard` | Discard WORN submission |
| GET | `/api/sync/status` | Chain tips for all jackets |
| GET | `/api/sync/daemon` | Sync daemon statistics |

---

## The five event types

| Type | Auth | Description |
|---|---|---|
| `GENESIS` | Founding key | Written once at fabrication. Never again. |
| `TRANSFER` | Two signatures | Outgoing and incoming custodian both sign. |
| `PATCH` | Custodian PIN | Records a material addition to the suit. |
| `NOTE` | Custodian PIN | Freeform — wearings, events, observations. |
| `WORN` | None (public) | Selfie portrait, held for custodian review. |

---

## Security note — before production deployment

The admin screen PIN signing uses Web Crypto API with P-256 curve. The Python server uses secp256k1. These must match before deploying to production. Requires adding `noble-secp256k1` to `admin.html`. This affects only the custodian write flow — chain integrity, data storage, and all public-facing screens are unaffected.

---

## The protocol

Full governing document at `/protocol` on any running node and in `resources/jacket_protocol.docx`.

The system is governed by the custodian collective. The founding body dissolves after genesis. No override. No retained keys. No audit trails.

21 suits. No owners. One system.

---

*ENTROPY — Proof of Work / Wear — Built to be Worn*
