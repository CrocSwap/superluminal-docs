from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from dfba_client.messages import (
    BookDeltaMessage,
    BookLevelMessage,
    BookSnapshotMessage,
    StreamResetMessage,
)
from dfba_client.sorted_map import SortedIntMap
from dfba_client.types import BookType

MAX_BUFFERED_DELTAS = 512


class WireKind(Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"
    RESET = "reset"


class SyncActionType(Enum):
    NOOP = "noop"
    EMIT = "emit"
    GAP = "gap"


@dataclass(frozen=True, slots=True)
class WireLevel:
    price: int
    qty: int


@dataclass(frozen=True, slots=True)
class WireMessage:
    kind: WireKind
    book_type: BookType
    feed_id: int
    seq: int = 0
    book_cursor: int = 0
    prev_book_cursor: int = 0
    unix_ns: int = 0
    mark_price: int = 0
    bids: tuple[WireLevel, ...] = ()
    asks: tuple[WireLevel, ...] = ()


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: int
    bid_qty: int
    ask_qty: int


class BookUpdateKind(Enum):
    FULL_BOOK = "full_book"
    DELTA_LEVELS = "delta_levels"


@dataclass(frozen=True, slots=True)
class BookUpdate:
    kind: BookUpdateKind
    levels: tuple[BookLevel, ...]
    synced: bool


@dataclass(frozen=True, slots=True)
class SyncAction:
    type: SyncActionType = SyncActionType.NOOP
    update: BookUpdate | None = None


@dataclass(slots=True)
class _BufferedDelta:
    prev_cursor: int
    cursor: int
    msg: WireMessage


@dataclass(slots=True)
class BookSyncEngine:
    book_state: SortedIntMap[BookLevel] = field(default_factory=SortedIntMap)
    book_cursor: int = 0
    book_synced: bool = False
    buffered_deltas: list[_BufferedDelta] = field(default_factory=list)
    newer_deltas_scratch: list[_BufferedDelta] = field(default_factory=list)
    changed_prices_scratch: list[int] = field(default_factory=list)

    def handle_message(self, msg: WireMessage) -> SyncAction:
        if msg.kind == WireKind.SNAPSHOT:
            return self._handle_snapshot(msg)
        if msg.kind == WireKind.DELTA:
            return self._handle_delta(msg)
        if msg.kind == WireKind.RESET:
            return self._handle_reset()
        return SyncAction()

    def _handle_snapshot(self, msg: WireMessage) -> SyncAction:
        self._apply_snapshot(msg)
        sync = self._try_sync_from_snapshot()
        if sync.type != SyncActionType.NOOP:
            return sync
        self.buffered_deltas.clear()
        self.book_synced = True
        return self._emit_book()

    def _handle_delta(self, msg: WireMessage) -> SyncAction:
        self._buffer_delta(msg)
        if not self.book_synced:
            return SyncAction()
        if self.book_cursor == 0 or msg.prev_book_cursor == 0 or msg.prev_book_cursor != self.book_cursor:
            self.book_state.clear()
            self.book_cursor = 0
            self.book_synced = False
            return self._gap_action()
        self._apply_delta(msg)
        self._prune_buffered_deltas_up_to(self.book_cursor)
        return self._emit_delta_levels(msg)

    def _handle_reset(self) -> SyncAction:
        self.book_state.clear()
        self.book_cursor = 0
        self.book_synced = False
        self.buffered_deltas.clear()
        return self._gap_action()

    def _apply_snapshot(self, msg: WireMessage) -> None:
        self.book_state.clear()
        self._apply_bids(msg.bids)
        self._apply_asks(msg.asks)
        self.book_cursor = msg.book_cursor

    def _apply_delta(self, msg: WireMessage) -> None:
        self._apply_bids(msg.bids)
        self._apply_asks(msg.asks)
        if msg.book_cursor != 0:
            self.book_cursor = msg.book_cursor

    def _apply_bids(self, bids: tuple[WireLevel, ...]) -> None:
        for level in bids:
            current = self.book_state.get_optional(level.price)
            ask_qty = 0 if current is None else current.ask_qty
            updated = BookLevel(level.price, level.qty, ask_qty)
            self._store_level(updated)

    def _apply_asks(self, asks: tuple[WireLevel, ...]) -> None:
        for level in asks:
            current = self.book_state.get_optional(level.price)
            bid_qty = 0 if current is None else current.bid_qty
            updated = BookLevel(level.price, bid_qty, level.qty)
            self._store_level(updated)

    def _store_level(self, level: BookLevel) -> None:
        if level.bid_qty == 0 and level.ask_qty == 0:
            self.book_state.pop(level.price, None)
        else:
            self.book_state[level.price] = level

    def _buffer_delta(self, msg: WireMessage) -> None:
        if msg.prev_book_cursor == 0 or msg.book_cursor == 0:
            return
        self.buffered_deltas.append(_BufferedDelta(msg.prev_book_cursor, msg.book_cursor, msg))
        if len(self.buffered_deltas) > MAX_BUFFERED_DELTAS:
            del self.buffered_deltas[: len(self.buffered_deltas) - MAX_BUFFERED_DELTAS]

    def _prune_buffered_deltas_up_to(self, cursor: int) -> None:
        if cursor != 0:
            write_index = 0
            for read_index, delta in enumerate(self.buffered_deltas):
                if delta.cursor > cursor:
                    if write_index != read_index:
                        self.buffered_deltas[write_index] = delta
                    write_index += 1
            del self.buffered_deltas[write_index:]

    def _try_sync_from_snapshot(self) -> SyncAction:
        base_cursor = self.book_cursor
        if base_cursor == 0:
            return SyncAction()
        newer = self.newer_deltas_scratch
        newer.clear()
        for delta in self.buffered_deltas:
            if delta.cursor > base_cursor:
                newer.append(delta)
        if len(newer) == 0:
            self.book_synced = True
            self._prune_buffered_deltas_up_to(self.book_cursor)
            return self._emit_book()
        newer.sort(key=lambda delta: delta.cursor)
        start_index = -1
        for index, delta in enumerate(newer):
            if delta.prev_cursor == base_cursor:
                start_index = index
                break
        if start_index < 0:
            newer.clear()
            return SyncAction()
        cursor = base_cursor
        for index in range(start_index, len(newer)):
            delta = newer[index]
            if delta.prev_cursor != cursor:
                newer.clear()
                return SyncAction()
            self._apply_delta(delta.msg)
            cursor = self.book_cursor
        self.book_synced = True
        newer.clear()
        self._prune_buffered_deltas_up_to(self.book_cursor)
        return self._emit_book()

    def _emit_book(self) -> SyncAction:
        levels = tuple(self.book_state[price] for price in self.book_state.reversed_keys())
        return SyncAction(SyncActionType.EMIT, BookUpdate(BookUpdateKind.FULL_BOOK, levels, self.book_synced))

    def _emit_delta_levels(self, msg: WireMessage) -> SyncAction:
        prices = self.changed_prices_scratch
        prices.clear()
        for level in msg.bids:
            prices.append(level.price)
        for level in msg.asks:
            prices.append(level.price)
        prices.sort(reverse=True)
        write_index = 0
        previous_price: int | None = None
        for price in prices:
            if previous_price != price:
                prices[write_index] = price
                write_index += 1
                previous_price = price
        del prices[write_index:]
        levels_list: list[BookLevel] = []
        for price in prices:
            current = self.book_state.get_optional(price)
            if current is None:
                levels_list.append(BookLevel(price, 0, 0))
            else:
                levels_list.append(current)
        levels = tuple(levels_list)
        return SyncAction(
            SyncActionType.EMIT,
            BookUpdate(BookUpdateKind.DELTA_LEVELS, levels, self.book_synced),
        )

    @staticmethod
    def _gap_action() -> SyncAction:
        return SyncAction(SyncActionType.GAP)


def wire_from_gateway_message(
    msg: BookSnapshotMessage | BookDeltaMessage | StreamResetMessage,
) -> WireMessage:
    if isinstance(msg, StreamResetMessage):
        return WireMessage(WireKind.RESET, msg.book_type, msg.feed_id)
    if isinstance(msg, BookSnapshotMessage):
        return WireMessage(
            kind=WireKind.SNAPSHOT,
            book_type=msg.book_type,
            feed_id=msg.feed_id,
            seq=msg.seq,
            book_cursor=msg.book_cursor,
            unix_ns=msg.matcher_unix_ns,
            bids=_levels_from_msg(msg.bids),
            asks=_levels_from_msg(msg.asks),
        )
    return WireMessage(
        kind=WireKind.DELTA,
        book_type=msg.book_type,
        feed_id=msg.feed_id,
        seq=msg.seq,
        book_cursor=msg.book_cursor,
        prev_book_cursor=msg.prev_book_cursor,
        unix_ns=msg.matcher_unix_ns,
        mark_price=msg.mark_price,
        bids=_levels_from_msg(msg.bids),
        asks=_levels_from_msg(msg.asks),
    )


def _levels_from_msg(levels: tuple[BookLevelMessage, ...]) -> tuple[WireLevel, ...]:
    return tuple(WireLevel(price=level.price, qty=level.qty) for level in levels)
