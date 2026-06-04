from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypeAlias, cast

from dfba_client.messages import (
    AccountFilledOrderMessage,
    AccountFundingUpdateMessage,
    AccountPositionMessage,
    AccountSnapshotErrorMessage,
    AccountSnapshotMessage,
    AccountSubaccountMessage,
    AccountVaultAccountMessage,
    AccountWorkingOrderMessage,
    AckMessage,
    AuthChallengeMessage,
    AuthExpiredMessage,
    BookDeltaMessage,
    BookLevelMessage,
    BookSnapshotMessage,
    CancelAckMessage,
    CancelRejectMessage,
    CreateVaultStatusMessage,
    FillMessage,
    FillSettlementMessage,
    FundingIndexEntryMessage,
    FundingIndexMessage,
    GatewayMessage,
    LocalOrderRejectMessage,
    ModifyAckMessage,
    ModifyRejectMessage,
    OrderAcceptedMessage,
    OrderRejectMessage,
    PongMessage,
    SessionPreemptedMessage,
    StreamResetMessage,
    TradeMessage,
    UnknownMessage,
)
from dfba_client.types import (
    INT32_MAX,
    INT32_MIN,
    INT64_MAX,
    INT64_MIN,
    UINT32_MAX,
    UINT64_MAX,
    BookType,
    DecodeError,
)

JsonObject: TypeAlias = dict[str, Any]


def decode_gateway_message(raw: str) -> GatewayMessage:
    payload = _parse_object(raw)
    msg_type = _required_str(payload, "type")
    if msg_type == "ack":
        return _decode_ack(payload)
    if msg_type == "auth_challenge":
        return AuthChallengeMessage(
            wallet_pubkey=_required_str(payload, "wallet_pubkey"),
            nonce=_required_str(payload, "nonce"),
            message=_required_str(payload, "message"),
            expires_unix_ms=_required_u64(payload, "expires_unix_ms"),
        )
    if msg_type == "auth_expired":
        return AuthExpiredMessage(reason=_required_str(payload, "reason"))
    if msg_type == "session_preempted":
        return SessionPreemptedMessage(reason=_required_str(payload, "reason"))
    if msg_type == "pong":
        return PongMessage(client_unix_ns=_required_u64(payload, "client_unix_ns"))
    if msg_type == "CreateVaultStatus" or msg_type == "CreateVault":
        return _decode_create_vault_status(payload, cast("LiteralCreateVaultType", msg_type))
    if msg_type == "BookSnapshot":
        return _decode_book_snapshot(payload)
    if msg_type == "BookDelta":
        return _decode_book_delta(payload)
    if msg_type == "stream_reset":
        return StreamResetMessage(
            book_type=_required_book_type(payload),
            feed_id=_required_u32_nonzero(payload, "symbol"),
        )
    if msg_type == "Trade":
        return TradeMessage(
            seq=_required_u64(payload, "seq"),
            trade_id=_optional_u64(payload, "trade_id") or 0,
            price=_required_u64(payload, "price"),
            qty=_required_u64(payload, "qty"),
            symbol_id=_required_u32_nonzero(payload, "id"),
            symbol=_optional_str(payload, "symbol") or "",
            side=_required_str(payload, "side"),
        )
    if msg_type == "AccountSnapshot":
        return _decode_account_snapshot(payload)
    if msg_type == "AccountSnapshotError":
        return AccountSnapshotErrorMessage(
            wallet_pubkey=_optional_str(payload, "wallet_pubkey") or "",
            message=_required_str(payload, "message"),
        )
    if msg_type == "OrderAccepted":
        return _decode_order_accepted(payload)
    if msg_type == "OrderReject":
        if "seq" in payload and "subaccount_id" in payload:
            return _decode_order_reject(payload)
        return LocalOrderRejectMessage(
            reason=_required_str(payload, "reason"),
            client_instruction_id=_optional_u64(payload, "client_instruction_id"),
            external_order_id=_optional_u64(payload, "external_order_id"),
            gateway_send_unix_ns=_optional_u64(payload, "gateway_send_unix_ns"),
        )
    if msg_type == "Fill":
        return _decode_fill(payload)
    if msg_type == "FillSettled" or msg_type == "FillBusted":
        return _decode_fill_settlement(payload, cast("LiteralFillSettlementType", msg_type))
    if msg_type == "AccountFundingUpdate":
        return _decode_account_funding_update(payload)
    if msg_type == "FundingIndex":
        return _decode_funding_index(payload)
    if msg_type == "CancelAck":
        return _decode_cancel_ack(payload)
    if msg_type == "CancelReject":
        return _decode_cancel_reject(payload)
    if msg_type == "ModifyAck":
        return _decode_modify_ack(payload)
    if msg_type == "ModifyReject":
        return _decode_modify_reject(payload)
    return UnknownMessage(message_type=msg_type, payload=raw)


