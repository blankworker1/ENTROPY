"""
server.py — Jacket node HTTP server
Serves the chain as JSON endpoints and handles event writes.
Runs on the Pi Zero W. Accessible on the local network.
The dashboard and admin screen are served as static HTML from this server.
"""

import json
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, request, abort, send_from_directory

from chain import (
    PATCH, NOTE, TRANSFER,
    build_event, attach_signature,
    append_event, load_chain, verify_chain,
    chain_summary, latest_hash, load_chain
)
from crypto import (
    pubkey_from_pin,
    sign_event_id,
    verify_event_signature,
    verify_signature,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR   = os.environ.get("JACKET_DATA_DIR", "./data")
STATIC_DIR = os.environ.get("JACKET_STATIC_DIR", "./static")
NODE_ID    = os.environ.get("JACKET_ID", "01")      # which jacket this node is for
PEERS      = os.environ.get("JACKET_PEERS", "").split(",")  # comma-separated peer URLs
PEERS      = [p.strip() for p in PEERS if p.strip()]

app = Flask(__name__, static_folder=STATIC_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_chain(jacket_id: str) -> list:
    return load_chain(DATA_DIR, jacket_id.zfill(2))


def jacket_ids() -> list[str]:
    """All jacket IDs with chain files on this node."""
    ids = []
    if not os.path.exists(DATA_DIR):
        return ids
    for fname in sorted(os.listdir(DATA_DIR)):
        if fname.startswith("jacket_") and fname.endswith(".jsonl"):
            ids.append(fname[7:9])
    return ids


def get_nfc_salt(jacket_id: str, button: str) -> str | None:
    """Retrieve the NFC salt for a specific button from the genesis block."""
    chain = get_chain(jacket_id)
    if not chain:
        return None
    return chain[0]["payload"]["nfc_salts"].get(button)


def current_custodian_pubkey(jacket_id: str) -> str | None:
    """
    Find the most recent custodian public key for a jacket.
    Walks the chain backwards looking for the latest TRANSFER or GENESIS.
    """
    chain = get_chain(jacket_id)
    for event in reversed(chain):
        if event["event_type"] in ("GENESIS", "TRANSFER"):
            if event["event_type"] == "GENESIS":
                return event["payload"]["custodian_pubkey"]
            if event["event_type"] == "TRANSFER":
                payload = event["payload"]
                # Confirmed transfer: incoming custodian is current
                if payload.get("status") == "confirmed":
                    return payload["incoming_pubkey"]
    return None


def pending_transfer(jacket_id: str) -> dict | None:
    """Return a pending (unconfirmed) TRANSFER event if one exists."""
    chain = get_chain(jacket_id)
    for event in reversed(chain):
        if event["event_type"] == TRANSFER:
            if event["payload"].get("status") == "pending":
                return event
    return None


def error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


# ---------------------------------------------------------------------------
# Public read endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    """Serve the dashboard."""
    return send_from_directory(STATIC_DIR, "dashboard.html")


@app.get("/admin")
def admin():
    """Serve the admin screen."""
    return send_from_directory(STATIC_DIR, "admin.html")


@app.get("/system")
def system():
    """Serve the full system overview."""
    return send_from_directory(STATIC_DIR, "system.html")


@app.get("/protocol")
def protocol():
    """Serve the full protocol text."""
    return send_from_directory(STATIC_DIR, "protocol.html")


@app.get("/worn")
def worn_page():
    """Serve the public selfie / WORN capture page."""
    return send_from_directory(STATIC_DIR, "worn.html")


@app.get("/entropy_mark_gold.svg")
def mark_gold():
    return send_from_directory(STATIC_DIR, "entropy_mark_gold.svg", mimetype="image/svg+xml")

@app.get("/entropy_mark_white.svg")
def mark_white():
    return send_from_directory(STATIC_DIR, "entropy_mark_white.svg", mimetype="image/svg+xml")

@app.get("/entropy_mark_black.svg")
def mark_black():
    return send_from_directory(STATIC_DIR, "entropy_mark_black.svg", mimetype="image/svg+xml")


# ---------------------------------------------------------------------------
# WORN — pending submission helpers
# ---------------------------------------------------------------------------

def worn_pending_path(jacket_id: str) -> str:
    return os.path.join(DATA_DIR, f"worn_pending_{jacket_id}.jsonl")


def load_pending_worn(jacket_id: str) -> list:
    path = worn_pending_path(jacket_id.zfill(2))
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append_pending_worn(jacket_id: str, entry: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = worn_pending_path(jacket_id.zfill(2))
    with open(path, "a") as f:
        f.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


def remove_pending_worn(jacket_id: str, submission_id: str) -> bool:
    jid      = jacket_id.zfill(2)
    path     = worn_pending_path(jid)
    if not os.path.exists(path):
        return False
    entries  = load_pending_worn(jid)
    filtered = [e for e in entries if e.get("submission_id") != submission_id]
    if len(filtered) == len(entries):
        return False
    with open(path, "w") as f:
        for e in filtered:
            f.write(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n")
    return True


# ---------------------------------------------------------------------------
# WORN — public endpoints (no PIN required)
# ---------------------------------------------------------------------------

@app.post("/api/jacket/<jacket_id>/worn/submit")
def worn_submit(jacket_id: str):
    """
    Public WORN submission — no authentication.
    Accepts selfie + name + location, holds for custodian review.
    The image is stored as a JPEG; its SHA-256 hash is stored on chain at approval.
    """
    import base64, uuid
    data = request.get_json(silent=True)
    if not data:
        return error("invalid JSON")

    name       = (data.get("name") or "").strip()
    location   = (data.get("location") or "").strip()
    image_hash = (data.get("image_hash") or "").strip()
    image_data = data.get("image_data", "")
    consented  = data.get("consented", False)

    if not name:        return error("name is required")
    if not image_hash:  return error("image_hash is required")
    if not image_data:  return error("image_data is required")
    if not consented:   return error("consent is required")

    jid     = jacket_id.zfill(2)
    pending = load_pending_worn(jid)
    if len(pending) >= 50:
        return error("submission queue full — please try again later", 429)

    # Save image to disk
    image_dir = os.path.join(DATA_DIR, f"worn_images_{jid}")
    os.makedirs(image_dir, exist_ok=True)
    safe_hash  = image_hash.replace("sha256:", "")[:64]
    image_path = os.path.join(image_dir, f"{safe_hash}.jpg")
    if image_data.startswith("data:"):
        _, encoded = image_data.split(",", 1)
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(encoded))

    submission = {
        "submission_id": str(uuid.uuid4()),
        "jacket_id":     jid,
        "name":          name,
        "location":      location,
        "note":          data.get("note", ""),
        "image_hash":    image_hash,
        "image_file":    f"{safe_hash}.jpg",
        "consented":     True,
        "submitted_at":  datetime.now(timezone.utc).isoformat(),
        "status":        "pending",
    }
    append_pending_worn(jid, submission)

    return jsonify({
        "status":        "pending",
        "submission_id": submission["submission_id"],
        "message":       "Submission received. The custodian will review and confirm.",
    }), 201


@app.get("/api/jacket/<jacket_id>/worn/pending")
def worn_pending_list(jacket_id: str):
    """Return all pending WORN submissions for custodian review."""
    pending = load_pending_worn(jacket_id.zfill(2))
    return jsonify({"jacket_id": jacket_id, "pending": pending, "count": len(pending)})


@app.get("/api/jacket/<jacket_id>/worn/approve/prepare/<submission_id>")
def worn_approve_prepare(jacket_id: str, submission_id: str):
    """
    Step 1 of approve: server builds the event and returns event_id for signing.
    The custodian signs the event_id and POSTs to /worn/approve.
    """
    from chain import WORN, build_event, latest_hash

    jid       = jacket_id.zfill(2)
    pending   = load_pending_worn(jid)
    submission = next((s for s in pending if s["submission_id"] == submission_id), None)
    if not submission:
        return error("submission not found", 404)

    payload = {
        "name":          submission["name"],
        "location":      submission["location"],
        "note":          submission["note"],
        "image_hash":    submission["image_hash"],
        "consented":     True,
        "submitted_at":  submission["submitted_at"],
        "submission_id": submission_id,
    }

    ts    = datetime.now(timezone.utc).isoformat()
    prev  = latest_hash(DATA_DIR, jid)
    pub   = current_custodian_pubkey(jid) or ''
    event = build_event(
        jacket_id        = jid,
        event_type       = WORN,
        payload          = payload,
        prev_hash        = prev,
        custodian_pubkey = pub,
        timestamp        = ts,
    )

    return jsonify({
        "event_id":  event["event_id"],
        "timestamp": ts,
        "prev_hash": prev,
    })


@app.post("/api/jacket/<jacket_id>/worn/approve")
def worn_approve(jacket_id: str):
    """
    Step 2 of approve: custodian signs the event_id from /prepare and submits.
    Writes signed WORN event to chain, removes from pending queue.
    """
    from chain import WORN, build_event, attach_signature, latest_hash, append_event

    data = request.get_json(silent=True)
    if not data:
        return error("invalid JSON")

    jid           = jacket_id.zfill(2)
    submission_id = data.get("submission_id")
    sig           = data.get("signature")
    custodian_pub = data.get("custodian_pubkey")
    ts            = data.get("timestamp", datetime.now(timezone.utc).isoformat())

    if not all([submission_id, sig, custodian_pub]):
        return error("submission_id, signature, and custodian_pubkey required")

    pending    = load_pending_worn(jid)
    submission = next((s for s in pending if s["submission_id"] == submission_id), None)
    if not submission:
        return error("submission not found", 404)

    registered_pub = current_custodian_pubkey(jid)
    if custodian_pub != registered_pub:
        return error("custodian_pubkey does not match registered custodian", 403)

    payload = {
        "name":          submission["name"],
        "location":      submission["location"],
        "note":          submission["note"],
        "image_hash":    submission["image_hash"],
        "consented":     True,
        "submitted_at":  submission["submitted_at"],
        "submission_id": submission_id,
    }

    prev  = latest_hash(DATA_DIR, jid)
    event = build_event(
        jacket_id        = jid,
        event_type       = WORN,
        payload          = payload,
        prev_hash        = prev,
        custodian_pubkey = custodian_pub,
        timestamp        = ts,
    )

    if not verify_signature(event["event_id"], sig, custodian_pub):
        return error("invalid custodian signature", 403)

    event = attach_signature(event, sig)
    append_event(DATA_DIR, jid, event)
    remove_pending_worn(jid, submission_id)

    return jsonify({
        "status":   "confirmed",
        "event_id": event["event_id"],
        "message":  f"{submission['name']} added to the chain.",
    }), 201


@app.post("/api/jacket/<jacket_id>/worn/discard")
def worn_discard(jacket_id: str):
    """Custodian discards a pending WORN submission without writing to chain."""
    data = request.get_json(silent=True)
    if not data:
        return error("invalid JSON")

    jid           = jacket_id.zfill(2)
    submission_id = data.get("submission_id")
    custodian_pub = data.get("custodian_pubkey")
    sig           = data.get("signature")

    registered_pub = current_custodian_pubkey(jid)
    if custodian_pub != registered_pub:
        return error("custodian_pubkey does not match registered custodian", 403)

    removed = remove_pending_worn(jid, submission_id)
    if not removed:
        return error("submission not found", 404)

    return jsonify({"status": "discarded", "submission_id": submission_id})


@app.get("/api/jacket/<jacket_id>/worn/image/<image_hash>")
def worn_image(jacket_id: str, image_hash: str):
    """Serve a stored WORN image by its hash."""
    from flask import send_file
    jid        = jacket_id.zfill(2)
    safe_hash  = image_hash.replace("sha256:", "")[:64]
    image_path = os.path.join(DATA_DIR, f"worn_images_{jid}", f"{safe_hash}.jpg")
    if not os.path.exists(image_path):
        return error("image not found", 404)
    return send_file(image_path, mimetype="image/jpeg")


@app.get("/api/status")
def status():
    """Node status — which jacket, how many events, sync state."""
    ids    = jacket_ids()
    chains = {jid: get_chain(jid) for jid in ids}
    return jsonify({
        "node_jacket": NODE_ID,
        "jackets":     ids,
        "chain_lengths": {jid: len(c) for jid, c in chains.items()},
        "peers":       PEERS,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/jacket/<jacket_id>")
def jacket(jacket_id: str):
    """Full chain summary for one jacket."""
    chain = get_chain(jacket_id)
    if not chain:
        return error(f"No chain found for jacket {jacket_id}", 404)
    valid, reason = verify_chain(chain)
    summary       = chain_summary(chain)
    return jsonify({**summary, "chain_valid": valid, "chain_valid_reason": reason})


@app.get("/api/jacket/<jacket_id>/events")
def jacket_events(jacket_id: str):
    """All events for one jacket. Optionally filter by type."""
    chain     = get_chain(jacket_id)
    event_type = request.args.get("type")
    if event_type:
        chain = [e for e in chain if e["event_type"] == event_type.upper()]
    # Strip signatures from public responses — not needed by the dashboard
    public = [{k: v for k, v in e.items() if k != "signature"} for e in chain]
    return jsonify({"jacket_id": jacket_id, "events": public})


@app.get("/api/jacket/<jacket_id>/events/<event_id>")
def jacket_event(jacket_id: str, event_id: str):
    """Single event by ID."""
    chain = get_chain(jacket_id)
    for event in chain:
        if event["event_id"] == event_id:
            return jsonify({k: v for k, v in event.items() if k != "signature"})
    return error("Event not found", 404)


@app.get("/api/jacket/<jacket_id>/tip")
def jacket_tip(jacket_id: str):
    """Latest event hash and chain length. Used by sync protocol."""
    chain = get_chain(jacket_id)
    if not chain:
        return jsonify({"jacket_id": jacket_id, "tip": None, "length": 0})
    return jsonify({
        "jacket_id": jacket_id,
        "tip":       chain[-1]["event_id"],
        "length":    len(chain),
    })


@app.get("/api/jacket/<jacket_id>/events/since/<event_id>")
def events_since(jacket_id: str, event_id: str):
    """
    All events after a given event_id.
    Used by peers during sync to request only what they're missing.
    """
    chain  = get_chain(jacket_id)
    found  = False
    result = []
    for event in chain:
        if found:
            result.append(event)
        if event["event_id"] == event_id:
            found = True
    if not found and event_id != "genesis":
        return error("event_id not found in chain", 404)
    return jsonify({"jacket_id": jacket_id, "events": result})


@app.get("/api/all")
def all_jackets():
    """
    Summary of all jackets on this node.
    Used by the dashboard to display the aggregate view.
    """
    result = []
    for jid in jacket_ids():
        chain = get_chain(jid)
        if chain:
            summary = chain_summary(chain)
            result.append(summary)
    return jsonify({"jackets": result, "count": len(result)})


# ---------------------------------------------------------------------------
# Authenticated write endpoints
# ---------------------------------------------------------------------------

def verify_custodian_request(jacket_id: str, event_id_to_sign: str, 
                              signature_hex: str) -> tuple[bool, str]:
    """
    Verify that a write request comes from the current custodian.
    Checks the signature against the registered custodian public key.
    Returns (valid, reason).
    """
    pubkey = current_custodian_pubkey(jacket_id)
    if not pubkey:
        return False, "no custodian public key found"
    valid = verify_signature(event_id_to_sign, signature_hex, pubkey)
    if not valid:
        return False, "signature verification failed"
    return True, "ok"


@app.post("/api/jacket/<jacket_id>/write")
def write_event(jacket_id: str):
    """
    Write a new PATCH or NOTE event to the chain.

    Request body:
    {
        "event_type": "PATCH" | "NOTE",
        "payload": { ... },
        "signature": "hex",      # custodian signs the unsigned event_id
        "custodian_pubkey": "hex"
    }

    The server builds the event, verifies the signature, then appends.
    The private key never reaches this endpoint — only the signature does.
    """
    data = request.get_json(silent=True)
    if not data:
        return error("invalid JSON")

    event_type     = data.get("event_type", "").upper()
    payload        = data.get("payload", {})
    signature_hex  = data.get("signature")
    custodian_pub  = data.get("custodian_pubkey")

    if event_type not in (PATCH, NOTE):
        return error(f"event_type must be PATCH or NOTE, got: {event_type}")
    if not signature_hex:
        return error("signature required")
    if not custodian_pub:
        return error("custodian_pubkey required")

    # Verify the claimed pubkey matches the registered custodian
    registered_pub = current_custodian_pubkey(jacket_id.zfill(2))
    if custodian_pub != registered_pub:
        return error("custodian_pubkey does not match registered custodian", 403)

    # Build the event — use client timestamp if provided so event_id matches
    prev  = latest_hash(DATA_DIR, jacket_id.zfill(2))
    event = build_event(
        jacket_id        = jacket_id.zfill(2),
        event_type       = event_type,
        payload          = payload,
        prev_hash        = prev,
        custodian_pubkey = custodian_pub,
        timestamp        = data.get("timestamp"),   # client provides this
    )

    # Verify the signature against the event_id
    if not verify_signature(event["event_id"], signature_hex, custodian_pub):
        return error("invalid signature", 403)

    # Attach signature and append
    event = attach_signature(event, signature_hex)
    append_event(DATA_DIR, jacket_id.zfill(2), event)

    return jsonify({
        "status":   "ok",
        "event_id": event["event_id"],
        "type":     event_type,
    }), 201


@app.post("/api/jacket/<jacket_id>/transfer/initiate")
def initiate_transfer(jacket_id: str):
    """
    Outgoing custodian initiates a transfer.
    Creates a TRANSFER event in 'pending' state.
    Incoming custodian must countersign via /transfer/confirm.

    Request body:
    {
        "outgoing_custodian": "Name",
        "incoming_custodian": "Name",
        "incoming_pubkey": "hex",
        "location": "City, Country",
        "witness_count": 12,
        "note": "optional",
        "image_hash": "optional sha256:...",
        "signature": "hex"           # outgoing custodian signs the event_id
        "custodian_pubkey": "hex"    # outgoing custodian's public key
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return error("invalid JSON")

    required = ["outgoing_custodian", "incoming_custodian", 
                 "incoming_pubkey", "location", "signature", "custodian_pubkey"]
    for field in required:
        if not data.get(field):
            return error(f"missing required field: {field}")

    # Only one pending transfer allowed at a time
    if pending_transfer(jacket_id.zfill(2)):
        return error("a transfer is already pending for this jacket")

    registered_pub = current_custodian_pubkey(jacket_id.zfill(2))
    if data["custodian_pubkey"] != registered_pub:
        return error("custodian_pubkey does not match registered custodian", 403)

    # Use the client's payload directly for hashing — the signature proves its integrity.
    # We only enforce that status is 'pending' and required fields are present.
    payload = {
        "status":               "pending",
        "outgoing_custodian":   data["outgoing_custodian"],
        "incoming_custodian":   data["incoming_custodian"],
        "incoming_pubkey":      data["incoming_pubkey"],
        "location":             data["location"],
        "witness_count":        data.get("witness_count", 0),
        "note":                 data.get("note", ""),
        "image_hash":           data.get("image_hash"),
    }

    # If client sent a pre-built payload with status, use it verbatim for hash parity
    if "status" in data:
        payload = {k: data.get(k, payload[k]) for k in payload}

    prev  = latest_hash(DATA_DIR, jacket_id.zfill(2))
    event = build_event(
        jacket_id        = jacket_id.zfill(2),
        event_type       = TRANSFER,
        payload          = payload,
        prev_hash        = prev,
        custodian_pubkey = data["custodian_pubkey"],
        timestamp        = data.get("timestamp"),
    )

    if not verify_signature(event["event_id"], data["signature"], data["custodian_pubkey"]):
        return error("invalid outgoing signature", 403)

    event = attach_signature(event, data["signature"])
    append_event(DATA_DIR, jacket_id.zfill(2), event)

    return jsonify({
        "status":   "pending",
        "event_id": event["event_id"],
        "message":  "Transfer initiated. Incoming custodian must confirm.",
    }), 201


@app.post("/api/jacket/<jacket_id>/transfer/confirm")
def confirm_transfer(jacket_id: str):
    """
    Incoming custodian countersigns the pending transfer.
    This completes the transfer and registers the new custodian pubkey.

    Request body:
    {
        "incoming_pubkey": "hex",
        "signature": "hex"    # incoming custodian signs the pending event_id
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return error("invalid JSON")

    pending = pending_transfer(jacket_id.zfill(2))
    if not pending:
        return error("no pending transfer found for this jacket")

    incoming_pubkey = data.get("incoming_pubkey")
    signature_hex   = data.get("signature")

    if not incoming_pubkey or not signature_hex:
        return error("incoming_pubkey and signature required")

    # Verify incoming pubkey matches what outgoing custodian registered
    if incoming_pubkey != pending["payload"]["incoming_pubkey"]:
        return error("incoming_pubkey does not match pending transfer", 403)

    # Verify the incoming custodian's signature
    if not verify_signature(pending["event_id"], signature_hex, incoming_pubkey):
        return error("invalid incoming signature", 403)

    # Update the pending event to confirmed by rewriting it
    # In an append-only chain we append a TRANSFER_CONFIRM event
    # that references the pending event_id
    confirm_payload = {
        "status":             "confirmed",
        "pending_event_id":   pending["event_id"],
        "incoming_pubkey":    incoming_pubkey,
        "incoming_signature": signature_hex,
        "incoming_custodian": pending["payload"]["incoming_custodian"],
        "outgoing_custodian": pending["payload"]["outgoing_custodian"],
        "location":           pending["payload"]["location"],
    }

    prev  = latest_hash(DATA_DIR, jacket_id.zfill(2))
    event = build_event(
        jacket_id        = jacket_id.zfill(2),
        event_type       = TRANSFER,
        payload          = confirm_payload,
        prev_hash        = prev,
        custodian_pubkey = incoming_pubkey,
    )

    event = attach_signature(event, signature_hex)
    append_event(DATA_DIR, jacket_id.zfill(2), event)

    return jsonify({
        "status":   "confirmed",
        "event_id": event["event_id"],
        "message":  "Transfer confirmed. Custody has passed.",
    }), 201


# ---------------------------------------------------------------------------
# Sync endpoint — used by peer nodes
# ---------------------------------------------------------------------------

@app.get("/api/sync/status")
def sync_status():
    """
    Return the tip hash and length for all jackets on this node.
    Peers call this to discover what they're missing.
    """
    result = {}
    for jid in jacket_ids():
        chain = get_chain(jid)
        result[jid] = {
            "tip":    chain[-1]["event_id"] if chain else None,
            "length": len(chain),
        }
    return jsonify({"node": NODE_ID, "chains": result})


@app.get("/api/sync/daemon")
def sync_daemon_status():
    """Sync daemon health and statistics."""
    from sync import get_sync_stats
    return jsonify(get_sync_stats())


@app.post("/api/sync/receive")
def sync_receive():
    """
    Accept events pushed by a peer node during sync.
    Each event is verified before being appended.
    Silently ignores events already in the chain.
    """
    data   = request.get_json(silent=True)
    events = data.get("events", []) if data else []
    added  = 0
    errors = []

    for event in events:
        jid   = event.get("jacket_id", "").zfill(2)
        chain = get_chain(jid)

        # Skip if already in chain
        existing_ids = {e["event_id"] for e in chain}
        if event.get("event_id") in existing_ids:
            continue

        # Verify hash integrity
        from chain import verify_hash
        if not verify_hash(event):
            errors.append(f"hash mismatch for event {event.get('event_id', '?')[:16]}")
            continue

        # Verify signature
        if not verify_event_signature(event):
            errors.append(f"invalid signature for event {event.get('event_id', '?')[:16]}")
            continue

        append_event(DATA_DIR, jid, event)
        added += 1

    return jsonify({"added": added, "errors": errors})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"\nJacket Node {NODE_ID} — starting on port {port}")
    print(f"Data directory: {DATA_DIR}")
    print(f"Peers: {PEERS or 'none configured'}\n")

    # Start LED controller
    try:
        from led import start as start_led
        start_led()
        print("LED controller started\n")
    except Exception as e:
        print(f"LED controller unavailable: {e}\n")

    # Start sync daemon if peers are configured
    if PEERS:
        from sync import start as start_sync
        start_sync(peers=PEERS)
        print(f"Sync daemon started — polling {len(PEERS)} peer(s) every 60s\n")

    app.run(host="0.0.0.0", port=port, debug=False)
