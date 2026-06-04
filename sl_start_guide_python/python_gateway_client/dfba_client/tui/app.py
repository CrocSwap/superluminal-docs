from __future__ import annotations

import argparse
import asyncio
import contextlib
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from rich import box
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dfba_client.client import GatewayClient
from dfba_client.transport import WebSocketTransport
from dfba_client.types import (
    INT32_MAX,
    INT32_MIN,
    UINT64_MAX,
    ClientConfig,
    FillRow,
    MarketInfo,
    OrderDraft,
    OrderMode,
    OrderRole,
    PositionRow,
    SymbolView,
    UiSnapshot,
    WorkingOrderRow,
)


@dataclass(slots=True)
class SnapshotStore:
    snapshot: UiSnapshot


@dataclass(frozen=True, slots=True)
class SubmitOrderIntent:
    draft: OrderDraft


@dataclass(frozen=True, slots=True)
class CancelOrderIntent:
    subaccount_id: int
    external_order_id: int


@dataclass(frozen=True, slots=True)
class StopIntent:
    pass


Intent = SubmitOrderIntent | CancelOrderIntent | StopIntent
MAX_LADDER_ROWS = 24


class SocketWorker:
    def __init__(
        self,
        config: ClientConfig,
        snapshot_queue: queue.Queue[UiSnapshot],
        intent_queue: queue.Queue[Intent],
    ) -> None:
        self._config = config
        self._snapshot_queue = snapshot_queue
        self._intent_queue = intent_queue
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dfba-socket-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._intent_queue.put_nowait(StopIntent())
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        client = GatewayClient(self._config)
        try:
            await client.initialize()
        except Exception as exc:
            self._publish(
                UiSnapshot(
                    ws_url=self._config.ws_url,
                    connection_status="failed",
                    last_error=str(exc),
                )
            )
            return
        transport = WebSocketTransport(self._config.ws_url, client)
        transport.snapshot_callback = self._publish
        transport_task = asyncio.create_task(transport.run_forever())
        intent_task = asyncio.create_task(self._pump_intents(client))
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.1)
        finally:
            transport_task.cancel()
            intent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await transport_task
            with contextlib.suppress(asyncio.CancelledError):
                await intent_task

    async def _pump_intents(self, client: GatewayClient) -> None:
        while True:
            intent = await asyncio.to_thread(self._intent_queue.get)
            if isinstance(intent, StopIntent) or self._stop.is_set():
                return
            if isinstance(intent, SubmitOrderIntent):
                try:
                    await client.submit_order(intent.draft)
                    self._publish(client.snapshot())
                except Exception as exc:
                    if not client.has_websocket_error():
                        client.record_error(str(exc))
                    self._publish(client.snapshot())
            elif isinstance(intent, CancelOrderIntent):
                try:
                    await client.cancel_order(intent.subaccount_id, intent.external_order_id)
                    self._publish(client.snapshot())
                except Exception as exc:
                    if not client.has_websocket_error():
                        client.record_error(str(exc))
                    self._publish(client.snapshot())

    def _publish(self, snapshot: UiSnapshot) -> None:
        if self._snapshot_queue.full():
            with contextlib.suppress(queue.Empty):
                self._snapshot_queue.get_nowait()
        with contextlib.suppress(queue.Full):
            self._snapshot_queue.put_nowait(snapshot)


def main() -> None:
    args = _parse_args()
    config = ClientConfig(
        ws_url=args.ws,
        keypair_path=args.keypair,
        feed_ids=tuple(args.feed_id),
        subaccount_id=args.subaccount,
        partition_id=int(args.partition_id, 0),
        auth_expiry_seconds=args.auth_expiry_seconds,
    )
    _run_tui(config)