LiteralCreateVaultType: TypeAlias = Literal["CreateVaultStatus", "CreateVault"]
LiteralFillSettlementType: TypeAlias = Literal["FillSettled", "FillBusted"]


def _decode_ack(payload: JsonObject) -> AckMessage:
    return AckMessage(
        status=_required_str(payload, "status"),
        reason=_optional_str(payload, "reason") or "",
        client_instruction_id=_optional_u64(payload, "client_instruction_id"),
        external_order_id=_optional_u64(payload, "external_order_id"),
    )


def _decode_create_vault_status(
    payload: JsonObject,
    msg_type: LiteralCreateVaultType,
) -> CreateVaultStatusMessage:
    return CreateVaultStatusMessage(
        message_type=msg_type,
        client_instruction_id=_required_u64(payload, "client_instruction_id"),
        source_subaccount_id=_required_u64(payload, "source_subaccount_id"),
        operation_subaccount_id=_required_u64(payload, "operation_subaccount_id"),
        vault_id=_required_u32(payload, "vault_id"),
        status=_required_u64(payload, "status"),
        error_code=_required_u64(payload, "error_code"),
        total_vault_shares=_optional_u64(payload, "total_vault_shares"),
    )


def _decode_book_snapshot(payload: JsonObject) -> BookSnapshotMessage:
    return BookSnapshotMessage(
        seq=_optional_u64(payload, "seq") or 0,
        book_type=_required_book_type(payload),
        feed_id=_required_u32_nonzero(payload, "id"),
        symbol=_optional_str(payload, "symbol") or "",
        book_cursor=_optional_u64(payload, "book_cursor") or 0,
        batch_id=_optional_u64(payload, "batch_id") or 0,
        term=_optional_u64(payload, "term") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns") or 0,
        depth_cap=_optional_u64(payload, "depth_cap") or 0,
        bids=_decode_levels(payload, "bids"),
        asks=_decode_levels(payload, "asks"),
        gateway_send_unix_ns=_optional_u64(payload, "gateway_send_unix_ns") or 0,
    )


def _decode_book_delta(payload: JsonObject) -> BookDeltaMessage:
    return BookDeltaMessage(
        seq=_optional_u64(payload, "seq") or 0,
        book_type=_required_book_type(payload),
        feed_id=_required_u32_nonzero(payload, "id"),
        symbol=_optional_str(payload, "symbol") or "",
        prev_book_cursor=_optional_u64(payload, "prev_book_cursor") or 0,
        book_cursor=_optional_u64(payload, "book_cursor") or 0,
        batch_id=_optional_u64(payload, "batch_id") or 0,
        term=_optional_u64(payload, "term") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns") or 0,
        depth_cap=_optional_u64(payload, "depth_cap") or 0,
        mark_price=_optional_u64(payload, "mark_price") or 0,
        bids=_decode_levels(payload, "bids"),
        asks=_decode_levels(payload, "asks"),
        gateway_send_unix_ns=_optional_u64(payload, "gateway_send_unix_ns") or 0,
    )


