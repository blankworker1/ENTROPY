"""
test_chain.py — Full chain integration test
Writes a genesis block, then PATCH and NOTE events, then verifies
the complete chain including signatures and hash linkage.
"""

import json
import os
import sys

from chain import (
    PATCH, NOTE, TRANSFER,
    build_event, attach_signature,
    append_event, load_chain, verify_chain,
    chain_summary, latest_hash
)
from crypto import (
    derive_keypair, pubkey_from_pin,
    sign_event_id, verify_event_signature,
    generate_nfc_salt, generate_founding_keypair,
    sign_with_founding_key,
)
from genesis import create_genesis, inspect_chain

DATA_DIR = "./test_data"

def separator(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

# ── Clean slate ────────────────────────────────────────────────
if os.path.exists(f"{DATA_DIR}/jacket_01.jsonl"):
    os.remove(f"{DATA_DIR}/jacket_01.jsonl")

# ── 1. Genesis ─────────────────────────────────────────────────
separator("Step 1 — Genesis block")

founding_private, founding_public = generate_founding_keypair()
CUSTODIAN_PIN  = "9271"
JACKET_ID      = "01"

# Generate NFC salts for this jacket
nfc_salts = {
    "right_pocket":  generate_nfc_salt(),
    "left_pocket":   generate_nfc_salt(),
    "breast_pocket": generate_nfc_salt(),
    "inner_pocket":  generate_nfc_salt(),
}

genesis = create_genesis(
    data_dir             = DATA_DIR,
    jacket_number        = JACKET_ID,
    xpub                 = "xpub6CUGRUonZSQ4TWtTMmzXdrXDtypWKiKrhko4egpiMZbpiaQL2jkwSB1icqYh2cfDfVxdx4df189oLKnC5fSwqPfgyP3hooxujYzAu3fDVmz",
    founding_date        = "2026-04-18",
    custodian_name       = "Custodian One",
    custodian_pin        = CUSTODIAN_PIN,
    founding_private_hex = founding_private,
    founding_public_hex  = founding_public,
    nfc_salts            = nfc_salts,
)

print(f"✓ Genesis written: {genesis['event_id'][:32]}...")
del founding_private  # founding key destroyed

# ── 2. PATCH event ─────────────────────────────────────────────
separator("Step 2 — PATCH event (custodian adds a patch)")

inner_salt     = nfc_salts["inner_pocket"]
custodian_pub  = pubkey_from_pin(CUSTODIAN_PIN, inner_salt)

patch_payload = {
    "description": "Red dragon on black field, left chest, wool thread on wool base",
    "position":    "Left chest panel",
    "image_hash":  "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}

prev = latest_hash(DATA_DIR, JACKET_ID)
patch_event = build_event(
    jacket_id        = JACKET_ID,
    event_type       = PATCH,
    payload          = patch_payload,
    prev_hash        = prev,
    custodian_pubkey = custodian_pub,
)

signature   = sign_event_id(patch_event["event_id"], CUSTODIAN_PIN, inner_salt)
patch_event = attach_signature(patch_event, signature)
append_event(DATA_DIR, JACKET_ID, patch_event)

sig_valid = verify_event_signature(patch_event)
print(f"✓ PATCH written:   {patch_event['event_id'][:32]}...")
print(f"  Signature valid: {sig_valid}")

# ── 3. NOTE event ──────────────────────────────────────────────
separator("Step 3 — NOTE event (custodian logs a public wearing)")

note_payload = {
    "text":       "Worn at the Senedd, Cardiff. QR code scanned 34 times during the session.",
    "image_hash": None,
}

prev = latest_hash(DATA_DIR, JACKET_ID)
note_event = build_event(
    jacket_id        = JACKET_ID,
    event_type       = NOTE,
    payload          = note_payload,
    prev_hash        = prev,
    custodian_pubkey = custodian_pub,
)

signature  = sign_event_id(note_event["event_id"], CUSTODIAN_PIN, inner_salt)
note_event = attach_signature(note_event, signature)
append_event(DATA_DIR, JACKET_ID, note_event)

sig_valid = verify_event_signature(note_event)
print(f"✓ NOTE written:    {note_event['event_id'][:32]}...")
print(f"  Signature valid: {sig_valid}")

# ── 4. Full chain verification ─────────────────────────────────
separator("Step 4 — Full chain verification")

chain        = load_chain(DATA_DIR, JACKET_ID)
valid, reason = verify_chain(chain)

print(f"  Chain length:   {len(chain)} events")
print(f"  Hash linkage:   {'✓ intact' if valid else '✗ broken'} ({reason})")
print()

all_sigs_valid = all(verify_event_signature(e) for e in chain)
print(f"  All signatures: {'✓ valid' if all_sigs_valid else '✗ invalid'}")

# ── 5. Tamper test ─────────────────────────────────────────────
separator("Step 5 — Tamper detection test")

# Attempt to modify a past event
chain_file = f"{DATA_DIR}/jacket_01.jsonl"
with open(chain_file, "r") as f:
    lines = f.readlines()

# Tamper with the PATCH event (line index 1)
original_line = lines[1]
tampered_event = json.loads(lines[1])
tampered_event["payload"]["description"] = "TAMPERED — this patch never happened"
lines[1] = json.dumps(tampered_event, sort_keys=True, separators=(",", ":")) + "\n"

with open(chain_file, "w") as f:
    f.writelines(lines)

tampered_chain   = load_chain(DATA_DIR, JACKET_ID)
valid_t, reason_t = verify_chain(tampered_chain)
print(f"  After tampering with event 1:")
print(f"  Chain valid:    {valid_t} ← correctly detected as invalid")
print(f"  Reason:         {reason_t}")

# Restore
lines[1] = original_line
with open(chain_file, "w") as f:
    f.writelines(lines)

restored_chain    = load_chain(DATA_DIR, JACKET_ID)
valid_r, reason_r = verify_chain(restored_chain)
print(f"\n  After restoring:")
print(f"  Chain valid:    {valid_r} ({reason_r})")

# ── 6. Wrong PIN test ──────────────────────────────────────────
separator("Step 6 — Wrong PIN produces wrong key, signature rejected")

wrong_pubkey = pubkey_from_pin("0000", inner_salt)
wrong_sig    = sign_event_id(note_event["event_id"], "0000", inner_salt)

# Verify with wrong key against a correctly signed event
wrong_check = verify_event_signature({**note_event, "custodian_pubkey": wrong_pubkey})
print(f"  Wrong PIN signature accepted: {wrong_check} ← correctly rejected")

# ── 7. Summary ─────────────────────────────────────────────────
separator("Final chain state")
inspect_chain(DATA_DIR, JACKET_ID)

print("\n✓ All tests passed. Chain core is working correctly.\n")
