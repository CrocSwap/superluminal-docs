from __future__ import annotations

import unittest
from unittest.mock import patch

from nacl.signing import SigningKey

from dfba_client.client import GatewayClient
from dfba_client.ingestion import IngestionEngine
from dfba_client.messages import AckMessage, AuthChallengeMessage
from dfba_client.signer import Keypair
from dfba_client.symbology import SymbologySnapshot
from dfba_client.types import (
    AccountSubaccountRow,
    ClientConfig,
    MarketInfo,
    OrderDraft,
    OrderMode,
    OrderRole,
    SessionPhase,
    WorkingOrderRow,
)


class ClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_auth_ack_starts_subscriptions_without_deposit_bootstrap(self) -> None:
        sent: list[str] = []
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7, 8),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        client.ingestion = IngestionEngine(
            config,
            {7: MarketInfo(7, "BTC", "USDC", -8, 5), 8: MarketInfo(8, "ETH", "USDC", -8, 4)},
            "wallet",
        )
        client.ingestion.phase = SessionPhase.AWAITING_AUTH_ACK
        client.ingestion.auth_wallet_ack_received = True

        async def sender(payload: str) -> None:
            sent.append(payload)

        client.attach_sender(sender)
        await client.on_message(AckMessage(status="accepted"))
        self.assertEqual(
            sent,
            [
                '{"type":"subscribe_account"}',
                '{"type":"subscribe_market","symbol":7}',
                '{"type":"subscribe_market","symbol":8}',
            ],
        )
        self.assertEqual(
            client.snapshot().log_lines,
            ("send subscribe_account", "send subscribe_market", "send subscribe_market"),
        )
        self.assertEqual(client.ingestion.phase, SessionPhase.RUNNING)
        self.assertTrue(client.snapshot().account_stream_started)

    async def test_gap_resubscribe_log_matches_cpp_context(self) -> None:
        sent: list[str] = []
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        client.ingestion.phase = SessionPhase.RUNNING
        client.ingestion.resubscribe_markets.append(7)

        async def sender(payload: str) -> None:
            sent.append(payload)

        client.attach_sender(sender)
        await client.on_message(AckMessage(status="accepted"))
        self.assertEqual(sent, ['{"type":"subscribe_market","symbol":7}'])
        self.assertEqual(client.snapshot().log_lines, ("send resubscribe_market",))

    async def test_terminal_session_phase_disables_reconnect(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        client.ingestion.phase = SessionPhase.RUNNING
        self.assertTrue(client.should_reconnect())
        client.ingestion.phase = SessionPhase.AUTH_EXPIRED
        self.assertFalse(client.should_reconnect())
        client.ingestion.phase = SessionPhase.FAILED
        self.assertFalse(client.should_reconnect())

    async def test_send_failure_records_cpp_style_websocket_error(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        client.ingestion.gateway_rtt_avg.add_sample(100.0)

        async def sender(_payload: str) -> None:
            raise RuntimeError("socket down")

        client.attach_sender(sender)
        with self.assertRaisesRegex(RuntimeError, "socket down"):
            await client._send("{}", "auth_wallet")
        snapshot = client.snapshot()

        self.assertEqual(snapshot.connection_status, "error")
        self.assertEqual(snapshot.last_error, "auth_wallet send failed")
        self.assertEqual(snapshot.last_notice, "websocket error")
        self.assertEqual(snapshot.gateway_rtt_avg_ns, 0)
        self.assertEqual(snapshot.log_lines, ("websocket error: auth_wallet send failed",))
        self.assertFalse(client.should_reconnect())

    async def test_submit_order_rejects_when_session_is_not_running(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        draft = OrderDraft(7, OrderMode.GTC_LIMIT, OrderRole.MAKER, False, 10, 100)
        with self.assertRaisesRegex(RuntimeError, "session is not ready for orders"):
            await client.submit_order(draft)

    async def test_cancel_order_uses_working_order_symbol_and_role(self) -> None:
        sent: list[str] = []
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        client.ingestion.phase = SessionPhase.RUNNING
        client.ingestion.connection_status = "connected"
        client.ingestion.auth_status = "authenticated"
        subaccount = AccountSubaccountRow(1)
        subaccount.working_orders.append(
            WorkingOrderRow(88, 99, 7, "BID", "TAKER", "GTC", 123, 10, 10)
        )
        client.ingestion.account_state.subaccounts[1] = subaccount

        async def sender(payload: str) -> None:
            sent.append(payload)

        client.attach_sender(sender)
        with patch("dfba_client.client.time.time_ns", return_value=1_710_000_000_000_000_000):
            request_id = await client.cancel_order(1, 88)
        self.assertEqual(request_id, 1_710_000_000_000_000_000)
        self.assertEqual(len(sent), 1)
        self.assertIn('"type":"signed_cancel"', sent[0])
        self.assertEqual(client.ingestion.last_notice, "sent signed_cancel oid=88")
        self.assertEqual(client.snapshot().log_lines, ("send signed_cancel",))

    async def test_on_raw_message_logs_decoded_non_book_type_like_cpp(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")

        await client.on_raw_message('{"type":"AccountSnapshotError","message":"bad snapshot"}')

        self.assertEqual(client.snapshot().log_lines[0], "recv type=AccountSnapshotError")
        self.assertIn("recv AccountSnapshotError payload=", client.snapshot().log_lines[1])

    async def test_request_id_seed_uses_realtime_nanoseconds(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config)
        with patch("dfba_client.client.time.time_ns", return_value=1_710_000_000_000_000_000):
            self.assertEqual(await client._next_request_id(), 1_710_000_000_000_000_000)
            self.assertEqual(await client._next_request_id(), 1_710_000_000_000_000_001)

    async def test_auth_challenge_rejects_wrong_wallet_without_signing(self) -> None:
        sent: list[str] = []
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")

        async def sender(payload: str) -> None:
            sent.append(payload)

        client.attach_sender(sender)
        await client.on_message(
            AuthChallengeMessage(
                wallet_pubkey="other",
                nonce="nonce",
                message="challenge",
                expires_unix_ms=9_999_999_999_999,
            )
        )
        self.assertEqual(sent, [])
        snapshot = client.snapshot()
        self.assertEqual(snapshot.auth_status, "rejected")
        self.assertEqual(snapshot.account_status, "blocked")
        self.assertEqual(snapshot.last_error, "auth challenge wallet mismatch")
        self.assertEqual(client.ingestion.phase, SessionPhase.FAILED)

    async def test_auth_wallet_ack_does_not_authenticate_before_auth_response_ack(self) -> None:
        sent: list[str] = []
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")

        async def sender(payload: str) -> None:
            sent.append(payload)

        client.attach_sender(sender)
        await client.on_message(
            AuthChallengeMessage(
                wallet_pubkey="wallet",
                nonce="nonce",
                message="challenge",
                expires_unix_ms=9_999_999_999_999,
            )
        )
        await client.on_message(AckMessage(status="accepted"))
        snapshot = client.snapshot()

        self.assertEqual(len(sent), 1)
        self.assertIn('"type":"auth_response"', sent[0])
        self.assertEqual(client.ingestion.phase, SessionPhase.AWAITING_AUTH_ACK)
        self.assertEqual(snapshot.auth_status, "signing challenge")
        self.assertFalse(snapshot.account_stream_started)
        self.assertEqual(snapshot.last_notice, "wallet challenge issued")

        await client.on_message(AckMessage(status="accepted"))
        self.assertEqual(
            sent[1:],
            [
                '{"type":"subscribe_account"}',
                '{"type":"subscribe_market","symbol":7}',
            ],
        )
        self.assertEqual(client.ingestion.phase, SessionPhase.RUNNING)
        self.assertTrue(client.snapshot().account_stream_started)

    async def test_auth_challenge_rejects_expired_without_signing(self) -> None:
        sent: list[str] = []
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")

        async def sender(payload: str) -> None:
            sent.append(payload)

        client.attach_sender(sender)
        with patch("dfba_client.client.time.time_ns", return_value=2_000_000_000_000_000_000):
            await client.on_message(
                AuthChallengeMessage(
                    wallet_pubkey="wallet",
                    nonce="nonce",
                    message="challenge",
                    expires_unix_ms=1_999_999_999_999,
                )
            )
        self.assertEqual(sent, [])
        snapshot = client.snapshot()
        self.assertEqual(snapshot.auth_status, "expired")
        self.assertEqual(snapshot.account_status, "blocked")
        self.assertEqual(snapshot.last_error, "received expired auth challenge")
        self.assertEqual(client.ingestion.phase, SessionPhase.FAILED)

    async def test_initialize_rejects_missing_configured_feed(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        with patch(
            "dfba_client.client.fetch_symbology_snapshot",
            return_value=SymbologySnapshot(version=1, markets={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "configured feed_id 7 missing from symbology"):
                await client.initialize()

    async def test_initialize_rejects_inactive_configured_feed(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        with patch(
            "dfba_client.client.fetch_symbology_snapshot",
            return_value=SymbologySnapshot(
                version=1,
                markets={7: MarketInfo(7, "BTC", "USDC", -8, 5, status="inactive")},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "configured feed_id 7 is not active in symbology"):
                await client.initialize()

if __name__ == "__main__":
    unittest.main()
