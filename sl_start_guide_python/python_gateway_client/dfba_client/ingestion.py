from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from dfba_client.account_state import AccountState
from dfba_client.book_sync import (
    BookLevel,
    BookSyncEngine,
    BookUpdate,
    BookUpdateKind,
    SyncActionType,
    wire_from_gateway_message,
)
from dfba_client.messages import (
    AccountFundingUpdateMessage,
    AccountSnapshotErrorMessage,
    AccountSnapshotMessage,
    AckMessage,
    AuthChallengeMessage,
    AuthExpiredMessage,
    BookDeltaMessage,
    BookSnapshotMessage,
    CancelAckMessage,
    CancelRejectMessage,
    CreateVaultStatusMessage,
    FillMessage,
    FillSettlementMessage,
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
from dfba_client.sorted_map import SortedIntMap
from dfba_client.types import (
    BookType,
    ClientConfig,
    LadderRow,
    MarketInfo,
    RunningAvg,
    SessionPhase,
    SymbolView,
    UiSnapshot,
)

MAX_LOG_LINES = 200
MAX_TRADE_LINES = 200
MAX_LADDER_ROWS = 24


@dataclass(slots=True)
class SymbolBookState:
    maker_engine: BookSyncEngine = field(default_factory=BookSyncEngine)
    taker_engine: BookSyncEngine = field(default_factory=BookSyncEngine)
    maker_book: SortedIntMap[BookLevel] = field(default_factory=SortedIntMap)
    taker_book: SortedIntMap[BookLevel] = field(default_factory=SortedIntMap)
    combined_levels: SortedIntMap[BookLevel] = field(default_factory=SortedIntMap)
    maker_synced: bool = False
    taker_synced: bool = False
    highest_seq: int = 0
    highest_unix_ns: int = 0
    has_funding_index: bool = False
    funding_index_e8: int = 0


@dataclass(slots=True)
class IngestionEngine:
    config: ClientConfig
    markets: dict[int, MarketInfo]
    wallet_pubkey: str
    connection_status: str = "disconnected"
    auth_status: str = "idle"
    account_status: str = "idle"
    phase: SessionPhase = SessionPhase.IDLE
    account_stream_started: bool = False
    auth_wallet_ack_received: bool = False
    last_notice: str = ""
    last_error: str = ""
    gateway_rtt_avg: RunningAvg = field(default_factory=RunningAvg)
    account_state: AccountState = field(default_factory=AccountState)
    books: dict[int, SymbolView] = field(default_factory=dict)
    book_states: dict[int, SymbolBookState] = field(default_factory=dict)
    trade_lines: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    resubscribe_markets: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        for feed_id in self.config.feed_ids:
            self.books[feed_id] = self._build_empty_symbol_view(feed_id)
            self.book_states[feed_id] = SymbolBookState()

    def on_open(self) -> None:
        self.connection_status = "connected"
        self.auth_status = "authenticating"
        self.account_status = "waiting for auth"
        self.phase = SessionPhase.AWAITING_AUTH_CHALLENGE
        self.account_stream_started = False
        self.auth_wallet_ack_received = False
        self.last_error = ""
        self.last_notice = "authenticating wallet"
        self.gateway_rtt_avg.reset()
        self.account_state.reset()
        self._log("websocket open")

    def on_close(self, reason: str) -> None:
        self.connection_status = "closed"
        if self.phase not in (SessionPhase.AUTH_EXPIRED, SessionPhase.FAILED):
            self.auth_status = "disconnected"
        self.last_notice = f"closed {reason}".strip()
        self.gateway_rtt_avg.reset()
        self._log(f"websocket closed {reason}".strip())

    def apply(self, message: GatewayMessage) -> None:
        if isinstance(message, AckMessage):
            self._apply_ack(message)
        elif isinstance(message, AuthChallengeMessage):
            self.phase = SessionPhase.AWAITING_AUTH_ACK
            self.last_notice = "auth challenge received"
        elif isinstance(message, PongMessage):
            self._apply_pong(message)
        elif isinstance(message, TradeMessage):
            self._apply_trade(message)
        elif isinstance(message, BookSnapshotMessage | BookDeltaMessage | StreamResetMessage):
            self._apply_book(message)
        elif isinstance(message, AccountSnapshotMessage):
            self.last_notice = self.account_state.apply_snapshot(message)
        elif isinstance(message, AccountSnapshotErrorMessage):
            payload = _account_snapshot_error_payload(message)
            self.last_notice = _truncate_notice(payload)
            self._log(f"recv AccountSnapshotError payload={payload}")
        elif isinstance(message, OrderAcceptedMessage):
            self.last_notice = self.account_state.apply_order_accepted(message)
        elif isinstance(message, OrderRejectMessage):
            self.last_notice = self.account_state.apply_order_reject(message)
        elif isinstance(message, LocalOrderRejectMessage):
            self.last_notice = self._local_order_reject_notice(message)
        elif isinstance(message, FillMessage):
            ok, notice, error = self.account_state.apply_fill(message, self.markets.get(message.symbol))
            self.last_notice = notice
            if ok:
                self.last_error = ""
            else:
                self.last_error = error
        elif isinstance(message, FillSettlementMessage):
            if message.message_type == "FillBusted":
                ok, notice, error = self.account_state.apply_fill_busted(
                    message,
                    self.markets.get(message.symbol),
                )
            else:
                ok, notice, error = self.account_state.apply_fill_settled(message)
            self.last_notice = notice
            if ok:
                self.last_error = ""
            else:
                self.last_error = error
        elif isinstance(message, AccountFundingUpdateMessage):
            ok, notice, error = self.account_state.apply_account_funding_update(message)
            self.last_notice = notice
            if ok:
                self.last_error = ""
                self._apply_funding_index(message.market_id, message.last_funding_index_e8)
            else:
                self.last_error = error
        elif isinstance(message, FundingIndexMessage):
            updated = 0
            for entry in message.entries:
                if self._apply_funding_index(entry.market_id, entry.cum_funding_index_e8):
                    updated += 1
            self.last_notice = f"funding index seq={message.seq} markets={updated}"
        elif isinstance(message, CreateVaultStatusMessage):
            self.last_notice = (
                f"create vault status cid={message.client_instruction_id} "
                f"vault={message.vault_id} status={message.status} error={message.error_code}"
            )
        elif isinstance(message, CancelAckMessage):
            self.last_notice = self.account_state.apply_cancel_ack(message)
        elif isinstance(message, CancelRejectMessage):
            self.last_notice = self.account_state.apply_cancel_reject(message)
        elif isinstance(message, ModifyAckMessage):
            self.last_notice = self.account_state.apply_modify_ack(message)
        elif isinstance(message, ModifyRejectMessage):
            self.last_notice = self.account_state.apply_modify_reject(message)
        elif isinstance(message, AuthExpiredMessage):
            self.phase = SessionPhase.AUTH_EXPIRED
            self.auth_status = "expired"
            self.last_notice = f"auth expired: {message.reason}"
        elif isinstance(message, SessionPreemptedMessage):
            self.phase = SessionPhase.FAILED
            self.connection_status = "preempted"
            self.auth_status = "preempted"
            self.account_status = "preempted"
            self.last_error = ""
            self.last_notice = f"session preempted: {message.reason}"
        elif isinstance(message, UnknownMessage):
            if message.message_type != "BatchCommit":
                self.last_notice = _truncate_notice(message.payload)
                self._log(f"unhandled payload={message.payload}")
        else:
            self._log(f"recv unhandled {type(message).__name__}")

    def snapshot(self) -> UiSnapshot:
        base = UiSnapshot(
            wallet_pubkey=self.wallet_pubkey,
            ws_url=self.config.ws_url,
            connection_status=self.connection_status,
            auth_status=self.auth_status,
            account_status=self.account_status,
            last_notice=self.last_notice,
            last_error=self.last_error,
            books=tuple(self.books[feed_id] for feed_id in sorted(self.books.keys())),
            gateway_rtt_avg_ns=self.gateway_rtt_avg.mean,
            gateway_rtt_samples=self.gateway_rtt_avg.sample_count,
            account_stream_started=self.account_stream_started,
            trade_lines=tuple(self.trade_lines),
            log_lines=tuple(self.log_lines),
        )
        return self.account_state.populate_snapshot(base)

    def mark_account_stream_started(self) -> None:
        self.account_stream_started = True
        self.last_notice = "subscribed account stream"

    def drain_resubscribe_markets(self) -> tuple[int, ...]:
        out = tuple(self.resubscribe_markets)
        self.resubscribe_markets.clear()
        return out

    def _apply_ack(self, ack: AckMessage) -> None:
        if ack.status == "accepted":
            if not self.auth_wallet_ack_received and self.phase in (
                SessionPhase.AWAITING_AUTH_CHALLENGE,
                SessionPhase.AWAITING_AUTH_ACK,
            ):
                self.auth_wallet_ack_received = True
                self.last_notice = "wallet challenge issued"
            elif self.phase == SessionPhase.AWAITING_AUTH_ACK:
                self.auth_status = "authenticated"
                self.account_status = "ready"
                self.phase = SessionPhase.RUNNING
                self.last_notice = "wallet authenticated"
                self.last_error = ""
            else:
                self.last_notice = "ack accepted"
        elif ack.status == "rejected" or ack.status == "busy":
            if self.phase in (SessionPhase.AWAITING_AUTH_CHALLENGE, SessionPhase.AWAITING_AUTH_ACK):
                self.last_error = f"authentication failed: {ack.reason}"
                self.last_notice = "wallet authentication failed"
                self.auth_status = "busy" if ack.status == "busy" else "rejected"
                self.account_status = "blocked"
                self.phase = SessionPhase.FAILED
            else:
                if ack.reason != "":
                    self.last_error = ack.reason
                self.last_notice = f"ack {ack.status}"
        else:
            self.last_notice = f"ack {ack.status}"

    def _apply_pong(self, message: PongMessage) -> None:
        now_ns = time.monotonic_ns()
        if now_ns >= message.client_unix_ns:
            self.gateway_rtt_avg.add_sample(float(now_ns - message.client_unix_ns))

    def _apply_trade(self, message: TradeMessage) -> None:
        market = self.markets.get(message.symbol_id)
        symbol = f"{message.symbol_id} {message.symbol}" if message.symbol != "" else str(message.symbol_id)
        qty = _format_qty(message.qty, market)
        price = _format_price(message.price, market)
        self._append_bounded(self.trade_lines, f"seq={message.seq} trade={message.trade_id} sym={symbol} {message.side} qty={qty} px={price}", MAX_TRADE_LINES)

    def _apply_book(self, message: BookSnapshotMessage | BookDeltaMessage | StreamResetMessage) -> None:
        wire = wire_from_gateway_message(message)
        state = self.book_states.setdefault(wire.feed_id, SymbolBookState())
        if wire.kind.value != "reset":
            state.highest_seq = max(state.highest_seq, wire.seq)
            state.highest_unix_ns = max(state.highest_unix_ns, wire.unix_ns)
        engine = state.taker_engine if wire.book_type == BookType.TAKER else state.maker_engine
        action = engine.handle_message(wire)
        if action.type == SyncActionType.GAP:
            _clear_book_update(state, wire.book_type)
            if wire.book_type == BookType.TAKER:
                state.taker_synced = False
            else:
                state.maker_synced = False
            self.resubscribe_markets.append(wire.feed_id)
            self.last_notice = f"book gap feed_id={wire.feed_id}"
        elif action.type == SyncActionType.EMIT and action.update is not None:
            _store_book_update(state, wire.book_type, action.update)
            if wire.book_type == BookType.TAKER:
                state.taker_synced = action.update.synced
            else:
                state.maker_synced = action.update.synced
            self.last_notice = f"book update feed_id={wire.feed_id}"
        self.books[wire.feed_id] = self._build_symbol_view(wire.feed_id)

    def _build_empty_symbol_view(self, feed_id: int) -> SymbolView:
        market = self.markets.get(feed_id)
        if market is None:
            return SymbolView(feed_id=feed_id)
        return SymbolView(
            feed_id=feed_id,
            symbol=market.label(),
            px_exponent=market.px_exponent,
            size_decimals=market.size_decimals,
            has_symbology=True,
        )

    def _build_symbol_view(self, feed_id: int) -> SymbolView:
        state = self.book_states.setdefault(feed_id, SymbolBookState())
        market = self.markets.get(feed_id)
        bid_rows: list[LadderRow] = []
        ask_rows: list[LadderRow] = []
        best_bid = 0
        best_ask = 0
        for price in state.combined_levels.keys():
            level = state.combined_levels[price]
            if level.ask_qty > 0:
                if best_ask == 0:
                    best_ask = price
                ask_rows.append(LadderRow(price, 0.0, _ladder_qty_to_double(level.ask_qty, market)))
                if len(ask_rows) >= MAX_LADDER_ROWS:
                    break
        for price in state.combined_levels.reversed_keys():
            level = state.combined_levels[price]
            if level.bid_qty > 0:
                if best_bid == 0:
                    best_bid = price
                bid_rows.append(LadderRow(price, _ladder_qty_to_double(level.bid_qty, market), 0.0))
                if len(bid_rows) >= MAX_LADDER_ROWS:
                    break
        has_mid = best_bid != 0 and best_ask != 0
        base = self._build_empty_symbol_view(feed_id)
        return SymbolView(
            feed_id=feed_id,
            symbol=base.symbol,
            px_exponent=base.px_exponent,
            size_decimals=base.size_decimals,
            has_symbology=base.has_symbology,
            seq=state.highest_seq,
            unix_ns=state.highest_unix_ns,
            synced=state.maker_synced and state.taker_synced,
            has_mid=has_mid,
            best_bid_price=best_bid,
            best_ask_price=best_ask,
            has_funding_index=state.has_funding_index,
            funding_index_e8=state.funding_index_e8,
            asks=tuple(ask_rows),
            bids=tuple(bid_rows),
        )

    def _apply_funding_index(self, market_id: int, funding_index_e8: int) -> bool:
        state = self.book_states.get(market_id)
        if state is not None:
            state.has_funding_index = True
            state.funding_index_e8 = funding_index_e8
            self.books[market_id] = self._build_symbol_view(market_id)
            return True
        return False

    def _local_order_reject_notice(self, message: LocalOrderRejectMessage) -> str:
        parts = ["order rejected"]
        if message.external_order_id is not None:
            parts.append(f"oid={message.external_order_id}")
        if message.client_instruction_id is not None:
            parts.append(f"cid={message.client_instruction_id}")
        if message.reason != "":
            parts.append(f"reason={message.reason}")
        return " ".join(parts)

    def _log(self, line: str) -> None:
        self._append_bounded(self.log_lines, line, MAX_LOG_LINES)

    @staticmethod
    def _append_bounded(lines: list[str], line: str, max_lines: int) -> None:
        lines.append(line)
        if len(lines) > max_lines:
            del lines[: len(lines) - max_lines]


def _format_qty(qty: int, market: MarketInfo | None) -> str:
    if market is None:
        return str(qty)
    return _format_scaled(qty, -market.size_decimals)


def _ladder_qty_to_double(qty: int, market: MarketInfo | None) -> float:
    if market is None:
        return float(qty)
    return float(qty) / float(10**market.size_decimals)


def _format_price(price: int, market: MarketInfo | None) -> str:
    if market is None:
        return str(price)
    return _format_scaled(price, market.px_exponent)


def _format_scaled(value: int, exponent: int) -> str:
    if exponent >= 0:
        return str(value * (10**exponent))
    scale = 10 ** (-exponent)
    whole = value // scale
    frac = value % scale
    return f"{whole}.{frac:0{-exponent}d}".rstrip("0").rstrip(".")


def _account_snapshot_error_payload(message: AccountSnapshotErrorMessage) -> str:
    payload = {"type": "AccountSnapshotError", "message": message.message}
    if message.wallet_pubkey != "":
        payload["wallet_pubkey"] = message.wallet_pubkey
    return json.dumps(payload, separators=(",", ":"))


def _truncate_notice(text: str) -> str:
    if len(text) <= 120:
        return text
    return text[:117] + "..."


def _select_leg_book(state: SymbolBookState, book_type: BookType) -> SortedIntMap[BookLevel]:
    if book_type == BookType.TAKER:
        return state.taker_book
    return state.maker_book


def _store_book_update(state: SymbolBookState, book_type: BookType, update: BookUpdate) -> None:
    leg_book = _select_leg_book(state, book_type)
    if update.kind == BookUpdateKind.DELTA_LEVELS:
        _apply_leg_level_changes(state, leg_book, update.levels)
    else:
        _apply_leg_full_book(state, leg_book, update.levels)


def _clear_book_update(state: SymbolBookState, book_type: BookType) -> None:
    leg_book = _select_leg_book(state, book_type)
    _apply_leg_full_book(state, leg_book, ())


def _apply_leg_full_book(
    state: SymbolBookState,
    leg_book: SortedIntMap[BookLevel],
    next_levels: tuple[BookLevel, ...],
) -> None:
    prev_price = leg_book.last_key()
    next_index = 0
    while prev_price is not None or next_index < len(next_levels):
        have_prev = prev_price is not None
        have_next = next_index < len(next_levels)
        if have_prev and (not have_next or _checked_price(prev_price) > next_levels[next_index].price):
            price = _checked_price(prev_price)
            previous = leg_book[price]
            _apply_combined_level_delta(state, previous.price, previous.bid_qty, previous.ask_qty, 0, 0)
            leg_book.pop(price)
            prev_price = leg_book.lower_key(price)
        elif have_next and (not have_prev or next_levels[next_index].price > _checked_price(prev_price)):
            next_level = next_levels[next_index]
            _apply_combined_level_delta(
                state,
                next_level.price,
                0,
                0,
                next_level.bid_qty,
                next_level.ask_qty,
            )
            leg_book[next_level.price] = next_level
            next_index += 1
        else:
            price = _checked_price(prev_price)
            next_level = next_levels[next_index]
            previous = leg_book[next_level.price]
            _apply_combined_level_delta(
                state,
                previous.price,
                previous.bid_qty,
                previous.ask_qty,
                next_level.bid_qty,
                next_level.ask_qty,
            )
            leg_book[next_level.price] = next_level
            prev_price = leg_book.lower_key(price)
            next_index += 1


def _checked_price(price: int | None) -> int:
    if price is None:
        raise RuntimeError("missing previous book price")
    return price


def _apply_leg_level_changes(
    state: SymbolBookState,
    leg_book: SortedIntMap[BookLevel],
    changed_levels: tuple[BookLevel, ...],
) -> None:
    for level in changed_levels:
        previous = leg_book.get_optional(level.price)
        old_bid_qty = 0 if previous is None else previous.bid_qty
        old_ask_qty = 0 if previous is None else previous.ask_qty
        _apply_combined_level_delta(
            state,
            level.price,
            old_bid_qty,
            old_ask_qty,
            level.bid_qty,
            level.ask_qty,
        )
        if level.bid_qty == 0 and level.ask_qty == 0:
            leg_book.pop(level.price, None)
        else:
            leg_book[level.price] = level


def _apply_combined_level_delta(
    state: SymbolBookState,
    price: int,
    old_bid_qty: int,
    old_ask_qty: int,
    new_bid_qty: int,
    new_ask_qty: int,
) -> None:
    if old_bid_qty == new_bid_qty and old_ask_qty == new_ask_qty:
        return
    current = state.combined_levels.get_optional(price)
    current_bid_qty = 0 if current is None else current.bid_qty
    current_ask_qty = 0 if current is None else current.ask_qty
    next_bid = (
        current_bid_qty + new_bid_qty - old_bid_qty
        if new_bid_qty >= old_bid_qty
        else current_bid_qty - (old_bid_qty - new_bid_qty)
    )
    next_ask = (
        current_ask_qty + new_ask_qty - old_ask_qty
        if new_ask_qty >= old_ask_qty
        else current_ask_qty - (old_ask_qty - new_ask_qty)
    )
    if next_bid == 0 and next_ask == 0:
        if price in state.combined_levels:
            state.combined_levels.pop(price)
    else:
        state.combined_levels[price] = BookLevel(price, next_bid, next_ask)
