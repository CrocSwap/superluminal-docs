from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import dfba_client.commands as commands
from dfba_client.codec import decode_gateway_message
from dfba_client.ingestion import IngestionEngine
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
from dfba_client.signer import (
    Keypair,
    build_signed_cancel_proof_base64,
    build_signed_modify_proof_base64,
    build_signed_order_proof_base64,
    load_keypair,
    sign_auth_challenge_base64,
)
from dfba_client.symbology import derive_symbology_url, fetch_symbology_snapshot
from dfba_client.types import (
    REQUEST_ID_PUBLIC_MAX,
    ClientConfig,
    MarketInfo,
    OrderDraft,
    OrderRole,
    SessionPhase,
    UiSnapshot,
)

Sender = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class GatewayClient:
    config: ClientConfig
    keypair: Keypair | None = None
    sender: Sender | None = None
    ingestion: IngestionEngine | None = None
    request_id: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def initialize(self) -> None:
        if self.keypair is None:
            self.keypair = load_keypair(self.config.keypair_path)
        wallet_pubkey = self.config.wallet_pubkey if self.config.wallet_pubkey != "" else self.keypair.public_key_base58
        symbology = fetch_symbology_snapshot(derive_symbology_url(self.config.ws_url))
        _validate_configured_feeds(self.config.feed_ids, symbology.markets)
        self.ingestion = IngestionEngine(self.config, symbology.markets, wallet_pubkey)

    def attach_sender(self, sender: Sender) -> None:
        self.sender = sender

    def detach_sender(self) -> None:
        self.sender = None

    def snapshot(self) -> UiSnapshot:
        if self.ingestion is None:
            return UiSnapshot(ws_url=self.config.ws_url)
        return self.ingestion.snapshot()

    def record_error(self, error: str) -> None:
        self._engine().last_error = error

    def record_websocket_error(self, reason: str) -> None:
        engine = self._engine()
        engine.connection_status = "error"
        engine.last_error = reason
        engine.last_notice = "websocket error"
        engine.gateway_rtt_avg.reset()
        engine._log(f"websocket error: {reason}")

    def has_websocket_error(self) -> bool:
        if self.ingestion is None:
            return False
        return self.ingestion.connection_status == "error"

    def log_event(self, line: str) -> None:
        self._engine()._log(line)

    def should_reconnect(self) -> bool:
        if self.ingestion is None:
            return True
        return self.ingestion.connection_status != "error" and self.ingestion.phase not in (
            SessionPhase.AUTH_EXPIRED,
            SessionPhase.FAILED,
        )

    async def on_open(self) -> None:
        engine = self._engine()
        engine.on_open()
        await self._send(
            commands.encode_auth_wallet(engine.wallet_pubkey, self.config.auth_expiry_seconds),
            "auth_wallet",
        )

    async def on_close(self, reason: str) -> None:
        self._engine().on_close(reason)

    async def on_raw_message(self, raw: str) -> None:
        message = decode_gateway_message(raw)
        self._log_received_message(message)
        await self.on_message(message)

    async def on_message(self, message: GatewayMessage) -> None:
        engine = self._engine()
        prior_phase = engine.phase
        engine.apply(message)
        if isinstance(message, AuthChallengeMessage):
            if message.wallet_pubkey != engine.wallet_pubkey:
                _reject_auth_challenge(engine, "rejected", "auth challenge wallet mismatch")
                return
            if message.expires_unix_ms <= time.time_ns() // 1_000_000:
                _reject_auth_challenge(engine, "expired", "received expired auth challenge")
                return
            engine.auth_status = "signing challenge"
            engine.last_notice = "signing auth challenge"
            keypair = self._keypair()
            signature = sign_auth_challenge_base64(message.message, keypair)
            await self._send(commands.encode_auth_response(message.nonce, signature), "auth_response")
        if prior_phase == SessionPhase.AWAITING_AUTH_ACK and engine.phase == SessionPhase.RUNNING:
            await self.subscribe_after_auth()
        for feed_id in engine.drain_resubscribe_markets():
            await self._send(commands.encode_subscribe_market(feed_id), "resubscribe_market")

    async def subscribe_after_auth(self) -> None:
        self._engine().mark_account_stream_started()
        await self._send(commands.encode_subscribe_account(), "subscribe_account")
        for feed_id in self.config.feed_ids:
            await self._send(commands.encode_subscribe_market(feed_id), "subscribe_market")

    async def send_ping(self) -> None:
        await self._send(commands.encode_ping(time.monotonic_ns()))

    async def submit_order(self, draft: OrderDraft) -> int:
        engine = self._ensure_ready_for_orders()
        request_id = await self._next_request_id()
        msg_b64 = build_signed_order_proof_base64(
            self._keypair(),
            self.config.partition_id,
            self.config.subaccount_id,
            request_id,
            draft,
        )
        await self._send(commands.encode_signed_order(msg_b64), "signed_order")
        engine.last_error = ""
        engine.last_notice = "sent signed_order"
        return request_id

    async def cancel_order(
        self,
        subaccount_id: int,
        external_order_id: int,
    ) -> int:
        engine = self._ensure_ready_for_orders()
        cancel_info = engine.account_state.find_working_order_cancel_info(
            subaccount_id,
            external_order_id,
        )
        if cancel_info is None:
            raise RuntimeError("working order not found")
        symbol, role = cancel_info
        request_id = await self._next_request_id()
        msg_b64 = build_signed_cancel_proof_base64(
            self._keypair(),
            self.config.partition_id,
            subaccount_id,
            external_order_id,
            request_id,
            symbol,
            role,
        )
        await self._send(commands.encode_signed_cancel(msg_b64), "signed_cancel")
        engine.last_error = ""
        engine.last_notice = f"sent signed_cancel oid={external_order_id}"
        return request_id

    async def modify_order(
        self,
        external_order_id: int,
        symbol: int,
        role: OrderRole,
        is_sell: bool,
        price: int,
        qty: int,
        delta_ppm: int,
    ) -> int:
        engine = self._ensure_ready_for_orders()
        request_id = await self._next_request_id()
        msg_b64 = build_signed_modify_proof_base64(
            self._keypair(),
            self.config.partition_id,
            self.config.subaccount_id,
            external_order_id,
            request_id,
            symbol,
            role,
            is_sell,
            price,
            qty,
            delta_ppm,
        )
        await self._send(commands.encode_signed_modify(msg_b64), "signed_modify")
        engine.last_error = ""
        engine.last_notice = f"sent signed_modify oid={external_order_id}"
        return request_id

    async def _next_request_id(self) -> int:
        async with self._lock:
            if self.request_id == 0:
                seed = time.time_ns()
                if seed <= 0 or seed > REQUEST_ID_PUBLIC_MAX:
                    raise RuntimeError("failed to allocate request id")
                self.request_id = seed
                return self.request_id
            if self.request_id >= REQUEST_ID_PUBLIC_MAX:
                raise RuntimeError("failed to allocate request id")
            self.request_id += 1
            return self.request_id

    async def _send(self, payload: str, context: str | None = None) -> None:
        if self.sender is None:
            if context is not None:
                self.record_websocket_error(f"{context} send failed")
            raise RuntimeError("client sender is not attached")
        try:
            await self.sender(payload)
        except Exception:
            if context is not None:
                self.record_websocket_error(f"{context} send failed")
            raise
        if context is not None:
            self.log_event(f"send {context}")

    def _log_received_message(self, message: GatewayMessage) -> None:
        msg_type = _received_message_type(message)
        if msg_type is not None:
            self.log_event(f"recv type={msg_type}")

    def _engine(self) -> IngestionEngine:
        if self.ingestion is None:
            raise RuntimeError("client is not initialized")
        return self.ingestion

    def _keypair(self) -> Keypair:
        if self.keypair is None:
            raise RuntimeError("client keypair is not loaded")
        return self.keypair

    def _ensure_ready_for_orders(self) -> IngestionEngine:
        engine = self._engine()
        if engine.phase != SessionPhase.RUNNING:
            raise RuntimeError("session is not ready for orders")
        if engine.connection_status != "connected":
            raise RuntimeError("websocket is not connected")
        if engine.auth_status != "authenticated":
            raise RuntimeError("wallet is not authenticated")
        if self.sender is None:
            raise RuntimeError("client sender is not attached")
        return engine


