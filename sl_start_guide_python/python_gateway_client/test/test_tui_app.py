from __future__ import annotations

import queue
import unittest
from types import SimpleNamespace

from dfba_client.tui.app import (
    SocketWorker,
    StopIntent,
    SubmitOrderIntent,
    _build_order_draft,
    _fill_status,
    _fill_summary,
    _format_mid_price,
    _format_position_pnl,
    _format_price,
    _visible_books,
)
from dfba_client.types import (
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
)


class WebsocketErrorClient:
    def __init__(self) -> None:
        self.recorded_errors: list[str] = []
        self._snapshot = UiSnapshot(connection_status="error", last_error="signed_order send failed")

    async def submit_order(self, _draft: OrderDraft) -> None:
        raise RuntimeError("socket down")

    def has_websocket_error(self) -> bool:
        return True

    def record_error(self, error: str) -> None:
        self.recorded_errors.append(error)

    def snapshot(self) -> UiSnapshot:
        return self._snapshot


class TuiAppHelperTest(unittest.TestCase):
    def test_order_ticket_parses_decimal_qty_and_snaps_limit_price(self) -> None:
        app = SimpleNamespace(
            symbol=SimpleNamespace(value="7"),
            qty=SimpleNamespace(value="0.001"),
            price=SimpleNamespace(value="1000.00000015"),
            delta=SimpleNamespace(value=""),
            side=SimpleNamespace(value="BUY"),
            role=SimpleNamespace(value="MAKER"),
            mode=SimpleNamespace(value="LIMIT"),
        )
        snap = UiSnapshot(
            books=(
                SymbolView(
                    feed_id=7,
                    symbol="BTC/USDC",
                    px_exponent=-8,
                    size_decimals=5,
                    has_symbology=True,
                ),
            )
        )
        draft = _build_order_draft(app, snap)
        self.assertEqual(draft.symbol, 7)
        self.assertEqual(draft.mode, OrderMode.GTC_LIMIT)
        self.assertEqual(draft.role, OrderRole.MAKER)
        self.assertEqual(draft.qty, 100)
        self.assertEqual(draft.price, 100_000_000_000)

    def test_price_formatting_matches_cpp_visible_decimals(self) -> None:
        market = MarketInfo(7, "BTC", "USDC", -8, 5)
        self.assertEqual(_format_price(7_091_400_000_000, market), "70914.00")
        self.assertEqual(_format_mid_price(10_001, 10_002, -2), "100.015")

    def test_mid_and_position_pnl_overflow_match_cpp_na(self) -> None:
        market = MarketInfo(7, "BTC", "USDC", 0, 0)
        position = PositionRow(7, UINT64_MAX, 0, 0, 0, 0)
        book = SymbolView(
            feed_id=7,
            px_exponent=0,
            size_decimals=0,
            has_symbology=True,
            has_mid=True,
            best_bid_price=UINT64_MAX,
            best_ask_price=1,
        )
        self.assertEqual(_format_mid_price(UINT64_MAX, 1, 0), "n/a")
        self.assertEqual(_format_position_pnl(position, book, market), "n/a")

    def test_visible_books_are_not_capped_to_three(self) -> None:
        snap = UiSnapshot(
            books=tuple(SymbolView(feed_id=feed_id) for feed_id in (1, 2, 3, 4))
        )

        self.assertEqual(tuple(book.feed_id for book in _visible_books(snap)), (1, 2, 3, 4))

    def test_recent_fill_helpers_split_execution_and_settlement(self) -> None:
        market = MarketInfo(7, "BTC", "USDC", -8, 5)
        fill = FillRow(
            seq=12,
            trade_id=34,
            subaccount_id=1,
            client_instruction_id=56,
            external_order_id=78,
            symbol=7,
            side="BUY",
            role="MAKER",
            price=7_645_000_000_000,
            qty=100,
            order_settled_qty=100,
            settle_delta_ns=1_250_000,
            has_order_settled_qty=True,
            has_settle_delta_ns=True,
            settled=True,
        )

        self.assertEqual(
            _fill_summary(fill, market),
            "seq=12 trade=34 oid=78 cid=56 sym=7 BTC/USDC BUY qty=0.001 px=76450.00",
        )
        self.assertEqual(
            _fill_status(fill, market),
            "settle=settled dt=1.25 ms order_settled_qty=0.001",
        )


class TuiSocketWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_intent_pump_does_not_overwrite_websocket_send_error(self) -> None:
        snapshot_queue: queue.Queue[UiSnapshot] = queue.Queue(maxsize=2)
        intent_queue: queue.Queue[object] = queue.Queue(maxsize=2)
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        worker = SocketWorker(config, snapshot_queue, intent_queue)  # type: ignore[arg-type]
        client = WebsocketErrorClient()
        intent_queue.put_nowait(
            SubmitOrderIntent(OrderDraft(7, OrderMode.GTC_LIMIT, OrderRole.MAKER, False, 1, 100))
        )
        intent_queue.put_nowait(StopIntent())

        await worker._pump_intents(client)  # type: ignore[arg-type]
        snapshot = snapshot_queue.get_nowait()

        self.assertEqual(client.recorded_errors, [])
        self.assertEqual(snapshot.last_error, "signed_order send failed")


if __name__ == "__main__":
    unittest.main()
