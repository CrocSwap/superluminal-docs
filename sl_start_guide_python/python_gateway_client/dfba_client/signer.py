from __future__ import annotations

import base64
import binascii
import json
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import base58
from nacl.signing import SigningKey

from dfba_client.types import (
    INT32_MAX,
    INT32_MIN,
    REQUEST_ID_PUBLIC_MAX,
    OrderDraft,
    OrderMode,
    OrderRole,
)

SOLANA_SIGNATURE_BYTES = 64
SOLANA_PUBKEY_BYTES = 32
ORDER_V1_BYTES = 37
ORDER_PROOF_BYTES = 191
CANCEL_V2_BYTES = 21
CANCEL_PROOF_BYTES = 175
MODIFY_V1_BYTES = 45
MODIFY_PROOF_BYTES = 199
CREATE_VAULT_V1_BYTES = 40
CREATE_VAULT_PROOF_BYTES = 194
ORDER_EXPIRY_SECONDS = 300
GTC_ORDER_EXPIRY_SECONDS = 30 * 24 * 60 * 60
MARK_TO_MID_EXPIRY_SECONDS = 24 * 60 * 60
CREATE_VAULT_EXPIRY_SECONDS = 300
ORDER_DOMAIN = b"AETHEON_ORDER_V1"
CANCEL_DOMAIN = b"AETHEON_CANCEL_V2"
MODIFY_DOMAIN = b"AETHEON_MODIFY_V1"
CREATE_VAULT_DOMAIN = b"AETHEON_CREATE_VAULT_V1"


@dataclass(frozen=True, slots=True)
class Keypair:
    signing_key: SigningKey

    @property
    def public_key(self) -> bytes:
        return bytes(self.signing_key.verify_key)

    @property
    def public_key_base58(self) -> str:
        return base58.b58encode(self.public_key).decode("ascii")


def load_keypair(path: str | Path) -> Keypair:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Solana keypair file must contain a JSON byte array")
    data = bytes(_json_byte(value) for value in raw)
    if len(data) == 64:
        return Keypair(SigningKey(data[:32]))
    if len(data) == 32:
        return Keypair(SigningKey(data))
    raise ValueError("Solana keypair must contain 32 seed bytes or 64 expanded bytes")


def sign_auth_challenge_base64(message: str, keypair: Keypair) -> str:
    signature = keypair.signing_key.sign(message.encode("utf-8")).signature
    return base64.b64encode(signature).decode("ascii")


def build_signed_order_proof_base64(
    keypair: Keypair,
    partition_id: int,
    subaccount_id: int,
    request_id: int,
    draft: OrderDraft,
) -> str:
    proof = build_signed_order_proof_raw(keypair, partition_id, subaccount_id, request_id, 0, draft)
    return base64.b64encode(proof).decode("ascii")


def build_signed_order_proof_raw(
    keypair: Keypair,
    partition_id: int,
    subaccount_id: int,
    request_id: int,
    execution_mode: int,
    draft: OrderDraft,
) -> bytes:
    return build_signed_order_proof_raw_for_sender(
        keypair,
        keypair.public_key,
        partition_id,
        subaccount_id,
        request_id,
        execution_mode,
        draft,
    )