def _run_tui(config: ClientConfig) -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.reactive import reactive
        from textual.widgets import Button, Footer, Header, Input, Log, RadioSet, Static

    except ImportError as exc:
        raise RuntimeError("textual is required for SLPythonClient") from exc

    store = SnapshotStore(UiSnapshot(ws_url=config.ws_url))
    snapshot_queue: queue.Queue[UiSnapshot] = queue.Queue(maxsize=2)
    intent_queue: queue.Queue[Intent] = queue.Queue(maxsize=64)
    worker = SocketWorker(config, snapshot_queue, intent_queue)

    if TYPE_CHECKING:
        from textual.app import App as _TextualApp
        from textual.widgets import Static as _TextualStatic

        StatusPanelBase: TypeAlias = _TextualStatic
        DfbaAppBase: TypeAlias = _TextualApp[None]
    else:
        StatusPanelBase: Any = Static
        DfbaAppBase: Any = App[None]

    class StatusPanel(StatusPanelBase):
        snapshot: reactive[UiSnapshot] = reactive(store.snapshot)

        def render(self) -> str:
            snap = self.snapshot
            return (
                "api_client ladder\n"
                f"wallet: {snap.wallet_pubkey}\n"
                f"ws:     {snap.ws_url}\n"
                f"status: {snap.connection_status}\n"
                f"auth:   {snap.auth_status}\n"
                f"acct:   {snap.account_status}\n"
                f"gw-rtt: {_format_avg_latency(snap.gateway_rtt_avg_ns, snap.gateway_rtt_samples)}\n"
                f"seq-match latency: "
                f"{_format_avg_latency(snap.seq_match_latency_avg_ns, snap.seq_match_latency_samples)}\n"
                f"last:   {snap.last_notice}\n"
                f"error:  {snap.last_error}"
            )

    class DfbaApp(DfbaAppBase):
        TITLE = "SLPythonClient"
        CSS = """
        Screen { layout: vertical; }
        #top { height: 3fr; }
        #books-pane { width: 1fr; }
        #ticket { width: 42; }
        #account { height: 2fr; }
        #positions-pane { width: 1fr; border: solid white; }
        #working-pane { width: 1fr; border: solid white; }
        #fills-pane { width: 1fr; border: solid white; }
        #bottom { height: 1fr; }
        #trades-pane { width: 1fr; border: solid white; }
        #log-pane { width: 1fr; border: solid white; }
        #ladders { height: 1fr; }
        .ladder { width: 1fr; height: 1fr; }
        .ticket-radio { layout: horizontal; height: auto; }
        .ticket-radio RadioButton { width: auto; margin-right: 1; }
        Log {
            height: 1fr;
            background: transparent;
            overflow-x: hidden;
            overflow-y: auto;
            scrollbar-background: transparent;
            scrollbar-background-hover: transparent;
            scrollbar-background-active: transparent;
            scrollbar-corner-color: transparent;
            scrollbar-size-horizontal: 0;
            scrollbar-size-vertical: 1;
        }
        Log:focus { background-tint: transparent; }
        Static { height: auto; }
        #working-orders {
            height: 1fr;
            overflow-x: hidden;
            overflow-y: auto;
            scrollbar-background: transparent;
            scrollbar-background-hover: transparent;
            scrollbar-background-active: transparent;
            scrollbar-corner-color: transparent;
            scrollbar-size-vertical: 1;
        }
        .working-order-row {
            height: auto;
            width: 1fr;
        }
        .working-order-label {
            width: 1fr;
            overflow-x: hidden;
        }
        .working-order-cancel {
            width: 10;
            min-width: 10;
        }
        """

        def __init__(self) -> None:
            super().__init__()
            self.status = StatusPanel()
            self.account_header = Static("last_seq=0", id="account-header")
            self.books: Any = Static(id="ladders")
            self.positions: Any = Log(id="positions", auto_scroll=True)
            self.working_orders_container: Any = Vertical(id="working-orders")
            self.fills: Any = Log(id="fills", auto_scroll=True)
            self.trades: Any = Log(id="trades", auto_scroll=True)
            self.logs: Any = Log(id="logs", auto_scroll=True)
            self._working_order_intents: dict[str, CancelOrderIntent] = {}
            self._working_order_rows: tuple[tuple[str, str], ...] = ()
            self._book_rows: tuple[str, ...] = ()
            self._position_lines: tuple[str, ...] = ()
            self._fill_lines: tuple[str, ...] = ()
            self._trade_lines: tuple[str, ...] = ()
            self._log_lines: tuple[str, ...] = ()
            self._symbol_labels = _symbol_radio_labels(config, store.snapshot)
            self.symbol_index = 0
            self.side_index = 0
            self.role_index = 0
            self.order_mode_index = 0
            self.symbol: Any = RadioSet(
                *self._symbol_labels,
                id="symbol-options",
                classes="ticket-radio",
                compact=True,
            )
            self.qty = Input(placeholder="qty", id="qty")
            self.price_label = Static("Price")
            self.price = Input(placeholder="price", id="price")
            self.delta_label = Static("Delta")
            self.delta = Input(placeholder="delta", id="delta")
            self.side: Any = RadioSet("Buy", "Sell", id="side-options", classes="ticket-radio", compact=True)
            self.role: Any = RadioSet("Maker", "Taker", id="role-options", classes="ticket-radio", compact=True)
            self.mode: Any = RadioSet(
                "GTC",
                "Mark-to-mid",
                "Market",
                id="mode-options",
                classes="ticket-radio",
                compact=True,
            )
            self.submit = Button("Place Order", id="submit-order")
            self.preview = Static("", id="preview")

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="top"):
                with Vertical(id="books-pane"):
                    yield self.status
                    yield self.books
                with Vertical(id="ticket"):
                    yield Static("Order Ticket")
                    yield Static("Symbol")
                    yield self.symbol
                    yield Static("Side")
                    yield self.side
                    yield Static("Role")
                    yield self.role
                    yield Static("Order")
                    yield self.mode
                    yield Static("Qty")
                    yield self.qty
                    yield self.price_label
                    yield self.price
                    yield self.delta_label
                    yield self.delta
                    yield Static("Preview")
                    yield self.preview
                    yield self.submit
            yield self.account_header
            with Horizontal(id="account"):
                with Vertical(id="positions-pane"):
                    yield Static("Positions")
                    yield self.positions
                with Vertical(id="working-pane"):
                    yield Static("Working Orders")
                    yield self.working_orders_container
                with Vertical(id="fills-pane"):
                    yield Static("Recent Fills")
                    yield self.fills
            with Horizontal(id="bottom"):
                with Vertical(id="trades-pane"):
                    yield Static("Trades")
                    yield self.trades
                with Vertical(id="log-pane"):
                    yield Static("Log")
                    yield self.logs
            yield Footer()

        def on_mount(self) -> None:
            self.qty.value = "0.001"
            _select_radio_index(self.symbol, self.symbol_index)
            _select_radio_index(self.side, self.side_index)
            _select_radio_index(self.role, self.role_index)
            _select_radio_index(self.mode, self.order_mode_index)
            self._sync_order_controls()
            self.set_interval(0.1, self.refresh_snapshot)

        def selected_symbol(self) -> int:
            if len(config.feed_ids) == 0:
                return 0
            if self.symbol_index < 0:
                self.symbol_index = 0
            last = len(config.feed_ids) - 1
            if self.symbol_index > last:
                self.symbol_index = last
            return config.feed_ids[self.symbol_index]

        def selected_mode(self) -> OrderMode:
            if self.order_mode_index == int(OrderMode.MARK_TO_MID):
                return OrderMode.MARK_TO_MID
            if self.order_mode_index == int(OrderMode.MARKET):
                return OrderMode.MARKET
            return OrderMode.GTC_LIMIT

        def selected_role(self) -> OrderRole:
            return OrderRole.TAKER if self.role_index == int(OrderRole.TAKER) else OrderRole.MAKER

        def selected_is_sell(self) -> bool:
            return self.side_index != 0

        def _sync_order_controls(self) -> None:
            show_price = self.selected_mode() == OrderMode.GTC_LIMIT
            show_delta = self.selected_mode() == OrderMode.MARK_TO_MID
            self.price_label.display = show_price
            self.price.display = show_price
            self.delta_label.display = show_delta
            self.delta.display = show_delta

        def on_radio_set_changed(self, event: Any) -> None:
            if event.radio_set.id == "symbol-options":
                self.symbol_index = event.radio_set.pressed_index
            elif event.radio_set.id == "side-options":
                self.side_index = event.radio_set.pressed_index
            elif event.radio_set.id == "role-options":
                self.role_index = event.radio_set.pressed_index
            elif event.radio_set.id == "mode-options":
                self.order_mode_index = event.radio_set.pressed_index
            else:
                return
            self._sync_order_controls()
            self._refresh_books(store.snapshot)
            self._refresh_preview(store.snapshot)

        def refresh_snapshot(self) -> None:
            updated = False
            while True:
                try:
                    store.snapshot = snapshot_queue.get_nowait()
                    updated = True
                except queue.Empty:
                    break
            if not updated:
                return
            snap = store.snapshot
            self.status.snapshot = snap
            self.account_header.update(f"Account last_seq={snap.account_last_seq}")
            self._refresh_symbol_options(snap)
            self._refresh_books(snap)
            self._refresh_positions(snap)
            self._refresh_working_orders(snap)
            self._refresh_fills(snap)
            self._refresh_trades(snap)
            self._refresh_logs(snap)
            self._refresh_preview(snap)

        def _refresh_symbol_options(self, snap: UiSnapshot) -> None:
            next_labels = _symbol_radio_labels(config, snap)
            if next_labels == self._symbol_labels:
                return
            self._symbol_labels = next_labels
            buttons = list(self.symbol.query("RadioButton"))
            for index, label in enumerate(next_labels):
                if index >= len(buttons):
                    return
                buttons[index].label = label

        def _refresh_books(self, snap: UiSnapshot) -> None:
            next_signature = _books_signature(snap)
            if next_signature == self._book_rows:
                return
            self._book_rows = next_signature
            self.books.update(
                Columns(
                    tuple(_book_panel(book) for book in _visible_books(snap)),
                    expand=True,
                    equal=True,
                )
            )

        def _refresh_positions(self, snap: UiSnapshot) -> None:
            lines: list[str] = []
            if not snap.account_stream_started:
                lines.append("account stream not subscribed")
            elif not snap.has_account_snapshot:
                lines.append("waiting for account snapshot...")
            else:
                for subaccount in snap.subaccounts:
                    lines.append(
                        f"subaccount {subaccount.subaccount_id} "
                        f"collateral={_format_usd_micro(subaccount.collateral_balance)}"
                    )
                    if len(subaccount.positions) == 0:
                        lines.append("  no positions")
                        continue
                    for position in subaccount.positions:
                        market = _find_market(snap, position.symbol)
                        book = _find_book(snap, position.symbol)
                        lines.append(
                            "  sym="
                            + _format_symbol_label(position.symbol, market)
                            + " net="
                            + _format_signed_atomic_qty(position.net_qty, market)
                            + " cb="
                            + _format_usd_micro(position.cost_basis)
                            + " fee="
                            + _format_usd_micro(position.pending_fees)
                            + " funding_accrued="
                            + _format_usd_micro(position.funding_accrued)
                            + " pnl="
                            + _format_position_pnl(position, book, market)
                        )
                if len(lines) == 0:
                    lines.append("no positions")
            self._sync_log_lines(self.positions, lines, "_position_lines")

        def _refresh_working_orders(self, snap: UiSnapshot) -> None:
            if not snap.account_stream_started:
                if self._working_order_rows == (("", "account stream not subscribed"),):
                    return
                self.working_orders_container.remove_children()
                self._working_order_rows = (("", "account stream not subscribed"),)
                self._working_order_intents.clear()
                self.working_orders_container.mount(Static("account stream not subscribed"))
                return
            if not snap.has_account_snapshot:
                if self._working_order_rows == (("", "waiting for account snapshot..."),):
                    return
                self.working_orders_container.remove_children()
                self._working_order_rows = (("", "waiting for account snapshot..."),)
                self._working_order_intents.clear()
                self.working_orders_container.mount(Static("waiting for account snapshot..."))
                return
            next_rows: list[tuple[str, str]] = []
            next_intents: dict[str, CancelOrderIntent] = {}
            for subaccount in snap.subaccounts:
                for order in subaccount.working_orders:
                    row_key = _working_order_key(subaccount.subaccount_id, order)
                    next_rows.append((row_key, _working_order_label(snap, subaccount.subaccount_id, order)))
                    next_intents[row_key] = CancelOrderIntent(
                        subaccount.subaccount_id,
                        order.external_order_id,
                    )
            if len(next_rows) == 0:
                next_rows.append(("", "no working orders"))
            next_signature = tuple(next_rows)
            if next_signature == self._working_order_rows:
                return
            self.working_orders_container.remove_children()
            self._working_order_rows = next_signature
            self._working_order_intents = next_intents
            for row_key, label in next_rows:
                if row_key == "":
                    self.working_orders_container.mount(Static(label))
                    continue
                self.working_orders_container.mount(
                    Horizontal(
                        Static(label, classes="working-order-label"),
                        Button(
                            "Cancel",
                            id=_cancel_button_id(row_key),
                            classes="working-order-cancel",
                            compact=True,
                        ),
                        classes="working-order-row",
                    )
                )

        def _refresh_fills(self, snap: UiSnapshot) -> None:
            lines: list[str] = []
            if len(snap.recent_fills) == 0:
                lines.append("no fills yet")
            else:
                for fill in snap.recent_fills:
                    market = _find_market(snap, fill.symbol)
                    lines.append(_fill_summary(fill, market))
                    lines.append("  " + _fill_status(fill, market))
            self._sync_log_lines(self.fills, lines, "_fill_lines")

        def _refresh_trades(self, snap: UiSnapshot) -> None:
            lines = ["no trades yet"] if len(snap.trade_lines) == 0 else list(snap.trade_lines)
            self._sync_log_lines(self.trades, lines, "_trade_lines")

        def _refresh_logs(self, snap: UiSnapshot) -> None:
            lines = ["no log events yet"] if len(snap.log_lines) == 0 else list(snap.log_lines)
            self._sync_log_lines(self.logs, lines, "_log_lines")

        def _sync_log_lines(
            self,
            widget: Any,
            lines: list[str],
            attr_name: str,
        ) -> None:
            next_lines = lines
            next_signature = tuple(next_lines)
            if getattr(self, attr_name) == next_signature:
                return
            setattr(self, attr_name, next_signature)
            follow_tail = bool(getattr(widget, "is_vertical_scroll_end", True))
            widget.clear()
            for line in next_lines:
                widget.write_line(line, scroll_end=follow_tail)

        def _refresh_preview(self, snap: UiSnapshot) -> None:
            self.preview.update(_order_preview(self, snap))

        async def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "submit-order":
                try:
                    draft = _build_order_draft(self, store.snapshot)
                    market = _find_market(store.snapshot, draft.symbol)
                    if draft.mode == OrderMode.GTC_LIMIT:
                        self.price.value = _format_price(draft.price, market)
                    intent_queue.put_nowait(SubmitOrderIntent(draft))
                except Exception as exc:
                    self._show_local_error(str(exc))
            elif event.button.id is not None and event.button.id.startswith("cancel_"):
                row_key = _row_key_from_cancel_button_id(event.button.id)
                intent = self._working_order_intents.get(row_key)
                if intent is None:
                    self._show_local_error("working order not found")
                    return
                try:
                    intent_queue.put_nowait(intent)
                except queue.Full as exc:
                    self._show_local_error(str(exc))

        def _show_local_error(self, error: str) -> None:
            store.snapshot = _snapshot_with_error(store.snapshot, error)
            self.status.snapshot = store.snapshot

    app = DfbaApp()
    worker.start()
    try:
        app.run()
    finally:
        worker.stop()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ws", default="ws://127.0.0.1:8080/ws")
    parser.add_argument("--keypair", required=True)
    parser.add_argument("--feed-id", type=int, action="append", required=True)
    parser.add_argument("--subaccount", type=int, required=True)
    parser.add_argument("--partition-id", default="0x01")
    parser.add_argument("--auth-expiry-seconds", type=int, default=0)
    args = parser.parse_args()
    return args