async def run_client(
    config: ClientConfig,
    snapshot_callback: Callable[[UiSnapshot], None] | None = None,
) -> None:
    from dfba_client.transport import WebSocketTransport

    client = GatewayClient(config)
    await client.initialize()
    transport = WebSocketTransport(config.ws_url, client)
    if snapshot_callback is not None:
        transport.snapshot_callback = snapshot_callback
    await transport.run_forever()


def _validate_configured_feeds(feed_ids: tuple[int, ...], markets: dict[int, MarketInfo]) -> None:
    for feed_id in feed_ids:
        market = markets.get(feed_id)
        if market is None:
            raise RuntimeError(f"configured feed_id {feed_id} missing from symbology")
        if market.status != "active":
            raise RuntimeError(f"configured feed_id {feed_id} is not active in symbology")


def _reject_auth_challenge(engine: IngestionEngine, auth_status: str, error: str) -> None:
    engine.auth_status = auth_status
    engine.account_status = "blocked"
    engine.last_error = error
    engine.last_notice = "wallet authentication failed"
    engine.phase = SessionPhase.FAILED


def _received_message_type(message: GatewayMessage) -> str | None:
    if isinstance(message, PongMessage | TradeMessage | BookSnapshotMessage | BookDeltaMessage | StreamResetMessage):
        return None
    if isinstance(message, UnknownMessage):
        return None if message.message_type == "BatchCommit" else message.message_type
    if isinstance(message, CreateVaultStatusMessage | FillSettlementMessage):
        return message.message_type
    if isinstance(message, AckMessage):
        return "ack"
    if isinstance(message, AuthChallengeMessage):
        return "auth_challenge"
    if isinstance(message, AuthExpiredMessage):
        return "auth_expired"
    if isinstance(message, SessionPreemptedMessage):
        return "session_preempted"
    if isinstance(message, AccountSnapshotMessage):
        return "AccountSnapshot"
    if isinstance(message, AccountSnapshotErrorMessage):
        return "AccountSnapshotError"
    if isinstance(message, OrderAcceptedMessage):
        return "OrderAccepted"
    if isinstance(message, OrderRejectMessage | LocalOrderRejectMessage):
        return "OrderReject"
    if isinstance(message, FillMessage):
        return "Fill"
    if isinstance(message, AccountFundingUpdateMessage):
        return "AccountFundingUpdate"
    if isinstance(message, FundingIndexMessage):
        return "FundingIndex"
    if isinstance(message, CancelAckMessage):
        return "CancelAck"
    if isinstance(message, CancelRejectMessage):
        return "CancelReject"
    if isinstance(message, ModifyAckMessage):
        return "ModifyAck"
    if isinstance(message, ModifyRejectMessage):
        return "ModifyReject"
    return None
