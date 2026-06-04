from __future__ import annotations

import json
from typing import Any


def encode_signed_order(msg_b64: str) -> str:
    return _compact({"type": "signed_order", "msg_b64": msg_b64})


def encode_signed_cancel(msg_b64: str) -> str:
    return _compact({"type": "signed_cancel", "msg_b64": msg_b64})


def encode_signed_modify(msg_b64: str) -> str:
    return _compact({"type": "signed_modify", "msg_b64": msg_b64})


def encode_signed_create_vault(msg_b64: str) -> str:
    return _compact({"type": "signed_create_vault", "msg_b64": msg_b64})


def encode_ping(client_unix_ns: int) -> str:
    return _compact({"type": "ping", "client_unix_ns": client_unix_ns})


def encode_subscribe_market(symbol: int) -> str:
    return _compact({"type": "subscribe_market", "symbol": symbol})


def encode_subscribe_account() -> str:
    return _compact({"type": "subscribe_account"})


def encode_auth_wallet(wallet_pubkey: str, expiry_seconds: int = 0) -> str:
    return _compact(
        {
            "type": "auth_wallet",
            "wallet_pubkey": wallet_pubkey,
            "expiry_seconds": expiry_seconds,
        }
    )


def encode_auth_response(nonce: str, signature_b64: str, wallet_pubkey: str = "") -> str:
    payload: dict[str, Any] = {
        "type": "auth_response",
        "nonce": nonce,
        "signature_b64": signature_b64,
    }
    if wallet_pubkey != "":
        payload["wallet_pubkey"] = wallet_pubkey
    return _compact(payload)


def _compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
