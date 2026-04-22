"""
chain.py — Jacket chain core
Handles event structure, SHA-256 linking, and integrity verification.
Every event is a dict. The chain is a list of dicts stored as JSON.
No external dependencies.
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

# Valid event types
GENESIS   = "GENESIS"
TRANSFER  = "TRANSFER"
PATCH     = "PATCH"
NOTE      = "NOTE"
WORN      = "WORN"

VALID_TYPES = {GENESIS, TRANSFER, PATCH, NOTE, WORN}

# Event types that require custodian authentication
AUTHENTICATED_TYPES = {TRANSFER, PATCH, NOTE}

# Event types that are public contributions (no PIN required, held for review)
PUBLIC_TYPES = {WORN}


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_event(event: dict) -> str:
    """
    SHA-256 hash of an event, excluding the signature and event_id fields.
    This is the canonical content hash — computed from payload only.
    The dict is serialised with sorted keys so hashing is deterministic.
    """
    event_core = {k: v for k, v in event.items() if k not in ("signature", "event_id")}
    serialised = json.dumps(event_core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialised.encode()).hexdigest()


def verify_hash(event: dict) -> bool:
    """Confirm that the stored event_id matches the event contents."""
    return event.get("event_id") == hash_event(event)


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------

def build_event(
    jacket_id: str,
    event_type: str,
    payload: dict,
    prev_hash: Optional[str],
    custodian_pubkey: str,
    timestamp: Optional[str] = None,
) -> dict:
    """
    Build an unsigned event dict ready for signing.
    The event_id is computed here — sign this dict's event_id field.
    If timestamp is provided (from the client), use it — ensures both
    sides hash the same data. Otherwise generate from current UTC time.
    """
    if event_type not in VALID_TYPES:
        raise ValueError(f"Unknown event type: {event_type}")

    event = {
        "jacket_id":        jacket_id,
        "event_type":       event_type,
        "timestamp":        timestamp or datetime.now(timezone.utc).isoformat(),
        "prev_hash":        prev_hash,
        "custodian_pubkey": custodian_pubkey,
        "payload":          payload,
        "signature":        None,
    }

    event["event_id"] = hash_event(event)
    return event


def attach_signature(event: dict, signature_hex: str) -> dict:
    """Attach a hex signature to an event. Returns a new dict."""
    signed = dict(event)
    signed["signature"] = signature_hex
    return signed


# ---------------------------------------------------------------------------
# Chain file I/O
# ---------------------------------------------------------------------------

def chain_path(data_dir: str, jacket_id: str) -> str:
    return os.path.join(data_dir, f"jacket_{jacket_id}.jsonl")


def load_chain(data_dir: str, jacket_id: str) -> list[dict]:
    """
    Load the full chain for a jacket from a .jsonl file.
    Each line is one JSON event. Returns an empty list if no chain exists.
    """
    path = chain_path(data_dir, jacket_id)
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def append_event(data_dir: str, jacket_id: str, event: dict) -> None:
    """
    Append a single verified event to the chain file.
    Never overwrites. Append-only. One event per line.
    """
    os.makedirs(data_dir, exist_ok=True)
    path = chain_path(data_dir, jacket_id)
    with open(path, "a") as f:
        f.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def latest_hash(data_dir: str, jacket_id: str) -> Optional[str]:
    """Return the event_id of the most recent event, or None for an empty chain."""
    chain = load_chain(data_dir, jacket_id)
    return chain[-1]["event_id"] if chain else None


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------

def verify_chain(events: list[dict]) -> tuple[bool, str]:
    """
    Verify the structural integrity of a full chain.

    Checks:
    1. First event must be GENESIS with prev_hash = None
    2. Every event_id must match its computed hash
    3. Every event's prev_hash must match the previous event's event_id
    4. Signatures are present (cryptographic verification is in crypto.py)

    Returns (True, "ok") or (False, "reason for failure").
    """
    if not events:
        return False, "chain is empty"

    # Check genesis
    if events[0]["event_type"] != GENESIS:
        return False, "first event is not GENESIS"
    if events[0]["prev_hash"] is not None:
        return False, "GENESIS event must have prev_hash = null"

    for i, event in enumerate(events):

        # Hash integrity
        if not verify_hash(event):
            return False, f"event {i} hash mismatch: stored={event.get('event_id')} computed={hash_event(event)}"

        # Chain linkage
        if i > 0:
            expected_prev = events[i - 1]["event_id"]
            if event["prev_hash"] != expected_prev:
                return False, (
                    f"event {i} broken link: "
                    f"prev_hash={event['prev_hash']} "
                    f"expected={expected_prev}"
                )

        # Signature present
        if not event.get("signature"):
            return False, f"event {i} has no signature"

    return True, "ok"


def chain_summary(events: list[dict]) -> dict:
    """Human-readable summary of a chain's current state."""
    if not events:
        return {"status": "empty"}

    genesis   = events[0]["payload"]
    latest    = events[-1]
    transfers = [e for e in events if e["event_type"] == TRANSFER]
    patches   = [e for e in events if e["event_type"] == PATCH]
    notes     = [e for e in events if e["event_type"] == NOTE]
    worns     = [e for e in events if e["event_type"] == WORN]

    return {
        "jacket_id":        genesis.get("jacket_number"),
        "xpub":             genesis.get("xpub"),
        "founded":          events[0]["timestamp"],
        "total_events":     len(events),
        "transfers":        len(transfers),
        "patches":          len(patches),
        "notes":            len(notes),
        "worn":             len(worns),
        "latest_event":     latest["event_type"],
        "latest_timestamp": latest["timestamp"],
        "chain_tip":        latest["event_id"],
        "valid":            verify_chain(events)[0],
    }
