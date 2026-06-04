from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from dfba_client.types import BookType


@dataclass(frozen=True, slots=True)
class AckMessage:
    status: str
    reason: str = ""
    client_instruction_id: int | None = None
    external_order_id: int | None = None


@dataclass(frozen=True, slots=True)
class AuthChallengeMessage:
    wallet_pubkey: str
    nonce: str
    message: str
    expires_unix_ms: int


@dataclass(frozen=True, slots=True)
class AuthExpiredMessage:
    reason: str


@dataclass(frozen=True, slots=True)
class SessionPreemptedMessage:
    reason: str


@dataclass(frozen=True, slots=True)
class PongMessage:
    client_unix_ns: int


@dataclass(frozen=True, slots=True)
class CreateVaultStatusMessage:
    message_type: Literal["CreateVaultStatus", "CreateVault"]
    client_instruction_id: int
    source_subaccount_id: int
    operation_subaccount_id: int
    vault_id: int
    status: int
    error_code: int
    total_vault_shares: int | None = None


@dataclass(frozen=True, slots=True)
class BookLevelMessage:
    price: int
    qty: int
    order_count: int = 0


@dataclass(frozen=True, slots=True)
class BookSnapshotMessage:
    seq: int
    book_type: BookType
    feed_id: int
    symbol: str
    book_cursor: int
    batch_id: int
    term: int
    matcher_unix_ns: int
    depth_cap: int
    bids: tuple[BookLevelMessage, ...]
    asks: tuple[BookLevelMessage, ...]
    gateway_send_unix_ns: int


@dataclass(frozen=True, slots=True)
class BookDeltaMessage:
    seq: int
    book_type: BookType
    feed_id: int
    symbol: str
    prev_book_cursor: int
    book_cursor: int
    batch_id: int
    term: int
    matcher_unix_ns: int
    depth_cap: int
    mark_price: int
    bids: tuple[BookLevelMessage, ...]
    asks: tuple[BookLevelMessage, ...]
    gateway_send_unix_ns: int


@dataclass(frozen=True, slots=True)
class StreamResetMessage:
    book_type: BookType
    feed_id: int


@dataclass(frozen=True, slots=True)
class TradeMessage:
    seq: int
    trade_id: int
    price: int
    qty: int
    symbol_id: int
    symbol: str
    side: str


@dataclass(frozen=True, slots=True)
class AccountPositionMessage:
    symbol: int
    net_qty: int
    cost_basis: int
    pending_fees: int
    funding_accrued: int
    last_funding_index_e8: int


@dataclass(frozen=True, slots=True)
class AccountWorkingOrderMessage:
    external_order_id: int
    client_instruction_id: int
    symbol: int
    side: str
    role: str
    tif: str
    order_type: int
    price: int
    original_qty: int
    remaining_qty: int
    delta_ppm: int


@dataclass(frozen=True, slots=True)
class AccountFilledOrderMessage:
    external_order_id: int
    client_instruction_id: int
    symbol: int
    side: str
    last_price: int
    filled_qty: int
    pending_settlement_qty: int
    settled_qty: int
    busted_qty: int


@dataclass(frozen=True, slots=True)
class AccountSubaccountMessage:
    subaccount_id: int
    collateral_balance: int
    positions: tuple[AccountPositionMessage, ...]
    working_orders: tuple[AccountWorkingOrderMessage, ...]
    filled_orders: tuple[AccountFilledOrderMessage, ...]


@dataclass(frozen=True, slots=True)
class AccountVaultAccountMessage:
    vault_id: int
    operation_subaccount_id: int
    leader_source_subaccount_id: int
    collateral_amount: int
    total_vault_shares: int
    last_seq: int


@dataclass(frozen=True, slots=True)
class AccountSnapshotMessage:
    last_seq: int
    subaccounts: tuple[AccountSubaccountMessage, ...]
    vault_accounts: tuple[AccountVaultAccountMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountSnapshotErrorMessage:
    wallet_pubkey: str
    message: str


@dataclass(frozen=True, slots=True)
class OrderAcceptedMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    symbol: int
    side: str
    role: str
    tif: str
    order_type: int
    price: int
    qty: int
    delta_ppm: int
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class OrderRejectMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    reason: int
    symbol: int
    side: str
    role: str
    tif: str
    order_type: int
    price: int
    qty: int
    delta_ppm: int
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class LocalOrderRejectMessage:
    reason: str
    client_instruction_id: int | None = None
    external_order_id: int | None = None
    gateway_send_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class FillMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    trade_id: int
    symbol: int
    side: str
    role: str
    price: int
    qty: int
    remaining_qty: int | None = None
    fee: int | None = None
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None
    gateway_send_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class FillSettlementMessage:
    message_type: Literal["FillSettled", "FillBusted"]
    seq: int
    subaccount_id: int
    external_order_id: int
    symbol: int
    side: str
    price: int
    qty: int
    fee: int | None = None
    gateway_send_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class AccountFundingUpdateMessage:
    seq: int
    subaccount_id: int
    symbol: int
    market_id: int
    prev_funding_index_e8: int
    last_funding_index_e8: int
    funding_delta: int
    funding_accrued: int


@dataclass(frozen=True, slots=True)
class FundingIndexEntryMessage:
    market_id: int
    perp_price: int
    premium_e8: int
    cum_funding_index_e8: int


@dataclass(frozen=True, slots=True)
class FundingIndexMessage:
    seq: int
    entries: tuple[FundingIndexEntryMessage, ...]
    oracle_price_batch_id: int | None = None


@dataclass(frozen=True, slots=True)
class CancelAckMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class CancelRejectMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    reason: int
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class ModifyAckMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    symbol: int
    price: int
    qty: int
    delta_ppm: int
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class ModifyRejectMessage:
    seq: int
    subaccount_id: int
    external_order_id: int
    client_instruction_id: int
    reason: int
    matcher_unix_ns: int | None = None
    sequencer_recv_unix_ns: int | None = None


@dataclass(frozen=True, slots=True)
class UnknownMessage:
    message_type: str
    payload: str


GatewayMessage: TypeAlias = (
    AckMessage
    | AuthChallengeMessage
    | AuthExpiredMessage
    | SessionPreemptedMessage
    | PongMessage
    | CreateVaultStatusMessage
    | BookSnapshotMessage
    | BookDeltaMessage
    | StreamResetMessage
    | TradeMessage
    | AccountSnapshotMessage
    | AccountSnapshotErrorMessage
    | OrderAcceptedMessage
    | OrderRejectMessage
    | LocalOrderRejectMessage
    | FillMessage
    | FillSettlementMessage
    | AccountFundingUpdateMessage
    | FundingIndexMessage
    | CancelAckMessage
    | CancelRejectMessage
    | ModifyAckMessage
    | ModifyRejectMessage
    | UnknownMessage
)
