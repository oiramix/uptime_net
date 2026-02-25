from __future__ import annotations

import secrets


def gen_token() -> str:
    return secrets.token_urlsafe(32)


def gen_id(prefix: str) -> str:
    # short stable id
    return f"{prefix}_{secrets.token_hex(8)}"