def _snapshot_with_error(snapshot: UiSnapshot, error: str) -> UiSnapshot:
    return UiSnapshot(
        wallet_pubkey=snapshot.wallet_pubkey,
        ws_url=snapshot.ws_url,
        connection_status=snapshot.connection_status,
        auth_status=snapshot.auth_status,
        account_status=snapshot.account_status,
        last_notice=snapshot.last_notice,
        last_error=error,
        books=snapshot.books,
        gateway_rtt_avg_ns=snapshot.gateway_rtt_avg_ns,
        gateway_rtt_samples=snapshot.gateway_rtt_samples,
        account_stream_started=snapshot.account_stream_started,
        has_account_snapshot=snapshot.has_account_snapshot,
        account_last_seq=snapshot.account_last_seq,
        seq_match_latency_avg_ns=snapshot.seq_match_latency_avg_ns,
        seq_match_latency_samples=snapshot.seq_match_latency_samples,
        subaccounts=snapshot.subaccounts,
        recent_fills=snapshot.recent_fills,
        trade_lines=snapshot.trade_lines,
        log_lines=snapshot.log_lines,
    )


def _working_order_key(subaccount_id: int, order: WorkingOrderRow) -> str:
    return f"{subaccount_id}:{order.external_order_id}"


def _cancel_button_id(row_key: str) -> str:
    return "cancel_" + row_key.replace(":", "_")


