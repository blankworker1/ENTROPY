"""
crypto.py — Key derivation and signing
PIN + NFC salt → deterministic keypair via PBKDF2.
Signing and verification use secp256k1 (ECDSA) via the cryptography library.
The private key exists in memory only during signing. Never stored anywhere.
"""

import hashlib
import hmac
import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

PBKDF2_ITERATIONS = 310_000   # OWASP 2023 recommended minimum for PBKDF2-SHA256


def derive_keypair(pin: str, nfc_salt_hex: str) -> tuple:
    """
    Derive a deterministic secp256k1 keypair from a PIN and NFC salt.

    The NFC salt is unique per jacket button, fixed at fabrication.
    The PIN is known only to the custodian, never stored anywhere.
    Together they produce a keypair that only this custodian on this
    jacket can reproduce.

    Returns (private_key, public_key_hex).
    The caller is responsible for discarding private_key after use.
    """
    nfc_salt = bytes.fromhex(nfc_salt_hex)

    # PBKDF2-SHA256: slow enough to resist brute force on the PIN
    key_material = hashlib.pbkdf2_hmac(
        hash_name   = "sha256",
        password    = pin.encode("utf-8"),
        salt        = nfc_salt,
        iterations  = PBKDF2_ITERATIONS,
        dklen       = 32,     # 256 bits → secp256k1 private key scalar
    )

    # Load as secp256k1 private key
    private_key = ec.derive_private_key(
        int.from_bytes(key_material, "big"),
        ec.SECP256K1(),
        default_backend(),
    )

    public_key_hex = private_key.public_key().public_bytes(
        encoding = serialization.Encoding.X962,
        format   = serialization.PublicFormat.CompressedPoint,
    ).hex()

    return private_key, public_key_hex


def pubkey_from_pin(pin: str, nfc_salt_hex: str) -> str:
    """Return only the public key hex. Use this to register a new custodian."""
    _, pubkey = derive_keypair(pin, nfc_salt_hex)
    return pubkey


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def sign_event_id(event_id: str, pin: str, nfc_salt_hex: str) -> str:
    """
    Sign an event_id with the derived private key.
    The private key is derived, used, and discarded in this call.
    Returns a DER-encoded signature as hex.
    """
    private_key, _ = derive_keypair(pin, nfc_salt_hex)
    message = bytes.fromhex(event_id)
    signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    # private_key goes out of scope here and is garbage collected
    return signature.hex()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def load_public_key(pubkey_hex: str):
    """Load a compressed secp256k1 public key from hex."""
    return ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256K1(),
        bytes.fromhex(pubkey_hex),
    )


def verify_signature(event_id: str, signature_hex: str, pubkey_hex: str) -> bool:
    """
    Verify that signature_hex is a valid signature of event_id
    by the key corresponding to pubkey_hex.
    Returns True if valid, False otherwise. Never raises on invalid sig.
    """
    try:
        public_key = load_public_key(pubkey_hex)
        message    = bytes.fromhex(event_id)
        signature  = bytes.fromhex(signature_hex)
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def verify_event_signature(event: dict) -> bool:
    """
    Verify the signature on a chain event using its stored custodian_pubkey.
    This is the per-event cryptographic check that complements chain.verify_chain().
    """
    return verify_signature(
        event_id      = event["event_id"],
        signature_hex = event["signature"],
        pubkey_hex    = event["custodian_pubkey"],
    )


# ---------------------------------------------------------------------------
# NFC salt generation (used at fabrication only)
# ---------------------------------------------------------------------------

def generate_nfc_salt() -> str:
    """
    Generate a random 32-byte salt for an NFC button.
    Called once per button at jacket fabrication. Written to the NFC tag.
    Never regenerated. If lost, that button's derived keys are unrecoverable.
    """
    return os.urandom(32).hex()


# ---------------------------------------------------------------------------
# Founding keypair (used once at genesis, then destroyed)
# ---------------------------------------------------------------------------

def generate_founding_keypair() -> tuple[str, str]:
    """
    Generate a one-time founding keypair for writing genesis blocks.
    Returns (private_key_hex, public_key_hex).
    The private key must be destroyed immediately after all 21 genesis
    blocks are written and verified. This enacts the Satoshi disappearance
    at the cryptographic level.
    """
    private_key = ec.generate_private_key(ec.SECP256K1(), default_backend())
    private_key_hex = format(
        private_key.private_numbers().private_value, "064x"
    )
    public_key_hex = private_key.public_key().public_bytes(
        encoding = serialization.Encoding.X962,
        format   = serialization.PublicFormat.CompressedPoint,
    ).hex()
    return private_key_hex, public_key_hex


def sign_with_founding_key(event_id: str, founding_private_hex: str) -> str:
    """Sign a genesis event_id with the founding private key."""
    key_bytes   = bytes.fromhex(founding_private_hex)
    private_key = ec.derive_private_key(
        int.from_bytes(key_bytes, "big"),
        ec.SECP256K1(),
        default_backend(),
    )
    message   = bytes.fromhex(event_id)
    signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    return signature.hex()
