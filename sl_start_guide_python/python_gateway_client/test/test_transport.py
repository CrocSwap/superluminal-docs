from __future__ import annotations

import unittest
from unittest.mock import patch

from nacl.signing import SigningKey

from dfba_client.client import GatewayClient
from dfba_client.ingestion import IngestionEngine
from dfba_client.signer import Keypair
from dfba_client.transport import PING_INTERVAL_SECONDS, WebSocketTransport, _websocket_close_detail
from dfba_client.types import ClientConfig, MarketInfo


class CloseInfoWebSocket:
    close_code = 1000
    close_reason = "new_session_authenticated"


class NoCloseInfoWebSocket:
    pass


class FailingSecondSendWebSocket:
    close_code = 1006
    close_reason = ""

    def __init__(self) -> None:
        self.send_count = 0
        self.sent_payloads: list[str] = []
        self.yielded_auth_challenge = False

    async def __aenter__(self) -> FailingSecondSendWebSocket:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def send(self, payload: str) -> None:
        self.send_count += 1
        self.sent_payloads.append(payload)
        if self.send_count == 2:
            raise RuntimeError("socket down")

    def __aiter__(self) -> FailingSecondSendWebSocket:
        return self

    async def __anext__(self) -> str:
        if self.yielded_auth_challenge:
            raise StopAsyncIteration
        self.yielded_auth_challenge = True
        return (
            '{"type":"auth_challenge","wallet_pubkey":"wallet","nonce":"nonce",'
            '"message":"challenge","expires_unix_ms":9999999999999}'
        )


class TransportTest(unittest.TestCase):
    def test_default_ping_interval_matches_cpp_api_client(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        transport = WebSocketTransport(config.ws_url, client)
        self.assertEqual(transport.ping_interval_seconds, PING_INTERVAL_SECONDS)
        self.assertEqual(PING_INTERVAL_SECONDS, 0.1)

    def test_websocket_close_detail_matches_cpp_format(self) -> None:
        self.assertEqual(
            _websocket_close_detail(CloseInfoWebSocket(), ""),
            "code=1000 reason=new_session_authenticated",
        )

    def test_websocket_close_detail_uses_fallback_without_close_code(self) -> None:
        self.assertEqual(_websocket_close_detail(NoCloseInfoWebSocket(), "socket failed"), "socket failed")


class TransportAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_failure_does_not_overwrite_cpp_style_websocket_error(self) -> None:
        config = ClientConfig(
            ws_url="ws://127.0.0.1:8080/ws",
            keypair_path="unused.json",
            feed_ids=(7,),
            subaccount_id=1,
        )
        client = GatewayClient(config, keypair=Keypair(SigningKey(bytes(range(1, 33)))))
        client.ingestion = IngestionEngine(config, {7: MarketInfo(7, "BTC", "USDC", -8, 5)}, "wallet")
        websocket = FailingSecondSendWebSocket()
        transport = WebSocketTransport(config.ws_url, client, ping_interval_seconds=60.0)

        def connect(_url: str, max_size: int) -> FailingSecondSendWebSocket:
            self.assertEqual(max_size, 256 * 1024)
            return websocket

        with patch("websockets.asyncio.client.connect", connect):
            await transport.run_forever()

        snapshot = client.snapshot()
        self.assertEqual(websocket.send_count, 2)
        self.assertEqual(snapshot.connection_status, "error")
        self.assertEqual(snapshot.last_error, "auth_response send failed")
        self.assertEqual(snapshot.last_notice, "websocket error")
        self.assertIn("websocket error: auth_response send failed", snapshot.log_lines)
        self.assertNotIn("websocket closed code=1006 reason=", snapshot.log_lines)


if __name__ == "__main__":
    unittest.main()