def _decode_account_snapshot(payload: JsonObject) -> AccountSnapshotMessage:
    subaccounts = []
    for raw_subaccount in _required_array(payload, "subaccounts"):
        subaccount = _as_object(raw_subaccount, "subaccounts[]")
        positions = tuple(
            AccountPositionMessage(
                symbol=_required_u32_nonzero(position, "symbol"),
                net_qty=_required_i64(position, "net_qty"),
                cost_basis=_required_i64(position, "cost_basis"),
                pending_fees=_required_i64(position, "pending_fees"),
                funding_accrued=_required_i64(position, "funding_accrued"),
                last_funding_index_e8=_required_i64(position, "last_funding_index_e8"),
            )
            for position in (_as_object(item, "positions[]") for item in _required_array(subaccount, "positions"))
        )
        working_orders = tuple(
            _decode_account_working_order(order)
            for order in (
                _as_object(item, "working_orders[]")
                for item in _required_array(subaccount, "working_orders")
            )
        )
        filled_orders = tuple(
            _decode_account_filled_order(order)
            for order in (
                _as_object(item, "filled_orders[]")
                for item in _required_array(subaccount, "filled_orders")
            )
        )
        subaccounts.append(
            AccountSubaccountMessage(
                subaccount_id=_required_u64(subaccount, "subaccount_id"),
                collateral_balance=_optional_i64(subaccount, "collateral_balance") or 0,
                positions=positions,
                working_orders=working_orders,
                filled_orders=filled_orders,
            )
        )
    return AccountSnapshotMessage(
        last_seq=_required_u64(payload, "last_seq"),
        subaccounts=tuple(subaccounts),
        vault_accounts=tuple(
            AccountVaultAccountMessage(
                vault_id=_required_u32(vault, "vault_id"),
                operation_subaccount_id=_required_u64(vault, "operation_subaccount_id"),
                leader_source_subaccount_id=_required_u64(vault, "leader_source_subaccount_id"),
                collateral_amount=_required_u64(vault, "collateral_amount"),
                total_vault_shares=_required_u64(vault, "total_vault_shares"),
                last_seq=_required_u64(vault, "last_seq"),
            )
            for vault in (
                _as_object(item, "vault_accounts[]")
                for item in _optional_array(payload, "vault_accounts")
            )
        ),
    )


def _decode_account_working_order(order: JsonObject) -> AccountWorkingOrderMessage:
    side = _optional_str(order, "side") or ""
    role = _optional_str(order, "role") or ""
    tif = _optional_str(order, "tif") or ""
    original_qty = _required_nonzero_u64(order, "original_qty")
    remaining_qty = _required_u64(order, "remaining_qty")
    if remaining_qty > original_qty:
        raise DecodeError("field remaining_qty exceeds original_qty")
    _require_valid_side(side, "side")
    _require_valid_role(role, "role")
    _require_valid_tif(tif, "tif")
    return AccountWorkingOrderMessage(
        external_order_id=_required_nonzero_u64(order, "external_order_id"),
        client_instruction_id=_required_u64(order, "client_instruction_id"),
        symbol=_required_u32_nonzero(order, "symbol"),
        side=side,
        role=role,
        tif=tif,
        order_type=_optional_u64(order, "order_type") or 0,
        price=_optional_u64(order, "price") or 0,
        original_qty=original_qty,
        remaining_qty=remaining_qty,
        delta_ppm=_optional_i64(order, "delta_ppm") or 0,
    )


def _decode_account_filled_order(order: JsonObject) -> AccountFilledOrderMessage:
    side = _optional_str(order, "side") or ""
    _require_valid_side(side, "side")
    filled_qty = _required_u64(order, "filled_qty")
    pending_qty = _required_u64(order, "pending_settlement_qty")
    settled_qty = _required_u64(order, "settled_qty")
    busted_qty = _required_u64(order, "busted_qty")
    if pending_qty + settled_qty + busted_qty != filled_qty:
        raise DecodeError("filled order quantities do not reconcile")
    return AccountFilledOrderMessage(
        external_order_id=_required_nonzero_u64(order, "external_order_id"),
        client_instruction_id=_required_u64(order, "client_instruction_id"),
        symbol=_required_u32_nonzero(order, "symbol"),
        side=side,
        last_price=_required_u64(order, "last_price"),
        filled_qty=filled_qty,
        pending_settlement_qty=pending_qty,
        settled_qty=settled_qty,
        busted_qty=busted_qty,
    )


def _decode_order_accepted(payload: JsonObject) -> OrderAcceptedMessage:
    side = _required_str(payload, "side")
    role = _optional_str(payload, "role") or ""
    tif = _optional_str(payload, "tif") or ""
    _require_valid_side(side, "side")
    _require_valid_role(role, "role")
    _require_valid_tif(tif, "tif")
    return OrderAcceptedMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        symbol=_required_u32_nonzero(payload, "symbol"),
        side=side,
        role=role,
        tif=tif,
        order_type=_optional_u64(payload, "order_type") or 0,
        price=_optional_u64(payload, "price") or 0,
        qty=_required_nonzero_u64(payload, "qty"),
        delta_ppm=_optional_i64(payload, "delta_ppm") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
    )


