"""
sync.py — Jacket node sync daemon
Runs as a background thread alongside the Flask server.
Periodically polls peer nodes, discovers missing events,
fetches and verifies them, appends to local chain.
Simple gossip: poll → compare tips → fetch delta → verify → append.
No consensus required — the chain is append-only and signature-verified.
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, timezone

import requests

from chain import (
    load_chain, append_event, latest_hash,
    verify_chain, verify_hash, chain_summary,
)
from crypto import verify_event_signature

log = logging.getLogger("sync")
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [sync] %(message)s",
    datefmt= "%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SYNC_INTERVAL   = int(os.environ.get("SYNC_INTERVAL",   "60"))   # seconds
REQUEST_TIMEOUT = int(os.environ.get("SYNC_TIMEOUT",    "8"))    # seconds per request
DATA_DIR        = os.environ.get("JACKET_DATA_DIR",     "./data")
NODE_ID         = os.environ.get("JACKET_ID",           "01")

# Peers: comma-separated URLs, e.g. "http://192.168.1.10:5000,http://192.168.1.11:5000"
PEERS = [p.strip() for p in os.environ.get("JACKET_PEERS", "").split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Peer communication
# ---------------------------------------------------------------------------

def peer_sync_status(peer_url: str) -> dict | None:
    """
    Fetch the sync status (tip + length for all jackets) from a peer.
    Returns None on any network or parse error.
    """
    try:
        r = requests.get(
            f"{peer_url}/api/sync/status",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("chains", {})
    except Exception as e:
        log.debug(f"Could not reach {peer_url}: {e}")
        return None


def fetch_events_since(peer_url: str, jacket_id: str, since_event_id: str) -> list:
    """
    Fetch all events after since_event_id from a peer node.
    Returns an empty list on any error.
    """
    try:
        r = requests.get(
            f"{peer_url}/api/jacket/{jacket_id}/events/since/{since_event_id}",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as e:
        log.debug(f"Could not fetch events from {peer_url}: {e}")
        return []


def fetch_full_chain(peer_url: str, jacket_id: str) -> list:
    """
    Fetch the full event list for a jacket from a peer.
    Used when we have no local chain for this jacket_id at all.
    """
    try:
        r = requests.get(
            f"{peer_url}/api/jacket/{jacket_id}/events",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as e:
        log.debug(f"Could not fetch full chain from {peer_url}: {e}")
        return []


def push_events_to_peer(peer_url: str, events: list) -> bool:
    """
    Push a list of events to a peer's sync/receive endpoint.
    Returns True if the peer accepted at least some events.
    """
    if not events:
        return True
    try:
        r = requests.post(
            f"{peer_url}/api/sync/receive",
            json={"events": events},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        result = r.json()
        added  = result.get("added", 0)
        errors = result.get("errors", [])
        if errors:
            log.warning(f"Peer {peer_url} rejected {len(errors)} events: {errors[:2]}")
        return True
    except Exception as e:
        log.debug(f"Could not push to {peer_url}: {e}")
        return False


# ---------------------------------------------------------------------------
# Event verification
# ---------------------------------------------------------------------------

def verify_incoming_event(event: dict) -> tuple[bool, str]:
    """
    Verify a single event received from a peer before appending.
    Checks: hash integrity, signature validity, event_type valid.
    Returns (ok, reason).
    """
    from chain import VALID_TYPES

    if event.get("event_type") not in VALID_TYPES:
        return False, f"unknown event_type: {event.get('event_type')}"

    if not verify_hash(event):
        return False, "hash mismatch"

    if not event.get("signature"):
        return False, "no signature"

    if not verify_event_signature(event):
        return False, "invalid signature"

    return True, "ok"


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def sync_with_peer(peer_url: str) -> dict:
    """
    Sync all jacket chains with one peer.
    Returns a summary dict of what happened.
    """
    summary = {"peer": peer_url, "fetched": 0, "pushed": 0, "errors": []}

    peer_chains = peer_sync_status(peer_url)
    if peer_chains is None:
        summary["errors"].append("unreachable")
        return summary

    # --- Pull: fetch events we're missing ---
    for jacket_id, peer_info in peer_chains.items():
        jacket_id = jacket_id.zfill(2)
        peer_tip    = peer_info.get("tip")
        peer_length = peer_info.get("length", 0)

        local_chain = load_chain(DATA_DIR, jacket_id)
        local_tip   = local_chain[-1]["event_id"] if local_chain else None
        local_len   = len(local_chain)

        # Already in sync
        if local_tip == peer_tip:
            continue

        # We're behind — fetch what we're missing
        if peer_length > local_len:
            if not local_chain:
                # We have nothing — fetch the full chain
                events = fetch_full_chain(peer_url, jacket_id)
                since  = "genesis"
            else:
                events = fetch_events_since(peer_url, jacket_id, local_tip)
                since  = local_tip[:12]

            if not events:
                continue

            added = 0
            existing_ids = {e["event_id"] for e in local_chain}

            for event in events:
                if event.get("event_id") in existing_ids:
                    continue

                ok, reason = verify_incoming_event(event)
                if not ok:
                    log.warning(f"Rejected event from {peer_url} jacket {jacket_id}: {reason}")
                    summary["errors"].append(f"jacket {jacket_id}: {reason}")
                    continue

                append_event(DATA_DIR, jacket_id, event)
                existing_ids.add(event["event_id"])
                added += 1

            if added:
                log.info(f"Pulled {added} events for jacket {jacket_id} from {peer_url} (since {since})")
                summary["fetched"] += added

    # --- Push: send events the peer is missing ---
    # We re-fetch peer status to find jackets the peer doesn't have yet
    for jacket_id in _local_jacket_ids():
        local_chain = load_chain(DATA_DIR, jacket_id)
        if not local_chain:
            continue

        peer_info = peer_chains.get(jacket_id, {})
        peer_tip    = peer_info.get("tip")
        peer_length = peer_info.get("length", 0)

        if peer_tip == local_chain[-1]["event_id"]:
            continue  # peer is up to date

        # Find events the peer doesn't have
        if peer_tip is None:
            events_to_push = local_chain
        else:
            events_to_push = []
            found = False
            for event in local_chain:
                if found:
                    events_to_push.append(event)
                if event["event_id"] == peer_tip:
                    found = True

        if events_to_push:
            ok = push_events_to_peer(peer_url, events_to_push)
            if ok:
                log.info(f"Pushed {len(events_to_push)} events for jacket {jacket_id} to {peer_url}")
                summary["pushed"] += len(events_to_push)

    return summary


def _local_jacket_ids() -> list[str]:
    """Return all jacket IDs with local chain files."""
    ids = []
    if not os.path.exists(DATA_DIR):
        return ids
    for fname in sorted(os.listdir(DATA_DIR)):
        if fname.startswith("jacket_") and fname.endswith(".jsonl"):
            ids.append(fname[7:9])
    return ids


# ---------------------------------------------------------------------------
# Chain health check
# ---------------------------------------------------------------------------

def health_check() -> dict:
    """
    Verify integrity of all local chains.
    Called after each sync cycle. Logs warnings for any broken chains.
    """
    results = {}
    for jid in _local_jacket_ids():
        chain = load_chain(DATA_DIR, jid)
        valid, reason = verify_chain(chain)
        results[jid] = {"valid": valid, "reason": reason, "length": len(chain)}
        if not valid:
            log.error(f"Chain integrity failure for jacket {jid}: {reason}")
    return results


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class SyncDaemon(threading.Thread):
    """
    Background thread that runs sync cycles on a timer.
    Designed to run alongside the Flask server.
    """

    def __init__(self, peers: list[str] = None, interval: int = SYNC_INTERVAL):
        super().__init__(daemon=True, name="SyncDaemon")
        self.peers    = peers or PEERS
        self.interval = interval
        self._stop    = threading.Event()
        self.stats    = {
            "cycles":       0,
            "total_fetched": 0,
            "total_pushed":  0,
            "last_cycle":    None,
            "peer_status":   {},
        }

    def stop(self):
        self._stop.set()

    def run(self):
        log.info(f"Sync daemon started — {len(self.peers)} peer(s), interval {self.interval}s")

        while not self._stop.is_set():
            self._cycle()
            # Sleep in small increments so stop() is responsive
            for _ in range(self.interval * 10):
                if self._stop.is_set():
                    break
                time.sleep(0.1)

        log.info("Sync daemon stopped")

    def _cycle(self):
        """Run one full sync cycle across all peers."""
        self.stats["cycles"] += 1
        self.stats["last_cycle"] = datetime.now(timezone.utc).isoformat()

        if not self.peers:
            return  # No peers configured — nothing to do

        log.info(f"Sync cycle {self.stats['cycles']} — {len(self.peers)} peer(s)")

        # Signal syncing state to LED
        try:
            from led import on_syncing
            on_syncing()
        except ImportError:
            pass

        for peer in self.peers:
            result = sync_with_peer(peer)
            self.stats["total_fetched"] += result["fetched"]
            self.stats["total_pushed"]  += result["pushed"]
            self.stats["peer_status"][peer] = {
                "fetched": result["fetched"],
                "pushed":  result["pushed"],
                "errors":  result["errors"],
                "time":    datetime.now(timezone.utc).isoformat(),
            }
            if result["fetched"] or result["pushed"]:
                log.info(f"  {peer}: fetched={result['fetched']} pushed={result['pushed']}")

        # Health check after sync
        health = health_check()
        broken = [jid for jid, r in health.items() if not r["valid"]]
        if broken:
            log.error(f"Broken chains after sync: {broken}")
        else:
            total_events = sum(r["length"] for r in health.values())
            log.info(f"Health OK — {len(health)} chains, {total_events} total events")

        # Update LED state based on sync outcome
        try:
            from led import on_sync_complete
            peers_reached = bool(self.peers) and any(
                "unreachable" not in s.get("errors", [])
                for s in self.stats["peer_status"].values()
            )
            on_sync_complete(chain_valid=not broken, peers_connected=peers_reached)
        except ImportError:
            pass


# ---------------------------------------------------------------------------
# Status endpoint data
# ---------------------------------------------------------------------------

_daemon_instance: SyncDaemon | None = None

def get_sync_stats() -> dict:
    """Called by the Flask server to expose sync status via API."""
    if _daemon_instance is None:
        return {"running": False}
    return {
        "running":       True,
        "interval":      _daemon_instance.interval,
        "peers":         _daemon_instance.peers,
        **_daemon_instance.stats,
    }


# ---------------------------------------------------------------------------
# Entry point — run standalone for testing
# ---------------------------------------------------------------------------

def start(peers: list[str] = None, interval: int = SYNC_INTERVAL) -> SyncDaemon:
    """
    Start the sync daemon and return the thread.
    Call this from server.py on startup.
    """
    global _daemon_instance
    _daemon_instance = SyncDaemon(peers=peers, interval=interval)
    _daemon_instance.start()
    return _daemon_instance


if __name__ == "__main__":
    # Standalone test: run one sync cycle and exit
    import sys
    log.setLevel(logging.DEBUG)
    peers_arg = sys.argv[1:] or PEERS

    if not peers_arg:
        print("No peers configured. Set JACKET_PEERS or pass peer URLs as arguments.")
        print("Usage: python sync.py http://192.168.1.10:5000 http://192.168.1.11:5000")
        sys.exit(0)

    print(f"\nRunning single sync cycle with peers: {peers_arg}\n")
    for peer in peers_arg:
        result = sync_with_peer(peer)
        print(f"Peer {peer}:")
        print(f"  Fetched: {result['fetched']}")
        print(f"  Pushed:  {result['pushed']}")
        print(f"  Errors:  {result['errors']}")

    print("\nLocal chain health:")
    for jid, info in health_check().items():
        print(f"  Jacket {jid}: {'✓' if info['valid'] else '✗'} ({info['length']} events)")