def _row_key_from_cancel_button_id(button_id: str) -> str:
    parts = button_id.removeprefix("cancel_").split("_", 1)
    if len(parts) != 2:
        return ""
    return f"{parts[0]}:{parts[1]}"


def _working_order_label(snap: UiSnapshot, subaccount_id: int, order: WorkingOrderRow) -> str:
    market = _find_market(snap, order.symbol)
    return (
        f"sub={subaccount_id} oid={order.external_order_id} "
        f"sym={_format_symbol_label(order.symbol, market)} {order.side} "
        f"rem={_format_atomic_qty(order.remaining_qty, market)}/"
        f"{_format_atomic_qty(order.original_qty, market)} px={_format_price(order.price, market)}"
    )


def _book_title(book: SymbolView) -> str:
    symbol = "" if book.symbol == "" else f" {book.symbol}"
    return f"Feed {book.feed_id}{symbol}"


def _books_signature(snap: UiSnapshot) -> tuple[str, ...]:
    lines: list[str] = []
    for book in _visible_books(snap):
        lines.append(_book_title(book))
        lines.append(f"seq={book.seq}")
        lines.append(f"sync={'yes' if book.synced else 'no'}")
        lines.append(f"fund_idx={_format_funding_index(book)}")
        for row in reversed(book.asks[-MAX_LADDER_ROWS:]):
            lines.append(f"A:{row.price}:{row.ask_qty}")
        lines.append(f"M:{_mid_label(book)}")
        for row in book.bids[:MAX_LADDER_ROWS]:
            lines.append(f"B:{row.price}:{row.bid_qty}")
    return tuple(lines)