def _decode_order_reject(payload: JsonObject) -> OrderRejectMessage:
    side = _optional_str(payload, "side") or ""
    role = _optional_str(payload, "role") or ""
    tif = _optional_str(payload, "tif") or ""
    symbol = _optional_u32_like_cpp(payload, "symbol") or 0
    if side != "":
        _require_valid_side(side, "side")
    _require_valid_role(role, "role")
    _require_valid_tif(tif, "tif")
    return OrderRejectMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_optional_u64(payload, "external_order_id") or 0,
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        reason=_optional_u64(payload, "reason") or 0,
        symbol=symbol,
        side=side,
        role=role,
        tif=tif,
        order_type=_optional_u64(payload, "order_type") or 0,
        price=_optional_u64(payload, "price") or 0,
        qty=_optional_u64(payload, "qty") or 0,
        delta_ppm=_optional_i64(payload, "delta_ppm") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
    )


def _decode_fill(payload: JsonObject) -> FillMessage:
    side = _required_str(payload, "side")
    role = _optional_str(payload, "role") or ""
    _require_valid_side(side, "side")
    _require_valid_role(role, "role")
    return FillMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        trade_id=_optional_u64(payload, "trade_id") or 0,
        symbol=_required_u32_nonzero(payload, "symbol"),
        side=side,
        role=role,
        price=_optional_u64(payload, "price") or 0,
        qty=_required_nonzero_u64(payload, "qty"),
        remaining_qty=_optional_u64(payload, "remaining_qty"),
        fee=_optional_i64(payload, "fee"),
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
        gateway_send_unix_ns=_optional_u64(payload, "gateway_send_unix_ns"),
    )


def _decode_fill_settlement(
    payload: JsonObject,
    msg_type: LiteralFillSettlementType,
) -> FillSettlementMessage:
    side = _required_str(payload, "side")
    _require_valid_side(side, "side")
    return FillSettlementMessage(
        message_type=msg_type,
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        symbol=_required_u32_nonzero(payload, "symbol"),
        side=side,
        price=_optional_u64(payload, "price") or 0,
        qty=_required_nonzero_u64(payload, "qty"),
        fee=_optional_i64(payload, "fee"),
        gateway_send_unix_ns=_optional_u64(payload, "gateway_send_unix_ns"),
    )


def _decode_account_funding_update(payload: JsonObject) -> AccountFundingUpdateMessage:
    market_id = _required_u32(payload, "market_id")
    symbol = _optional_u32_like_cpp(payload, "symbol")
    prev_funding_index_e8 = _optional_i64(payload, "prev_funding_index_e8") or 0
    last_funding_index_e8 = _optional_i64(payload, "last_funding_index_e8") or 0
    return AccountFundingUpdateMessage(
        seq=_required_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        symbol=market_id if symbol is None else symbol,
        market_id=market_id,
        prev_funding_index_e8=prev_funding_index_e8,
        last_funding_index_e8=last_funding_index_e8,
        funding_delta=_optional_i64(payload, "funding_delta") or 0,
        funding_accrued=_optional_i64(payload, "funding_accrued") or 0,
    )


def _decode_funding_index(payload: JsonObject) -> FundingIndexMessage:
    entries = tuple(
        FundingIndexEntryMessage(
            market_id=_required_u32(entry, "market_id"),
            perp_price=_optional_u64(entry, "perp_price") or 0,
            premium_e8=_optional_i64(entry, "premium_e8") or 0,
            cum_funding_index_e8=_required_i64(entry, "cum_funding_index_e8"),
        )
        for entry in (_as_object(item, "entries[]") for item in _required_array(payload, "entries"))
    )
    return FundingIndexMessage(
        seq=_required_u64(payload, "seq"),
        entries=entries,
        oracle_price_batch_id=_optional_u64(payload, "oracle_price_batch_id"),
    )


def _decode_modify_ack(payload: JsonObject) -> ModifyAckMessage:
    delta_ppm = _optional_i64(payload, "delta_ppm") or 0
    if delta_ppm < INT32_MIN or delta_ppm > INT32_MAX:
        raise DecodeError("field delta_ppm out of int32 range")
    symbol = _optional_u32_like_cpp(payload, "symbol") or 0
    return ModifyAckMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        symbol=symbol,
        price=_optional_u64(payload, "price") or 0,
        qty=_required_nonzero_u64(payload, "qty"),
        delta_ppm=delta_ppm,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
    )


def _decode_cancel_ack(payload: JsonObject) -> CancelAckMessage:
    return CancelAckMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
    )


def _decode_cancel_reject(payload: JsonObject) -> CancelRejectMessage:
    return CancelRejectMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        reason=_optional_u64(payload, "reason") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
    )


