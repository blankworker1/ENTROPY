# ENTROPY

**The Entropy Collection** — 21 garments. No owners. One system.

A ceremonial waistcoat that functions as a physical interface to a permanent civic record. Each suit holds a Bitcoin wallet, carries NFC buttons that link to a live dashboard, and accumulates a chain-verified history of custody, patches, and everyone who has ever worn it.

> *A ledger you can wear. A ceremony you can scan.*

---

## What this is

ENTROPY is a system of 21 numbered suits, each paired with a node — a small hardware device that stores and serves the suit's permanent record. The record is append-only, cryptographically linked, and replicated across all 21 nodes. No central server. No operator. No private key.

**Proof of Work / Wear** — the project name.
**Built to be Worn** — the purpose.

---

## Repository contents

```
chain.py          Core chain logic — event structure, hashing, verification
crypto.py         Key derivation (PBKDF2), signing, verification (secp256k1)
genesis.py        Founding tool — writes genesis block for one jacket
led.py            Onboard ACT LED controller — flash patterns for node status
server.py         Flask HTTP server — dashboard, admin, API, sync endpoints
sync.py           Background sync daemon — gossips chain state with peers
test_chain.py     Integration tests — run before deploying

static/
  dashboard.html  Public jacket record — Bitcoin balance, custody history, events
  admin.html      Custodian write interface — PIN protected, mobile-first
  system.html     Full fleet view — all 21 jackets, aggregate Bitcoin
  protocol.html   The full Jacket Protocol document
  worn.html       Public selfie page — WORN event capture, no auth required
  entropy_mark_black.svg   ENTROPY mark — black
  entropy_mark_gold.svg    ENTROPY mark — gold
  entropy_mark_white.svg   ENTROPY mark — white

resources/
  jacket_protocol.docx     The Jacket Protocol — full governing document
```

---

## Hardware

Each node runs on:

- **Raspberry Pi Zero W** — single-board computer with onboard WiFi
- **MicroSD card** (16GB minimum) — contains OS, node software, and chain data
- **USB power supply** — standard 5V micro-USB, draws under 1 watt
- **Custom numbered enclosure** — aluminium faceplate, silicone chassis, numbered 01–21

The onboard ACT LED indicates node status via flash patterns:

| Pattern | Meaning |
|---|---|
| Solid on | Synced — chain healthy, peers connected |
| Slow pulse | Syncing — catching up with peers |
| Double blink | Solo — online but no peers reachable |
| Three rapid blinks | Boot confirmation |
| SOS | Chain integrity failure — act immediately |

---

## Setup on Pi Zero W

### 1. Flash the OS

Download Raspberry Pi OS Lite (32-bit) and flash to microSD using Raspberry Pi Imager. Enable SSH and set your WiFi credentials in the imager before writing.

### 2. Install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip git
pip3 install flask cryptography requests --break-system-packages
```

### 3. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/entropy.git
cd entropy
```

### 4. Create the static directory structure

```bash
mkdir -p static
mv *.html static/
mv *.svg static/
```

### 5. Set environment variables

Create a `.env` file or add to `/etc/environment`:

```bash
export JACKET_ID="01"                          # This node's jacket number (01–21)
export JACKET_DATA_DIR="/home/pi/entropy/data" # Where chain data is stored
export JACKET_STATIC_DIR="/home/pi/entropy/static"
export JACKET_PEERS=""                         # Comma-separated peer URLs when known
                                               # e.g. "http://192.168.1.10:5000,http://192.168.1.11:5000"
```

### 6. Initialise the chain (first run only)

```bash
python3 genesis.py
```

This writes the genesis block for this jacket. You will need:
- The jacket's xpub (Bitcoin extended public key)
- The jacket number
- The first custodian's name and chosen PIN
- The founding private key (held by founding body, destroyed after all 21 genesis blocks written)

### 7. Run the server

```bash
source .env
python3 server.py
```

The node starts on port 5000. Access the dashboard at `http://[PI_IP_ADDRESS]:5000`

### 8. Run as a service (auto-start on boot)

Create `/etc/systemd/system/entropy.service`:

```ini
[Unit]
Description=ENTROPY Node
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/entropy
EnvironmentFile=/home/pi/entropy/.env
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable entropy
sudo systemctl start entropy
sudo systemctl status entropy
```

---

## LED permissions

The ACT LED is controlled via sysfs. Add the pi user to the gpio group:

```bash
sudo usermod -a -G gpio pi
sudo chmod g+w /sys/class/leds/ACT/brightness
sudo chmod g+w /sys/class/leds/ACT/trigger
```

Or run the server with `sudo` during testing.

---

## Testing

Run the integration test suite before deploying:

```bash
python3 test_chain.py
```

All tests should pass. If any fail, do not proceed to genesis.

Test the LED patterns directly on the Pi:

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
| GET | `/worn` | Public selfie / WORN capture |
| GET | `/api/jacket/<id>` | Chain summary for one jacket |
| GET | `/api/jacket/<id>/events` | All events for one jacket |
| GET | `/api/jacket/<id>/tip` | Latest event hash and chain length |
| GET | `/api/all` | Summary of all jackets on this node |
| POST | `/api/jacket/<id>/write` | Write PATCH or NOTE event (custodian auth) |
| POST | `/api/jacket/<id>/transfer/initiate` | Initiate custody transfer |
| POST | `/api/jacket/<id>/transfer/confirm` | Confirm custody transfer |
| POST | `/api/jacket/<id>/worn/submit` | Public WORN submission (no auth) |
| GET | `/api/jacket/<id>/worn/pending` | Pending WORN submissions |
| POST | `/api/jacket/<id>/worn/approve` | Approve a WORN submission |
| POST | `/api/jacket/<id>/worn/discard` | Discard a WORN submission |
| GET | `/api/sync/status` | Chain tips for all jackets on this node |
| GET | `/api/sync/daemon` | Sync daemon statistics |

---

## Security note

**Before finalising production deployment**, the admin screen key derivation must be updated. The current implementation uses Web Crypto API with P-256 curve. The Python server uses secp256k1. These curves must match for PIN signing to verify correctly. Integration requires adding the `noble-secp256k1` library to `admin.html` and `worn.html`.

This does not affect chain integrity or data storage — only the PIN authentication flow on the admin screen.

---

## The five event types

| Type | Auth | Description |
|---|---|---|
| `GENESIS` | Founding key | Written once at fabrication. Never again. |
| `TRANSFER` | Two signatures | Outgoing and incoming custodian both sign. |
| `PATCH` | Custodian PIN | Records a material addition to the suit. |
| `NOTE` | Custodian PIN | Freeform record — wearings, events, observations. |
| `WORN` | None (public) | Selfie portrait submission, held for custodian review. |

---

## The protocol

The full governing document is at `/protocol` on any running node, and in `resources/jacket_protocol.docx`.

The system is governed by the custodian collective. The founding body dissolves after genesis. No override. No retained keys.

21 suits. No owners. One system.

---

*ENTROPY — Proof of Work / Wear — Built to be Worn*