def _book_panel(book: SymbolView) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(justify="right", width=10)
    table.add_column(justify="right", width=12)
    table.add_column(justify="right", width=10)
    if len(book.asks) == 0 and len(book.bids) == 0:
        table.add_row(Text("waiting for book ...", style="dim"), "", "")
    else:
        for row in reversed(book.asks[-MAX_LADDER_ROWS:]):
            table.add_row(
                Text(_format_ladder_qty(row.ask_qty, book.size_decimals), style="bold red"),
                Text(_format_book_price(book, row.price), style="bold red"),
                "",
            )
        table.add_row("", Text(_mid_label(book), style="bold yellow"), "")
        for row in book.bids[:MAX_LADDER_ROWS]:
            table.add_row(
                "",
                Text(_format_book_price(book, row.price), style="bold green"),
                Text(_format_ladder_qty(row.bid_qty, book.size_decimals), style="bold green"),
            )
    return Panel(
        table,
        title=_book_title(book),
        border_style="white",
        box=box.ROUNDED,
    )


def _mid_label(book: SymbolView) -> str:
    if not book.has_mid:
        return "n/a"
    return _format_mid_price(book.best_bid_price, book.best_ask_price, book.px_exponent)


def _format_funding_index(book: SymbolView) -> str:
    if not book.has_funding_index:
        return "n/a"
    return _format_e8(book.funding_index_e8)


def _fill_summary(fill: FillRow, market: MarketInfo | None) -> str:
    return (
        "seq="
        + str(fill.seq)
        + " trade="
        + str(fill.trade_id)
        + " oid="
        + str(fill.external_order_id)
        + " cid="
        + str(fill.client_instruction_id)
        + " sym="
        + _format_symbol_label(fill.symbol, market)
        + " "
        + fill.side
        + " qty="
        + _format_atomic_qty(fill.qty, market)
        + " px="
        + _format_price(fill.price, market)
    )


def _fill_status(fill: FillRow, market: MarketInfo | None) -> str:
    if not fill.settled:
        status = "settle=pending"
    elif not fill.has_settle_delta_ns:
        status = "settle=settled"
    else:
        status = f"settle=settled dt={_format_latency_ns(float(fill.settle_delta_ns))}"
    if fill.has_order_settled_qty:
        status += f" order_settled_qty={_format_atomic_qty(fill.order_settled_qty, market)}"
    return status


def _format_latency_ns(latency_ns: float) -> str:
    if latency_ns >= 1_000_000.0:
        return f"{latency_ns / 1_000_000.0:.2f} ms"
    if latency_ns >= 1_000.0:
        return f"{latency_ns / 1_000.0:.2f} us"
    return f"{latency_ns:.2f} ns"


def _format_avg_latency(avg_ns: float, samples: int) -> str:
    if samples == 0:
        return "n/a"
    return f"{_format_latency_ns(avg_ns)} avg n={samples}"


def _find_book(snap: UiSnapshot, symbol: int) -> SymbolView | None:
    for book in snap.books:
        if book.feed_id == symbol:
            return book
    return None


def _visible_books(snap: UiSnapshot) -> tuple[SymbolView, ...]:
    return snap.books


