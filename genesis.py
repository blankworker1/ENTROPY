"""
genesis.py — Genesis block writer
Writes the first event for a jacket chain.
In production this runs once per jacket, then the founding key is destroyed.
In development it generates test jackets freely.
"""

import json
import os
import sys

from chain import (
    GENESIS, build_event, attach_signature,
    append_event, load_chain, verify_chain, chain_summary
)
from crypto import (
    generate_nfc_salt,
    generate_founding_keypair,
    sign_with_founding_key,
    pubkey_from_pin,
)


def create_genesis(
    data_dir: str,
    jacket_number: str,           # "01" through "21"
    xpub: str,                    # Bitcoin extended public key
    founding_date: str,           # ISO date string e.g. "2026-04-18"
    custodian_name: str,          # First custodian's name
    custodian_pin: str,           # First custodian's PIN (never stored)
    founding_private_hex: str,    # Founding key — destroyed after all 21 written
    founding_public_hex: str,
    nfc_salts: dict = None,       # Optional: pre-generated salts. Generated if absent.
) -> dict:
    """
    Write a genesis block for one jacket.
    Returns the completed genesis event.
    """
    jacket_id = jacket_number.zfill(2)
    existing  = load_chain(data_dir, jacket_id)
    if existing:
        raise RuntimeError(f"Chain already exists for jacket {jacket_id}. Genesis can only be written once.")

    # Generate NFC salts for the four buttons if not provided
    if nfc_salts is None:
        nfc_salts = {
            "right_pocket":  generate_nfc_salt(),
            "left_pocket":   generate_nfc_salt(),
            "breast_pocket": generate_nfc_salt(),
            "inner_pocket":  generate_nfc_salt(),   # write button
        }

    # Derive the first custodian's public key from their PIN and write-button salt
    custodian_pubkey = pubkey_from_pin(
        pin          = custodian_pin,
        nfc_salt_hex = nfc_salts["inner_pocket"],
    )

    payload = {
        "jacket_number":    jacket_id,
        "xpub":             xpub,
        "founding_date":    founding_date,
        "founding_pubkey":  founding_public_hex,
        "custodian_name":   custodian_name,
        "custodian_pubkey": custodian_pubkey,
        "nfc_salts": {
            # Public salts: stored openly — security comes from the PIN, not the salt
            "right_pocket":  nfc_salts["right_pocket"],
            "left_pocket":   nfc_salts["left_pocket"],
            "breast_pocket": nfc_salts["breast_pocket"],
            "inner_pocket":  nfc_salts["inner_pocket"],
        },
    }

    # Build the unsigned event (prev_hash is None for genesis)
    event = build_event(
        jacket_id        = jacket_id,
        event_type       = GENESIS,
        payload          = payload,
        prev_hash        = None,
        custodian_pubkey = founding_public_hex,   # genesis is signed by founding key
    )

    # Sign with the founding key
    signature = sign_with_founding_key(event["event_id"], founding_private_hex)
    event     = attach_signature(event, signature)

    # Write to chain
    append_event(data_dir, jacket_id, event)

    return event


# ---------------------------------------------------------------------------
# Dev helper: print a full chain with verification
# ---------------------------------------------------------------------------

def inspect_chain(data_dir: str, jacket_id: str) -> None:
    jacket_id = jacket_id.zfill(2)
    chain     = load_chain(data_dir, jacket_id)

    if not chain:
        print(f"No chain found for jacket {jacket_id}")
        return

    valid, reason = verify_chain(chain)
    summary       = chain_summary(chain)

    print(f"\n{'='*60}")
    print(f"  Jacket {jacket_id} — chain inspection")
    print(f"{'='*60}")
    print(f"  Valid:          {valid} ({reason})")
    print(f"  Events:         {summary['total_events']}")
    print(f"  Founded:        {summary['founded']}")
    print(f"  Chain tip:      {summary['chain_tip'][:16]}...")
    print(f"  xpub:           {summary['xpub'][:24]}...")
    print()

    for i, event in enumerate(chain):
        print(f"  [{i:02d}] {event['event_type']:10s}  {event['timestamp']}")
        print(f"       id:   {event['event_id'][:32]}...")
        print(f"       prev: {str(event['prev_hash'])[:32]}{'...' if event['prev_hash'] else ''}")
        print(f"       sig:  {event['signature'][:32]}...")
        print()


# ---------------------------------------------------------------------------
# CLI: python genesis.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    DATA_DIR = "./data"

    print("\nJacket Protocol — Genesis Tool")
    print("================================\n")
    print("Generating founding keypair...")
    founding_private, founding_public = generate_founding_keypair()
    print(f"Founding public key: {founding_public}")
    print()
    print("WARNING: In production, the founding private key is destroyed")
    print("after all 21 genesis blocks are written and verified.")
    print("It must never be stored. Here it lives only in this process.\n")

    # Test jacket — jacket 01
    print("Writing genesis block for Jacket 01 (test)...\n")

    genesis = create_genesis(
        data_dir             = DATA_DIR,
        jacket_number        = "01",
        xpub                 = "xpub6CUGRUonZSQ4TWtTMmzXdrXDtypWKiKrhko4egpiMZbpiaQL2jkwSB1icqYh2cfDfVxdx4df189oLKnC5fSwqPfgyP3hooxujYzAu3fDVmz",
        founding_date        = "2026-04-18",
        custodian_name       = "Test Custodian",
        custodian_pin        = "1234",
        founding_private_hex = founding_private,
        founding_public_hex  = founding_public,
    )

    print(f"Genesis block written.")
    print(f"Event ID: {genesis['event_id']}")
    print(f"Jacket:   {genesis['payload']['jacket_number']}")
    print(f"xpub:     {genesis['payload']['xpub'][:32]}...")

    # Verify
    inspect_chain(DATA_DIR, "01")

    print("Founding private key would be destroyed here in production.")
    print("del founding_private  # key material cleared from memory")
    del founding_private
    print("\nGenesis complete.\n")
