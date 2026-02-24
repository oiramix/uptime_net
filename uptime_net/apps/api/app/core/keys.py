from __future__ import annotations

import os

from shared.ed25519 import generate_keypair


def get_or_create_server_sk_b64() -> str:
    sk = os.environ.get("SERVER_ED25519_SK_B64")
    if sk:
        return sk
    # DEV ONLY: generate ephemeral key if not provided.
    kp = generate_keypair()
    return kp.sk_b64
