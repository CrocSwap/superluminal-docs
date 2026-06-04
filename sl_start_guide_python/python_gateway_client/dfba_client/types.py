from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum

UINT32_MAX = 2**32 - 1
UINT64_MAX = 2**64 - 1
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
REQUEST_ID_PUBLIC_MAX = 2**63 - 1

DEFAULT_PARTITION_ID = 1


class DecodeError(ValueError):
    pass


class OrderMode(IntEnum):
    GTC_LIMIT = 0
    MARK_TO_MID = 1
    MARKET = 2


class OrderRole(IntEnum):
    MAKER = 0
    TAKER = 1


class SessionPhase(Enum):
    IDLE = "idle"
    AWAITING_AUTH_CHALLENGE = "awaiting_auth_challenge"
    AWAITING_AUTH_ACK = "awaiting_auth_ack"
    RUNNING = "running"
    FAILED = "failed"
    AUTH_EXPIRED = "auth_expired"


class BookType(Enum):
    MAKER = "maker"
    TAKER = "taker"


@dataclass(frozen=True, slots=True)
class ClientConfig:
    ws_url: str
    keypair_path: str
    feed_ids: tuple[int, ...]
    subaccount_id: int
    partition_id: int = DEFAULT_PARTITION_ID
    wallet_pubkey: str = ""
    auth_expiry_seconds: int = 0


@dataclass(frozen=True, slots=True)
class MarketInfo:
    feed_id: int
    symbol: str
    quote: str
    px_exponent: int
    size_decimals: int
    status: str = "active"

    def label(self) -> str:
        return self.symbol if self.quote == "" else f"{self.symbol}/{self.quote}"


@dataclass(frozen=True, slots=True)
class OrderDraft:
    symbol: int
    mode: OrderMode
    role: OrderRole
    is_sell: bool
    qty: int
    price: int = 0
    delta_ppm: int = 0


@dataclass(frozen=True, slots=True)
class LadderRow:
    price: int
    bid_qty: float
    ask_qty: float


@dataclass(frozen=True, slots=True)
class SymbolView:
    feed_id: int
    symbol: str = ""
    px_exponent: int = 0
    size_decimals: int = 0
    has_symbology: bool = False
    seq: int = 0
    unix_ns: int = 0
    synced: bool = False
    has_mid: bool = False
    best_bid_price: int = 0
    best_ask_price: int = 0
    has_funding_index: bool = False
    funding_index_e8: int = 0
    asks: tuple[LadderRow, ...] = ()
    bids: tuple[LadderRow, ...] = ()


@dataclass(frozen=True, slots=True)
class UiSnapshot:
    wallet_pubkey: str = ""
    ws_url: str = ""
    connection_status: str = "disconnected"
    auth_status: str = "idle"
    account_status: str = "idle"
    last_notice: str = ""
    last_error: str = ""
    books: tuple[SymbolView, ...] = ()
    gateway_rtt_avg_ns: float = 0.0
    gateway_rtt_samples: int = 0
    account_stream_started: bool = False
    has_account_snapshot: bool = False
    account_last_seq: int = 0
    seq_match_latency_avg_ns: float = 0.0
    seq_match_latency_samples: int = 0
    subaccounts: tuple["AccountSubaccountRow", ...] = ()
    recent_fills: tuple["FillRow", ...] = ()
    trade_lines: tuple[str, ...] = ()
    log_lines: tuple[str, ...] = ()


@dataclass(slots=True)
class PositionRow:
    symbol: int
    net_qty: int
    cost_basis: int
    pending_fees: int
    funding_accrued: int
    last_funding_index_e8: int


@dataclass(slots=True)
class WorkingOrderRow:
    external_order_id: int
    client_instruction_id: int
    symbol: int
    side: str
    role: str
    tif: str
    price: int
    original_qty: int
    remaining_qty: int


@dataclass(slots=True)
class FilledOrderRow:
    external_order_id: int
    client_instruction_id: int
    symbol: int
    side: str
    last_price: int
    filled_qty: int
    pending_settlement_qty: int
    settled_qty: int
    busted_qty: int


@dataclass(slots=True)
class FillRow:
    seq: int
    trade_id: int
    subaccount_id: int
    client_instruction_id: int
    external_order_id: int
    symbol: int
    side: str
    role: str
    price: int
    qty: int
    order_settled_qty: int = 0
    event_unix_ns: int = 0
    settle_delta_ns: int = 0
    has_order_settled_qty: bool = False
    has_event_unix_ns: bool = False
    has_settle_delta_ns: bool = False
    settled: bool = False


@dataclass(slots=True)
class AccountSubaccountRow:
    subaccount_id: int
    collateral_balance: int = 0
    positions: list[PositionRow] = field(default_factory=list)
    working_orders: list[WorkingOrderRow] = field(default_factory=list)
    filled_orders: list[FilledOrderRow] = field(default_factory=list)

    def frozen_copy(self) -> "AccountSubaccountRow":
        return AccountSubaccountRow(
            subaccount_id=self.subaccount_id,
            collateral_balance=self.collateral_balance,
            positions=[replace(row) for row in self.positions],
            working_orders=[replace(row) for row in self.working_orders],
            filled_orders=[replace(row) for row in self.filled_orders],
        )


class RunningAvg:
    __slots__ = ("_mean", "_sample_count")

    def __init__(self) -> None:
        self._mean = 0.0
        self._sample_count = 0

    def reset(self) -> None:
        self._mean = 0.0
        self._sample_count = 0

    def add_sample(self, value: float) -> None:
        self._sample_count += 1
        if self._sample_count == 1:
            self._mean = value
            return
        self._mean += (value - self._mean) / float(self._sample_count)

    @property
    def mean(self) -> float:
        return self._mean

    @property
    def sample_count(self) -> int:
        return self._sample_count
