"""Ed25519 helpers (keygen, sign, verify) with base64 encoding."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


@dataclass(frozen=True)
class Keypair:
    sk_b64: str
    pk_b64: str


def generate_keypair() -> Keypair:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    sk_bytes = sk.private_bytes_raw()
    pk_bytes = pk.public_bytes_raw()
    return Keypair(sk_b64=b64e(sk_bytes), pk_b64=b64e(pk_bytes))


def sign_bytes(sk_b64: str, msg: bytes) -> str:
    sk = Ed25519PrivateKey.from_private_bytes(b64d(sk_b64))
    sig = sk.sign(msg)
    return b64e(sig)


def verify_bytes(pk_b64: str, msg: bytes, sig_b64: str) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(b64d(pk_b64))
        pk.verify(b64d(sig_b64), msg)
        return True
    except Exception:
        return False