def build_signed_order_proof_raw_for_sender(
    signing_keypair: Keypair,
    sender_pubkey: bytes,
    partition_id: int,
    subaccount_id: int,
    request_id: int,
    execution_mode: int,
    draft: OrderDraft,
) -> bytes:
    _check_pubkey(sender_pubkey, "sender_pubkey")
    _check_request_id(request_id)
    _check_i32(draft.delta_ppm, "delta_ppm")

    tif = 1
    order_type = 0
    if draft.mode == OrderMode.MARK_TO_MID:
        order_type = 1
    elif draft.mode == OrderMode.MARKET:
        tif = 0
        order_type = 2

    order = bytearray()
    order.extend(_u32(draft.symbol))
    order.extend(_u64(draft.price))
    order.extend(_u64(draft.qty))
    order.append(tif)
    order.append(order_type)
    order.append(_u8(execution_mode, "execution_mode"))
    order.append(1 if draft.role == OrderRole.TAKER else 0)
    order.extend(_i32(draft.delta_ppm))
    order.append(1 if draft.is_sell else 0)
    order.extend(_u64(request_id))
    if len(order) != ORDER_V1_BYTES:
        raise ValueError("invalid order payload size")

    partition = _u16(partition_id)
    now_sec = int(time.time())
    exp_sec = now_sec + ORDER_EXPIRY_SECONDS
    if tif == 1:
        exp_sec = now_sec + GTC_ORDER_EXPIRY_SECONDS
    if order_type == 1:
        exp_sec = now_sec + MARK_TO_MID_EXPIRY_SECONDS

    prefix = sender_pubkey + partition + _u64(subaccount_id) + _u64(now_sec) + _u64(exp_sec)
    payload = ORDER_DOMAIN + b"\x00" + prefix + bytes(order)
    signature = signing_keypair.signing_key.sign(payload).signature
    proof = prefix + bytes(order) + signing_keypair.public_key + signature
    if len(proof) != ORDER_PROOF_BYTES:
        raise ValueError("invalid signed order proof size")
    return proof


def build_signed_cancel_proof_base64(
    keypair: Keypair,
    partition_id: int,
    subaccount_id: int,
    external_order_id: int,
    request_id: int,
    symbol: int,
    role: OrderRole,
) -> str:
    _check_request_id(request_id)
    cancel = _u64(external_order_id) + _u64(request_id) + _u32(symbol) + bytes([1 if role == OrderRole.TAKER else 0])
    if len(cancel) != CANCEL_V2_BYTES:
        raise ValueError("invalid cancel payload size")
    proof = _build_signed_command_proof(
        keypair,
        partition_id,
        subaccount_id,
        ORDER_EXPIRY_SECONDS,
        CANCEL_DOMAIN,
        cancel,
        CANCEL_PROOF_BYTES,
    )
    return base64.b64encode(proof).decode("ascii")


def build_signed_modify_proof_base64(
    keypair: Keypair,
    partition_id: int,
    subaccount_id: int,
    external_order_id: int,
    request_id: int,
    symbol: int,
    role: OrderRole,
    is_sell: bool,
    price: int,
    qty: int,
    delta_ppm: int,
) -> str:
    _check_request_id(request_id)
    _check_i32(delta_ppm, "delta_ppm")
    modify = bytearray()
    modify.extend(_u64(external_order_id))
    modify.extend(_u64(request_id))
    modify.extend(_u32(symbol))
    modify.append(1 if role == OrderRole.TAKER else 0)
    modify.append(1 if is_sell else 0)
    modify.append(1)
    modify.append(1)
    modify.append(0)
    modify.extend(_u64(price))
    modify.extend(_u64(qty))
    modify.extend(_i32(delta_ppm))
    if len(modify) != MODIFY_V1_BYTES:
        raise ValueError("invalid modify payload size")
    proof = _build_signed_command_proof(
        keypair,
        partition_id,
        subaccount_id,
        ORDER_EXPIRY_SECONDS,
        MODIFY_DOMAIN,
        bytes(modify),
        MODIFY_PROOF_BYTES,
    )
    return base64.b64encode(proof).decode("ascii")


def build_signed_create_vault_proof_base64(
    keypair: Keypair,
    partition_id: int,
    source_subaccount_id: int,
    vault_id: int,
    operation_subaccount_id: int,
    requested_vault_collateral: int,
    vault_creation_fee: int,
    request_id: int,
) -> str:
    if (
        operation_subaccount_id == 0
        or source_subaccount_id == operation_subaccount_id
        or requested_vault_collateral == 0
        or request_id == 0
    ):
        raise ValueError("invalid create vault proof fields")
    _check_request_id(request_id)
    body = (
        _u32(vault_id)
        + _u32(0)
        + _u64(operation_subaccount_id)
        + _u64(requested_vault_collateral)
        + _u64(vault_creation_fee)
        + _u64(request_id)
    )
    if len(body) != CREATE_VAULT_V1_BYTES:
        raise ValueError("invalid create-vault payload size")
    proof = _build_signed_command_proof(
        keypair,
        partition_id,
        source_subaccount_id,
        CREATE_VAULT_EXPIRY_SECONDS,
        CREATE_VAULT_DOMAIN,
        body,
        CREATE_VAULT_PROOF_BYTES,
    )
    return base64.b64encode(proof).decode("ascii")