def _find_market(snap: UiSnapshot, symbol: int) -> MarketInfo | None:
    book = _find_book(snap, symbol)
    if book is None or not book.has_symbology:
        return None
    return MarketInfo(
        feed_id=book.feed_id,
        symbol=book.symbol,
        quote="",
        px_exponent=book.px_exponent,
        size_decimals=book.size_decimals,
    )


def _format_symbol_label(symbol: int, market: MarketInfo | None) -> str:
    if market is None or market.symbol == "":
        return str(symbol)
    return f"{symbol} {market.label()}"


def _symbol_radio_labels(config: ClientConfig, snap: UiSnapshot) -> tuple[str, ...]:
    if len(config.feed_ids) == 0:
        return ("Symbol",)
    labels: list[str] = []
    for feed_id in config.feed_ids:
        book = _find_book(snap, feed_id)
        labels.append(book.symbol if book is not None and book.symbol != "" else f"Feed {feed_id}")
    return tuple(labels)


def _select_radio_index(radio_set: Any, index: int) -> None:
    buttons = list(radio_set.query("RadioButton"))
    if len(buttons) == 0:
        return
    clamped_index = min(max(index, 0), len(buttons) - 1)
    buttons[clamped_index].value = True


def _format_book_price(book: SymbolView, price: int) -> str:
    if not book.has_symbology:
        return str(price)
    return _format_scaled_price(price, book.px_exponent)


def _format_price(price: int, market: MarketInfo | None) -> str:
    if market is None:
        return str(price)
    return _format_scaled_price(price, market.px_exponent)


