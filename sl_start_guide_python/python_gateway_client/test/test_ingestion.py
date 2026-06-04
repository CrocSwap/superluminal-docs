from __future__ import annotations

import unittest
from unittest.mock import patch

from dfba_client.ingestion import IngestionEngine
from dfba_client.messages import (
    AccountFundingUpdateMessage,
    AccountSnapshotErrorMessage,
    AckMessage,
    AuthExpiredMessage,
    BookDeltaMessage,
    BookLevelMessage,
    BookSnapshotMessage,
    LocalOrderRejectMessage,
    PongMessage,
    SessionPreemptedMessage,
    TradeMessage,
    UnknownMessage,
)
from dfba_client.types import BookType, ClientConfig, MarketInfo, SessionPhase


class IngestionTest(unittest.TestCase):
    def test_pong_updates_gateway_rtt_running_average(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")

        with patch("time.monotonic_ns", side_effect=(1_100, 1_500, 900)):
            engine.apply(PongMessage(client_unix_ns=1_000))
            engine.apply(PongMessage(client_unix_ns=1_000))
            engine.apply(PongMessage(client_unix_ns=1_000))

        snapshot = engine.snapshot()
        self.assertEqual(snapshot.gateway_rtt_samples, 2)
        self.assertEqual(snapshot.gateway_rtt_avg_ns, 300.0)

    def test_combined_book_updates_incrementally(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(
            config,
            {7: MarketInfo(7, "BTC", "USDC", -8, 5)},
            "wallet",
        )
        engine.apply(
            BookSnapshotMessage(
                seq=1,
                book_type=BookType.MAKER,
                feed_id=7,
                symbol="BTC",
                book_cursor=10,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                bids=(BookLevelMessage(100, 5),),
                asks=(BookLevelMessage(105, 3),),
                gateway_send_unix_ns=0,
            )
        )
        snapshot = engine.snapshot()
        self.assertFalse(snapshot.books[0].synced)

        engine.apply(
            BookSnapshotMessage(
                seq=2,
                book_type=BookType.TAKER,
                feed_id=7,
                symbol="BTC",
                book_cursor=20,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                bids=(BookLevelMessage(101, 2),),
                asks=(BookLevelMessage(104, 4),),
                gateway_send_unix_ns=0,
            )
        )
        snapshot = engine.snapshot()
        book = snapshot.books[0]
        self.assertTrue(book.synced)
        self.assertEqual(book.best_bid_price, 101)
        self.assertEqual(book.best_ask_price, 104)

        engine.apply(
            BookDeltaMessage(
                seq=3,
                book_type=BookType.TAKER,
                feed_id=7,
                symbol="BTC",
                prev_book_cursor=20,
                book_cursor=21,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                mark_price=0,
                bids=(BookLevelMessage(101, 0),),
                asks=(),
                gateway_send_unix_ns=0,
            )
        )
        snapshot = engine.snapshot()
        book = snapshot.books[0]
        self.assertEqual(book.best_bid_price, 100)
        self.assertEqual(book.best_ask_price, 104)

    def test_ladder_quantities_use_symbology_size_decimals(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.apply(
            BookSnapshotMessage(
                seq=1,
                book_type=BookType.MAKER,
                feed_id=7,
                symbol="BTC",
                book_cursor=10,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                bids=(BookLevelMessage(100, 500_000),),
                asks=(BookLevelMessage(105, 250_000),),
                gateway_send_unix_ns=0,
            )
        )
        engine.apply(
            BookSnapshotMessage(
                seq=2,
                book_type=BookType.TAKER,
                feed_id=7,
                symbol="BTC",
                book_cursor=20,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                bids=(),
                asks=(),
                gateway_send_unix_ns=0,
            )
        )
        book = engine.snapshot().books[0]
        self.assertEqual(book.bids[0].bid_qty, 5.0)
        self.assertEqual(book.asks[0].ask_qty, 2.5)

    def test_account_snapshot_error_logs_payload_notice(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.apply(AccountSnapshotErrorMessage(wallet_pubkey="wallet", message="bad snapshot"))
        snapshot = engine.snapshot()
        self.assertIn("AccountSnapshotError", snapshot.last_notice)
        self.assertIn("recv AccountSnapshotError payload=", snapshot.log_lines[-1])

    def test_local_order_reject_updates_notice_without_sticky_error(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.last_error = "old error"

        engine.apply(
            LocalOrderRejectMessage(
                reason="invalid_symbol",
                client_instruction_id=123,
                external_order_id=456,
            )
        )
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.last_error, "old error")
        self.assertIn("order rejected", snapshot.last_notice)
        self.assertIn("reason=invalid_symbol", snapshot.last_notice)
        self.assertIn("cid=123", snapshot.last_notice)
        self.assertIn("oid=456", snapshot.last_notice)

    def test_unknown_message_surfaces_raw_payload_like_cpp(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        raw = '{"type":"FutureMessage","field":123}'

        engine.apply(UnknownMessage(message_type="FutureMessage", payload=raw))
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.last_notice, raw)
        self.assertEqual(snapshot.log_lines, (f"unhandled payload={raw}",))

    def test_malformed_funding_update_sets_account_stream_error_like_cpp(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")

        engine.apply(
            AccountFundingUpdateMessage(
                seq=1,
                subaccount_id=7,
                symbol=7,
                market_id=7,
                prev_funding_index_e8=5,
                last_funding_index_e8=5,
                funding_delta=0,
                funding_accrued=0,
            )
        )
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.last_notice, "account stream update failed")
        self.assertEqual(snapshot.last_error, "malformed funding update event")

    def test_successful_account_stream_update_clears_previous_error_like_cpp(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.last_error = "malformed funding update event"

        engine.apply(
            AccountFundingUpdateMessage(
                seq=2,
                subaccount_id=7,
                symbol=7,
                market_id=7,
                prev_funding_index_e8=5,
                last_funding_index_e8=6,
                funding_delta=1,
                funding_accrued=1,
            )
        )
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.last_notice, "funding update sym=7 delta=1")
        self.assertEqual(snapshot.last_error, "")

    def test_close_preserves_failed_auth_state(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.phase = SessionPhase.FAILED
        engine.auth_status = "preempted"
        engine.account_status = "blocked"

        engine.on_close("code=1000 reason=new_session_authenticated")
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.connection_status, "closed")
        self.assertEqual(snapshot.auth_status, "preempted")
        self.assertEqual(snapshot.account_status, "blocked")
        self.assertIn("new_session_authenticated", snapshot.last_notice)

    def test_auth_expired_matches_cpp_state(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.account_status = "running"
        engine.last_error = "old error"

        engine.apply(AuthExpiredMessage(reason="ttl"))
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.auth_status, "expired")
        self.assertEqual(snapshot.account_status, "running")
        self.assertEqual(snapshot.last_error, "old error")
        self.assertEqual(snapshot.last_notice, "auth expired: ttl")
        self.assertEqual(engine.phase, SessionPhase.AUTH_EXPIRED)

    def test_session_preempted_matches_cpp_state(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.last_error = "old error"

        engine.apply(SessionPreemptedMessage(reason="new session"))
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.connection_status, "preempted")
        self.assertEqual(snapshot.auth_status, "preempted")
        self.assertEqual(snapshot.account_status, "preempted")
        self.assertEqual(snapshot.last_error, "")
        self.assertEqual(snapshot.last_notice, "session preempted: new session")
        self.assertEqual(engine.phase, SessionPhase.FAILED)

    def test_auth_ack_reject_and_busy_notice_match_cpp(self) -> None:
        for status in ("rejected", "busy"):
            with self.subTest(status=status):
                config = ClientConfig(
                    ws_url="ws://127.0.0.1:8080/ws",
                    keypair_path="unused.json",
                    feed_ids=(7,),
                    subaccount_id=1,
                )
                engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
                engine.phase = SessionPhase.AWAITING_AUTH_ACK

                engine.apply(AckMessage(status=status, reason="bad_auth"))
                snapshot = engine.snapshot()

                self.assertEqual(snapshot.auth_status, status)
                self.assertEqual(snapshot.account_status, "blocked")
                self.assertEqual(snapshot.last_notice, "wallet authentication failed")
                self.assertEqual(snapshot.last_error, "authentication failed: bad_auth")
                self.assertEqual(engine.phase, SessionPhase.FAILED)

    def test_trade_log_keeps_200_lines_like_cpp(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        for seq in range(201):
            engine.apply(
                TradeMessage(
                    seq=seq,
                    trade_id=seq,
                    price=100,
                    qty=1,
                    symbol_id=7,
                    symbol="BTC",
                    side="BID",
                )
            )

        snapshot = engine.snapshot()
        self.assertEqual(len(snapshot.trade_lines), 200)
        self.assertIn("seq=1 ", snapshot.trade_lines[0])
        self.assertIn("seq=200 ", snapshot.trade_lines[-1])

    def test_full_book_update_merges_like_ordered_map(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        engine = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        engine.apply(
            BookSnapshotMessage(
                seq=1,
                book_type=BookType.MAKER,
                feed_id=7,
                symbol="BTC",
                book_cursor=10,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                bids=(BookLevelMessage(110, 1), BookLevelMessage(100, 2)),
                asks=(BookLevelMessage(120, 3),),
                gateway_send_unix_ns=0,
            )
        )
        engine.apply(
            BookSnapshotMessage(
                seq=2,
                book_type=BookType.MAKER,
                feed_id=7,
                symbol="BTC",
                book_cursor=11,
                batch_id=0,
                term=0,
                matcher_unix_ns=0,
                depth_cap=0,
                bids=(BookLevelMessage(105, 4),),
                asks=(BookLevelMessage(115, 5),),
                gateway_send_unix_ns=0,
            )
        )
        book = engine.snapshot().books[0]
        self.assertEqual(book.best_bid_price, 105)
        self.assertEqual(book.best_ask_price, 115)


if __name__ == "__main__":
    unittest.main()