def _decode_modify_reject(payload: JsonObject) -> ModifyRejectMessage:
    return ModifyRejectMessage(
        seq=_required_nonzero_u64(payload, "seq"),
        subaccount_id=_required_u64(payload, "subaccount_id"),
        external_order_id=_required_nonzero_u64(payload, "external_order_id"),
        client_instruction_id=_optional_u64(payload, "client_instruction_id") or 0,
        reason=_optional_u64(payload, "reason") or 0,
        matcher_unix_ns=_optional_u64(payload, "matcher_unix_ns"),
        sequencer_recv_unix_ns=_optional_u64(payload, "sequencer_recv_unix_ns"),
    )


def _decode_levels(payload: JsonObject, field: str) -> tuple[BookLevelMessage, ...]:
    raw_levels = payload.get(field, [])
    if not isinstance(raw_levels, Sequence) or isinstance(raw_levels, str | bytes | bytearray):
        raise DecodeError(f"field {field} must be an array")
    levels = []
    for item in raw_levels:
        obj = _as_object(item, f"{field}[]")
        levels.append(
            BookLevelMessage(
                price=_required_u64(obj, "price"),
                qty=_required_u64(obj, "qty"),
                order_count=_optional_u64(obj, "order_count") or 0,
            )
        )
    return tuple(levels)


def _parse_object(raw: str) -> JsonObject:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DecodeError("payload is not valid json") from exc
    return _as_object(value, "payload")


def _as_object(value: Any, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise DecodeError(f"{label} must be an object")
    return dict(value)


def _required_array(payload: JsonObject, field: str) -> list[Any]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise DecodeError(f"field {field} must be an array")
    return value


def _optional_array(payload: JsonObject, field: str) -> list[Any]:
    value = payload.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise DecodeError(f"field {field} must be an array")
    return value


def _required_str(payload: JsonObject, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise DecodeError(f"field {field} must be a string")
    return value


def _optional_str(payload: JsonObject, field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DecodeError(f"field {field} must be a string")
    return value


def _required_u32_nonzero(payload: JsonObject, field: str) -> int:
    value = _required_u64(payload, field)
    if value == 0 or value > UINT32_MAX:
        raise DecodeError(f"field {field} out of uint32 nonzero range")
    return value


def _required_u32(payload: JsonObject, field: str) -> int:
    value = _required_u64(payload, field)
    if value > UINT32_MAX:
        raise DecodeError(f"field {field} out of uint32 range")
    return value


def _optional_u32_like_cpp(payload: JsonObject, field: str) -> int | None:
    if field not in payload:
        return None
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > UINT32_MAX:
        return None
    return value


def _required_nonzero_u64(payload: JsonObject, field: str) -> int:
    value = _required_u64(payload, field)
    if value == 0:
        raise DecodeError(f"field {field} must be nonzero")
    return value


def _require_valid_side(value: str, field: str) -> None:
    if value != "BID" and value != "ASK":
        raise DecodeError(f"field {field} must be BID or ASK")


def _require_valid_role(value: str, field: str) -> None:
    if value != "" and value != "MAKER" and value != "TAKER":
        raise DecodeError(f"field {field} must be empty, MAKER, or TAKER")


def _require_valid_tif(value: str, field: str) -> None:
    if value != "" and value != "GTC" and value != "IOC":
        raise DecodeError(f"field {field} must be empty, GTC, or IOC")


def _required_u64(payload: JsonObject, field: str) -> int:
    value = payload.get(field)
    return _coerce_int_range(value, field, 0, UINT64_MAX)


def _optional_u64(payload: JsonObject, field: str) -> int | None:
    if field not in payload:
        return None
    return _coerce_int_range(payload.get(field), field, 0, UINT64_MAX)


def _required_i64(payload: JsonObject, field: str) -> int:
    value = payload.get(field)
    return _coerce_int_range(value, field, INT64_MIN, INT64_MAX)


def _optional_i64(payload: JsonObject, field: str) -> int | None:
    if field not in payload:
        return None
    return _coerce_int_range(payload.get(field), field, INT64_MIN, INT64_MAX)


def _coerce_int_range(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DecodeError(f"field {field} must be an integer")
    if value < low or value > high:
        raise DecodeError(f"field {field} out of range")
    return value


def _required_book_type(payload: JsonObject) -> BookType:
    value = _required_str(payload, "bookType")
    if value == BookType.MAKER.value:
        return BookType.MAKER
    if value == BookType.TAKER.value:
        return BookType.TAKER
    raise DecodeError("field bookType must be maker or taker")