def sign_transaction_base64(tx_b64: str, wallet_pubkey: str, keypair: Keypair) -> str:
    wallet_bytes = base58.b58decode(wallet_pubkey)
    _check_pubkey(wallet_bytes, "wallet_pubkey")
    if wallet_bytes != keypair.public_key:
        raise ValueError("wallet pubkey does not match keypair")
    try:
        raw = bytearray(base64.b64decode(tx_b64, validate=True))
    except binascii.Error as exc:
        raise ValueError("invalid base64 payload") from exc
    signature_count, signatures_offset = _decode_shortvec(raw, 0)
    if signature_count == 0:
        raise ValueError("transaction missing signatures")
    signatures_bytes = signature_count * SOLANA_SIGNATURE_BYTES
    if signatures_offset + signatures_bytes >= len(raw):
        raise ValueError("transaction signature section truncated")
    message_offset = signatures_offset + signatures_bytes
    if message_offset + 3 > len(raw):
        raise ValueError("transaction message truncated")
    if (raw[message_offset] & 0x80) != 0:
        raise ValueError("versioned transactions are not supported")
    if raw[message_offset] != signature_count:
        raise ValueError("signature count does not match message header")
    account_count, account_keys_offset = _decode_shortvec(raw, message_offset + 3)
    if account_count < signature_count:
        raise ValueError("transaction signer count exceeds account keys")
    if account_keys_offset + account_count * SOLANA_PUBKEY_BYTES > len(raw):
        raise ValueError("transaction account keys truncated")
    signer_index = -1
    for index in range(signature_count):
        offset = account_keys_offset + index * SOLANA_PUBKEY_BYTES
        if bytes(raw[offset : offset + SOLANA_PUBKEY_BYTES]) == wallet_bytes:
            signer_index = index
            break
    if signer_index < 0:
        raise ValueError("wallet signer not present in transaction")
    signature = keypair.signing_key.sign(bytes(raw[message_offset:])).signature
    start = signatures_offset + signer_index * SOLANA_SIGNATURE_BYTES
    raw[start : start + SOLANA_SIGNATURE_BYTES] = signature
    return base64.b64encode(raw).decode("ascii")


def _build_signed_command_proof(
    keypair: Keypair,
    partition_id: int,
    subaccount_id: int,
    expiry_seconds: int,
    domain: bytes,
    command: bytes,
    expected_size: int,
) -> bytes:
    partition = _u16(partition_id)
    now_sec = int(time.time())
    exp_sec = now_sec + expiry_seconds
    prefix = keypair.public_key + partition + _u64(subaccount_id) + _u64(now_sec) + _u64(exp_sec)
    payload = domain + b"\x00" + prefix + command
    signature = keypair.signing_key.sign(payload).signature
    proof = prefix + command + keypair.public_key + signature
    if len(proof) != expected_size:
        raise ValueError("invalid signed command proof size")
    return proof


def _decode_shortvec(raw: bytes | bytearray, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    pos = offset
    while pos < len(raw) and shift < 64:
        byte = raw[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return value, pos
        shift += 7
    raise ValueError("invalid shortvec")


def _json_byte(value: object) -> int:
    if not isinstance(value, int) or value < 0 or value > 255:
        raise ValueError("keypair JSON contains a non-byte value")
    return value


def _check_pubkey(value: bytes, label: str) -> None:
    if len(value) != SOLANA_PUBKEY_BYTES:
        raise ValueError(f"{label} must be 32 bytes")


def _check_request_id(value: int) -> None:
    if value < 0 or value > REQUEST_ID_PUBLIC_MAX:
        raise ValueError("request_id must leave bit 63 clear")


def _check_i32(value: int, label: str) -> None:
    if value < INT32_MIN or value > INT32_MAX:
        raise ValueError(f"{label} out of int32 range")


def _u8(value: int, label: str) -> int:
    if value < 0 or value > 255:
        raise ValueError(f"{label} out of uint8 range")
    return value


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def _i32(value: int) -> bytes:
    return struct.pack("<i", value)