def _format_mid_price(best_bid_price: int, best_ask_price: int, px_exponent: int) -> str:
    if best_bid_price > UINT64_MAX - best_ask_price:
        return "n/a"
    price_sum = best_bid_price + best_ask_price
    if price_sum % 2 == 0:
        return _format_scaled_price(price_sum // 2, px_exponent)
    if price_sum > UINT64_MAX // 5:
        return "n/a"
    return _format_scaled_price(price_sum * 5, px_exponent - 1)


def _format_scaled_price(price: int, px_exponent: int) -> str:
    digits = str(price)
    if px_exponent >= 0:
        return _trim_decimal_zeros(digits + ("0" * px_exponent), 2)
    decimal_places = -px_exponent
    if len(digits) <= decimal_places:
        digits = ("0" * (decimal_places - len(digits) + 1)) + digits
    point = len(digits) - decimal_places
    return _trim_decimal_zeros(digits[:point] + "." + digits[point:], 2)


def _trim_decimal_zeros(value: str, min_decimal_places: int) -> str:
    dot = value.find(".")
    if dot < 0:
        if min_decimal_places != 0:
            return value + "." + ("0" * min_decimal_places)
        return value
    while len(value) > dot + 1 + min_decimal_places and value.endswith("0"):
        value = value[:-1]
    if value.endswith("."):
        value = value[:-1]
    final_dot = value.find(".")
    decimal_places = 0 if final_dot < 0 else len(value) - final_dot - 1
    if decimal_places < min_decimal_places:
        if final_dot < 0:
            value += "."
        value += "0" * (min_decimal_places - decimal_places)
    return value


def _format_ladder_qty(qty: float, size_decimals: int) -> str:
    return f"{qty:.{size_decimals}f}"


def _format_atomic_qty(qty: int, market: MarketInfo | None) -> str:
    if market is None:
        return str(qty)
    return _format_qty(qty, market.size_decimals)


def _format_signed_atomic_qty(qty: int, market: MarketInfo | None) -> str:
    if market is None:
        return str(qty)
    if qty < 0:
        return "-" + _format_qty(-qty, market.size_decimals)
    return _format_qty(qty, market.size_decimals)


def _format_qty(value: int, size_decimals: int) -> str:
    digits = str(value)
    if size_decimals == 0:
        return digits
    if len(digits) <= size_decimals:
        digits = ("0" * (size_decimals - len(digits) + 1)) + digits
    point = len(digits) - size_decimals
    out = digits[:point] + "." + digits[point:]
    out = out.rstrip("0").rstrip(".")
    return "0" if out == "" else out


def _format_fixed_decimal_i64(value: int, decimals: int) -> str:
    negative = value < 0
    digits = str(-value if negative else value)
    if len(digits) <= decimals:
        digits = ("0" * (decimals - len(digits) + 1)) + digits
    point = len(digits) - decimals
    out = (digits[:point] + "." + digits[point:]).rstrip("0").rstrip(".")
    if out == "":
        out = "0"
    if negative and out != "0":
        out = "-" + out
    return out


def _format_usd_micro(value: int) -> str:
    return _format_fixed_decimal_i64(value, 6)


def _format_e8(value: int) -> str:
    return _format_fixed_decimal_i64(value, 8)


def _format_position_pnl(
    position: PositionRow,
    book: SymbolView | None,
    market: MarketInfo | None,
) -> str:
    if book is None or market is None or not book.has_mid:
        return "n/a"
    if book.best_bid_price > UINT64_MAX - book.best_ask_price:
        return "n/a"
    price_sum = book.best_bid_price + book.best_ask_price
    mid_price = price_sum // 2
    mid_exponent = market.px_exponent
    if price_sum % 2 != 0:
        if price_sum > UINT64_MAX // 5:
            return "n/a"
        mid_price = price_sum * 5
        mid_exponent -= 1
    mark_value = _notional_to_usd_micro(
        mid_price,
        mid_exponent,
        position.net_qty,
        market.size_decimals,
    )
    if mark_value is None:
        return "n/a"
    return _format_usd_micro(mark_value - position.cost_basis)


def _notional_to_usd_micro(
    price: int,
    price_exponent: int,
    qty: int,
    size_decimals: int,
) -> int | None:
    scaled = price * qty
    exponent = price_exponent - size_decimals
    if exponent > -6:
        scaled *= 10 ** (exponent + 6)
    elif exponent < -6:
        scaled //= 10 ** (-6 - exponent)
    if scaled > 2**63 - 1 or scaled < -(2**63):
        return None
    return scaled


def _build_order_draft(app: Any, snap: UiSnapshot) -> OrderDraft:
    symbol = _selected_symbol(app)
    market = _find_market(snap, symbol)
    if market is None:
        raise ValueError(f"symbology is missing for symbol {symbol}")
    mode = _selected_mode(app)
    role = _selected_role(app)
    is_sell = _selected_is_sell(app)
    qty = _parse_qty_decimal(app.qty.value, market.size_decimals)
    if qty == 0:
        raise ValueError("qty must be a positive decimal amount representable at size_decimals")
    if mode == OrderMode.GTC_LIMIT:
        price = _parse_price_decimal(app.price.value, market.px_exponent)
        if price == 0:
            raise ValueError("price must be a positive decimal amount on the market exponent")
        snapped = _snap_price_to_grid(price, is_sell, market.px_exponent, market.size_decimals)
        if snapped == 0:
            raise ValueError("price is invalid for the selected market grid")
        return OrderDraft(symbol, mode, role, is_sell, qty, snapped, 0)
    if mode == OrderMode.MARK_TO_MID:
        delta_ppm = _parse_delta_ppm(app.delta.value)
        if delta_ppm == 0:
            raise ValueError("delta must be a non-zero integer ppm value")
        return OrderDraft(symbol, mode, role, is_sell, qty, 0, delta_ppm)
    if role != OrderRole.TAKER:
        raise ValueError("market orders require taker role")
    return OrderDraft(symbol, mode, role, is_sell, qty, 0, 0)


def _selected_symbol(app: Any) -> int:
    selected_symbol = getattr(app, "selected_symbol", None)
    if callable(selected_symbol):
        return int(selected_symbol())
    return _parse_uint32(app.symbol.value.strip())


def _selected_mode(app: Any) -> OrderMode:
    selected_mode = getattr(app, "selected_mode", None)
    if callable(selected_mode):
        mode = selected_mode()
        if not isinstance(mode, OrderMode):
            raise ValueError("mode must be LIMIT, MARK, or MARKET")
        return mode
    return _parse_mode(app.mode.value)


def _selected_role(app: Any) -> OrderRole:
    selected_role = getattr(app, "selected_role", None)
    if callable(selected_role):
        role = selected_role()
        if not isinstance(role, OrderRole):
            raise ValueError("role must be MAKER or TAKER")
        return role
    return _parse_role(app.role.value)


def _selected_is_sell(app: Any) -> bool:
    selected_is_sell = getattr(app, "selected_is_sell", None)
    if callable(selected_is_sell):
        return bool(selected_is_sell())
    return _parse_side(app.side.value)


def _parse_uint32(value: str) -> int:
    parsed = _parse_uint64(value)
    if parsed <= 0 or parsed > 4_294_967_295:
        raise ValueError("symbol is required")
    return parsed


def _parse_qty_decimal(text: str, size_decimals: int) -> int:
    trimmed = text.strip()
    if trimmed == "":
        raise ValueError("qty must be a positive decimal amount representable at size_decimals")
    if trimmed.count(".") > 1:
        raise ValueError("qty must be a positive decimal amount representable at size_decimals")
    whole_part, dot, frac_part = trimmed.partition(".")
    if dot == "":
        frac_part = ""
    if whole_part == "" and frac_part == "":
        raise ValueError("qty must be a positive decimal amount representable at size_decimals")
    if len(frac_part) > size_decimals:
        raise ValueError("qty must be a positive decimal amount representable at size_decimals")
    scale = 10**size_decimals
    whole = 0 if whole_part == "" else _parse_uint64(whole_part)
    frac = 0
    frac_scale = scale
    for char in frac_part:
        if not char.isdigit():
            raise ValueError("qty must be a positive decimal amount representable at size_decimals")
        frac_scale //= 10
        frac += int(char) * frac_scale
    if whole > (UINT64_MAX - frac) // scale:
        raise ValueError("qty must be a positive decimal amount representable at size_decimals")
    return int(whole * scale + frac)


def _parse_price_decimal(text: str, px_exponent: int) -> int:
    trimmed = text.strip()
    if trimmed == "":
        raise ValueError("price must be a positive decimal amount on the market exponent")
    dot_seen = False
    mantissa = 0
    decimals = 0
    saw_digit = False
    for char in trimmed:
        if char == ".":
            if dot_seen:
                raise ValueError("price must be a positive decimal amount on the market exponent")
            dot_seen = True
            continue
        if not char.isdigit():
            raise ValueError("price must be a positive decimal amount on the market exponent")
        digit = int(char)
        if mantissa > (UINT64_MAX - digit) // 10:
            raise ValueError("price must be a positive decimal amount on the market exponent")
        mantissa = mantissa * 10 + digit
        saw_digit = True
        if dot_seen:
            decimals += 1
    if not saw_digit:
        raise ValueError("price must be a positive decimal amount on the market exponent")
    if mantissa == 0:
        return 0
    scale_adjust = decimals + px_exponent
    if scale_adjust < 0:
        scale = 10 ** (-scale_adjust)
        if mantissa > UINT64_MAX // scale:
            raise ValueError("price must be a positive decimal amount on the market exponent")
        return int(mantissa * scale)
    scale = 10**scale_adjust
    if scale == 0 or mantissa % scale != 0:
        raise ValueError("price must be a positive decimal amount on the market exponent")
    return int(mantissa // scale)


def _snap_price_to_grid(
    price: int,
    round_up: bool,
    px_exponent: int,
    size_decimals: int,
) -> int:
    if price == 0:
        return 0
    if _is_price_on_grid(price, px_exponent, size_decimals):
        return price
    if px_exponent >= 0:
        return price
    max_decimal_places = 6 - size_decimals
    if max_decimal_places < 0:
        return 0
    min_trailing_zeros = max(0, -px_exponent - max_decimal_places)
    sig_zeros = max(0, _decimal_digits10(price) - 5)
    step = 10 ** max(min_trailing_zeros, sig_zeros)
    remainder = price % step
    snapped = price
    if not round_up:
        snapped -= remainder
        if snapped == 0:
            return 0
    elif remainder != 0:
        if snapped > UINT64_MAX - (step - remainder):
            return 0
        snapped += step - remainder
    if not _is_price_on_grid(snapped, px_exponent, size_decimals):
        return 0
    return snapped


def _is_price_on_grid(price: int, px_exponent: int, size_decimals: int) -> bool:
    if price == 0:
        return False
    if px_exponent >= 0:
        return True
    max_decimal_places = 6 - size_decimals
    if max_decimal_places < 0:
        return False
    normalized_price = price
    decimal_places = -px_exponent
    while decimal_places > 0 and normalized_price % 10 == 0:
        normalized_price //= 10
        decimal_places -= 1
    if decimal_places > max_decimal_places:
        return False
    if decimal_places == 0:
        return True
    return _decimal_digits10(normalized_price) <= 5


def _decimal_digits10(value: int) -> int:
    digits = 1
    while value >= 10:
        value //= 10
        digits += 1
    return digits


def _parse_delta_ppm(value: str) -> int:
    text = value.strip()
    if text == "":
        raise ValueError("delta must be a non-zero integer ppm value")
    try:
        parsed = int(text, 10)
    except ValueError as exc:
        raise ValueError("delta must be a non-zero integer ppm value") from exc
    if parsed < INT32_MIN or parsed > INT32_MAX:
        raise ValueError("delta must be a non-zero integer ppm value")
    return parsed


def _parse_uint64(value: str) -> int:
    if value == "" or not value.isdigit():
        raise ValueError("expected unsigned integer")
    parsed = int(value, 10)
    if parsed > UINT64_MAX:
        raise ValueError("expected unsigned integer")
    return parsed


def _order_preview(app: Any, snap: UiSnapshot) -> str:
    try:
        draft = _build_order_draft(app, snap)
    except Exception as exc:
        return str(exc)
    side = "SELL" if draft.is_sell else "BUY"
    role = "TAKER" if draft.role == OrderRole.TAKER else "MAKER"
    mode = _mode_label(draft.mode)
    market = _find_market(snap, draft.symbol)
    if draft.mode == OrderMode.GTC_LIMIT:
        order_detail = f"{mode} @{_format_price(draft.price, market)}"
    elif draft.mode == OrderMode.MARK_TO_MID:
        order_detail = f"{mode} {draft.delta_ppm} ppm delta"
    else:
        order_detail = mode
    mid = ""
    book = _find_book(snap, draft.symbol)
    if book is not None and book.has_mid:
        mid = f" mid={_mid_label(book)}"
    return f"{side} {role} {order_detail} qty={_format_atomic_qty(draft.qty, market)}{mid}"


def _mode_label(mode: OrderMode) -> str:
    if mode == OrderMode.MARK_TO_MID:
        return "mark-to-mid"
    if mode == OrderMode.MARKET:
        return "market"
    return "GTC limit"


def _parse_mode(value: str) -> OrderMode:
    normalized = value.strip().upper()
    if normalized == "LIMIT":
        return OrderMode.GTC_LIMIT
    if normalized == "MARK" or normalized == "MARK_TO_MID":
        return OrderMode.MARK_TO_MID
    if normalized == "MARKET":
        return OrderMode.MARKET
    raise ValueError("mode must be LIMIT, MARK, or MARKET")


def _parse_role(value: str) -> OrderRole:
    normalized = value.strip().upper()
    if normalized == "MAKER":
        return OrderRole.MAKER
    if normalized == "TAKER":
        return OrderRole.TAKER
    raise ValueError("role must be MAKER or TAKER")


def _parse_side(value: str) -> bool:
    normalized = value.strip().upper()
    if normalized == "SELL":
        return True
    if normalized == "BUY":
        return False
    raise ValueError("side must be BUY or SELL")


if __name__ == "__main__":
    main()
