from __future__ import annotations

from dataclasses import dataclass, field, replace

from dfba_client.messages import (
    AccountFundingUpdateMessage,
    AccountSnapshotMessage,
    CancelAckMessage,
    CancelRejectMessage,
    FillMessage,
    FillSettlementMessage,
    ModifyAckMessage,
    ModifyRejectMessage,
    OrderAcceptedMessage,
    OrderRejectMessage,
)
from dfba_client.types import (
    INT64_MAX,
    AccountSubaccountRow,
    FilledOrderRow,
    FillRow,
    MarketInfo,
    OrderRole,
    PositionRow,
    RunningAvg,
    UiSnapshot,
    WorkingOrderRow,
)

MAX_RECENT_FILLS = 20


@dataclass(slots=True)
class AccountState:
    has_snapshot: bool = False
    last_seq: int = 0
    seq_match_latency_avg: RunningAvg = field(default_factory=RunningAvg)
    subaccounts: dict[int, AccountSubaccountRow] = field(default_factory=dict)
    recent_fills: list[FillRow] = field(default_factory=list)

    def reset(self) -> None:
        self.has_snapshot = False
        self.last_seq = 0
        self.seq_match_latency_avg.reset()
        self.subaccounts.clear()
        self.recent_fills.clear()

    def populate_snapshot(self, snapshot: UiSnapshot) -> UiSnapshot:
        return UiSnapshot(
            wallet_pubkey=snapshot.wallet_pubkey,
            ws_url=snapshot.ws_url,
            connection_status=snapshot.connection_status,
            auth_status=snapshot.auth_status,
            account_status=snapshot.account_status,
            last_notice=snapshot.last_notice,
            last_error=snapshot.last_error,
            books=snapshot.books,
            gateway_rtt_avg_ns=snapshot.gateway_rtt_avg_ns,
            gateway_rtt_samples=snapshot.gateway_rtt_samples,
            account_stream_started=snapshot.account_stream_started,
            has_account_snapshot=self.has_snapshot,
            account_last_seq=self.last_seq,
            seq_match_latency_avg_ns=self.seq_match_latency_avg.mean,
            seq_match_latency_samples=self.seq_match_latency_avg.sample_count,
            subaccounts=tuple(row.frozen_copy() for row in self.subaccounts.values()),
            recent_fills=tuple(replace(row) for row in self.recent_fills),
            trade_lines=snapshot.trade_lines,
            log_lines=snapshot.log_lines,
        )

    def find_working_order_cancel_info(
        self,
        subaccount_id: int,
        external_order_id: int,
    ) -> tuple[int, OrderRole] | None:
        subaccount = self.subaccounts.get(subaccount_id)
        if subaccount is None:
            return None
        for order in subaccount.working_orders:
            if order.external_order_id == external_order_id:
                role = OrderRole.TAKER if order.role == "TAKER" else OrderRole.MAKER
                return order.symbol, role
        return None

    def apply_snapshot(self, message: AccountSnapshotMessage) -> str:
        if self._should_ignore_snapshot(message.last_seq):
            return f"ignored stale account snapshot seq={message.last_seq}"
        next_subaccounts: dict[int, AccountSubaccountRow] = {}
        for source in message.subaccounts:
            row = AccountSubaccountRow(source.subaccount_id, source.collateral_balance)
            for position in source.positions:
                row.positions.append(
                    PositionRow(
                        position.symbol,
                        position.net_qty,
                        position.cost_basis,
                        position.pending_fees,
                        position.funding_accrued,
                        position.last_funding_index_e8,
                    )
                )
            for working_order in source.working_orders:
                row.working_orders.append(
                    WorkingOrderRow(
                        working_order.external_order_id,
                        working_order.client_instruction_id,
                        working_order.symbol,
                        working_order.side,
                        working_order.role,
                        working_order.tif,
                        working_order.price,
                        working_order.original_qty,
                        working_order.remaining_qty,
                    )
                )
            for filled_order in source.filled_orders:
                row.filled_orders.append(
                    FilledOrderRow(
                        filled_order.external_order_id,
                        filled_order.client_instruction_id,
                        filled_order.symbol,
                        filled_order.side,
                        filled_order.last_price,
                        filled_order.filled_qty,
                        filled_order.pending_settlement_qty,
                        filled_order.settled_qty,
                        filled_order.busted_qty,
                    )
                )
            _sort_subaccount(row)
            next_subaccounts[row.subaccount_id] = row
        self.subaccounts = next_subaccounts
        for fill in self.recent_fills:
            subaccount = self.subaccounts.get(fill.subaccount_id)
            _apply_recent_fill_order_aggregate(fill, _find_filled_order(subaccount, fill.external_order_id))
        self.has_snapshot = True
        self.last_seq = message.last_seq
        return f"account snapshot seq={message.last_seq}"

    def apply_order_accepted(self, message: OrderAcceptedMessage) -> str:
        if self._should_ignore_event(message.seq):
            return f"ignored stale order accepted seq={message.seq}"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        subaccount = self._subaccount(message.subaccount_id)
        replacement = WorkingOrderRow(
            message.external_order_id,
            message.client_instruction_id,
            message.symbol,
            message.side,
            message.role,
            message.tif,
            message.price,
            message.qty,
            message.qty,
        )
        replaced = False
        for index, order in enumerate(subaccount.working_orders):
            if order.external_order_id == message.external_order_id:
                subaccount.working_orders[index] = replacement
                replaced = True
                break
        if not replaced:
            subaccount.working_orders.append(replacement)
        _sort_subaccount(subaccount)
        self._advance_seq(message.seq)
        return f"order accepted oid={message.external_order_id}"

    def apply_order_reject(self, message: OrderRejectMessage) -> str:
        if self._should_ignore_event(message.seq):
            return f"ignored stale order reject seq={message.seq}"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        subaccount = self._subaccount(message.subaccount_id)
        subaccount.working_orders = [
            order for order in subaccount.working_orders if order.external_order_id != message.external_order_id
        ]
        self._advance_seq(message.seq)
        return (
            f"order rejected oid={message.external_order_id} "
            f"reason={_order_reject_reason_name(message.reason)} code={message.reason}"
        )

    def apply_fill(self, message: FillMessage, market: MarketInfo | None) -> tuple[bool, str, str]:
        if self._should_ignore_event(message.seq):
            self._append_recent_fill(message)
            subaccount = self.subaccounts.get(message.subaccount_id)
            _apply_recent_fill_order_aggregate(
                self.recent_fills[0],
                _find_filled_order(subaccount, message.external_order_id),
            )
            return True, f"bootstrapped fill seq={message.seq}", ""
        if message.qty > INT64_MAX:
            return False, "account stream update failed", "fill qty too large"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        subaccount = self._subaccount(message.subaccount_id)
        try:
            error = _apply_position_delta(
                subaccount,
                message.symbol,
                message.side,
                message.qty,
                message.price,
                message.fee or 0,
                market,
                False,
            )
        except OverflowError as exc:
            error = str(exc)
        if error != "":
            return False, "account stream update failed", error
        for index, order in enumerate(subaccount.working_orders):
            if order.external_order_id == message.external_order_id:
                if message.qty >= order.remaining_qty:
                    del subaccount.working_orders[index]
                else:
                    order.remaining_qty -= message.qty
                break
        filled_order = _find_filled_order(subaccount, message.external_order_id)
        if filled_order is None:
            subaccount.filled_orders.append(
                FilledOrderRow(
                    message.external_order_id,
                    message.client_instruction_id,
                    message.symbol,
                    message.side,
                    message.price,
                    message.qty,
                    message.qty,
                    0,
                    0,
                )
            )
        else:
            if filled_order.client_instruction_id == 0:
                filled_order.client_instruction_id = message.client_instruction_id
            if filled_order.symbol == 0:
                filled_order.symbol = message.symbol
            if filled_order.side == "":
                filled_order.side = message.side
            filled_order.last_price = message.price
            filled_order.filled_qty += message.qty
            filled_order.pending_settlement_qty += message.qty
        _sort_subaccount(subaccount)
        self._append_recent_fill(message)
        _apply_recent_fill_order_aggregate(
            self.recent_fills[0],
            _find_filled_order(subaccount, message.external_order_id),
        )
        self._advance_seq(message.seq)
        return True, f"fill trade={message.trade_id} qty={message.qty}", ""

    def apply_fill_settled(self, message: FillSettlementMessage) -> tuple[bool, str, str]:
        if self._should_ignore_event(message.seq):
            return True, f"ignored stale fill settlement seq={message.seq}", ""
        subaccount = self._subaccount(message.subaccount_id)
        filled_order = _find_filled_order(subaccount, message.external_order_id)
        if filled_order is None:
            return False, "account stream update failed", "fill settlement missing filled order"
        if message.qty > filled_order.pending_settlement_qty:
            return False, "account stream update failed", "fill settlement exceeds pending qty"
        filled_order.pending_settlement_qty -= message.qty
        filled_order.settled_qty += message.qty
        filled_order.last_price = message.price
        _mark_recent_fill_settled(self.recent_fills, message)
        _prune_reconciled_filled_order(subaccount, message.external_order_id)
        self._advance_seq(message.seq)
        return True, f"fill settled oid={message.external_order_id} qty={message.qty}", ""

    def apply_fill_busted(self, message: FillSettlementMessage, market: MarketInfo | None) -> tuple[bool, str, str]:
        if self._should_ignore_event(message.seq):
            return True, f"ignored stale fill busted seq={message.seq}", ""
        subaccount = self._subaccount(message.subaccount_id)
        filled_order = _find_filled_order(subaccount, message.external_order_id)
        if filled_order is None:
            return False, "account stream update failed", "fill bust missing filled order"
        if message.qty > filled_order.pending_settlement_qty:
            return False, "account stream update failed", "fill bust exceeds pending qty"
        reverse_side = "BID" if message.side == "ASK" else "ASK"
        try:
            error = _apply_position_delta(
                subaccount,
                message.symbol,
                reverse_side,
                message.qty,
                message.price,
                message.fee or 0,
                market,
                True,
            )
        except OverflowError as exc:
            error = str(exc)
        if error != "":
            return False, "account stream update failed", error
        filled_order.pending_settlement_qty -= message.qty
        filled_order.busted_qty += message.qty
        filled_order.last_price = message.price
        _mark_recent_fill_settled(self.recent_fills, message)
        _prune_reconciled_filled_order(subaccount, message.external_order_id)
        self._advance_seq(message.seq)
        return True, f"fill busted oid={message.external_order_id} qty={message.qty}", ""

    def apply_account_funding_update(self, message: AccountFundingUpdateMessage) -> tuple[bool, str, str]:
        if message.seq == 0 or message.market_id == 0 or message.last_funding_index_e8 == message.prev_funding_index_e8:
            return False, "account stream update failed", "malformed funding update event"
        if self._should_ignore_event(message.seq):
            return True, f"ignored stale funding update seq={message.seq}", ""
        subaccount = self._subaccount(message.subaccount_id)
        symbol = message.symbol if message.symbol != 0 else message.market_id
        for position in subaccount.positions:
            if position.symbol == symbol:
                position.funding_accrued = message.funding_accrued
                position.last_funding_index_e8 = message.last_funding_index_e8
                self._advance_seq(message.seq)
                return True, f"funding update sym={message.market_id} delta={message.funding_delta}", ""
        subaccount.positions.append(
            PositionRow(
                symbol,
                0,
                0,
                0,
                message.funding_accrued,
                message.last_funding_index_e8,
            )
        )
        _sort_subaccount(subaccount)
        self._advance_seq(message.seq)
        return True, f"funding update sym={message.market_id} delta={message.funding_delta}", ""

    def apply_cancel_ack(self, message: CancelAckMessage) -> str:
        if self._should_ignore_event(message.seq):
            return f"ignored stale cancel ack seq={message.seq}"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        subaccount = self._subaccount(message.subaccount_id)
        subaccount.working_orders = [
            order for order in subaccount.working_orders if order.external_order_id != message.external_order_id
        ]
        self._advance_seq(message.seq)
        return f"cancel ack oid={message.external_order_id}"

    def apply_cancel_reject(self, message: CancelRejectMessage) -> str:
        if self._should_ignore_event(message.seq):
            return f"ignored stale cancel reject seq={message.seq}"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        self._advance_seq(message.seq)
        return (
            f"cancel rejected oid={message.external_order_id} "
            f"reason={_cancel_reject_reason_name(message.reason)} code={message.reason}"
        )

    def apply_modify_ack(self, message: ModifyAckMessage) -> str:
        if self._should_ignore_event(message.seq):
            return f"ignored stale modify ack seq={message.seq}"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        subaccount = self._subaccount(message.subaccount_id)
        for order in subaccount.working_orders:
            if order.external_order_id == message.external_order_id:
                order.price = message.price
                order.remaining_qty = message.qty
                break
        self._advance_seq(message.seq)
        return f"modify ack oid={message.external_order_id}"

    def apply_modify_reject(self, message: ModifyRejectMessage) -> str:
        if self._should_ignore_event(message.seq):
            return f"ignored stale modify reject seq={message.seq}"
        self._record_seq_match_latency(message.matcher_unix_ns, message.sequencer_recv_unix_ns)
        self._advance_seq(message.seq)
        return f"modify rejected oid={message.external_order_id} reason={message.reason}"

    def _append_recent_fill(self, message: FillMessage) -> None:
        if message.seq != 0 and any(fill.seq == message.seq for fill in self.recent_fills):
            return
        self.recent_fills.insert(
            0,
            FillRow(
                seq=message.seq,
                trade_id=message.trade_id,
                subaccount_id=message.subaccount_id,
                client_instruction_id=message.client_instruction_id,
                external_order_id=message.external_order_id,
                symbol=message.symbol,
                side=message.side,
                role=message.role,
                price=message.price,
                qty=message.qty,
                event_unix_ns=message.gateway_send_unix_ns or 0,
                has_event_unix_ns=message.gateway_send_unix_ns is not None,
            ),
        )
        if len(self.recent_fills) > MAX_RECENT_FILLS:
            del self.recent_fills[MAX_RECENT_FILLS:]

    def _subaccount(self, subaccount_id: int) -> AccountSubaccountRow:
        row = self.subaccounts.get(subaccount_id)
        if row is None:
            row = AccountSubaccountRow(subaccount_id)
            self.subaccounts[subaccount_id] = row
        return row

    def _record_seq_match_latency(self, matcher_unix_ns: int | None, sequencer_recv_unix_ns: int | None) -> None:
        if matcher_unix_ns is not None and sequencer_recv_unix_ns is not None and matcher_unix_ns >= sequencer_recv_unix_ns:
            self.seq_match_latency_avg.add_sample(float(matcher_unix_ns - sequencer_recv_unix_ns))

    def _should_ignore_snapshot(self, seq: int) -> bool:
        return self.last_seq != 0 and seq <= self.last_seq

    def _should_ignore_event(self, seq: int) -> bool:
        return seq != 0 and seq <= self.last_seq

    def _advance_seq(self, seq: int) -> None:
        if seq > self.last_seq:
            self.last_seq = seq


def _sort_subaccount(row: AccountSubaccountRow) -> None:
    row.positions.sort(key=lambda position: position.symbol)
    row.working_orders.sort(key=lambda order: order.external_order_id)
    row.filled_orders.sort(key=lambda order: order.external_order_id)


def _order_reject_reason_name(reason: int) -> str:
    if reason == 1:
        return "missing_symbology"
    if reason == 2:
        return "full"
    if reason == 3:
        return "risk_rejected"
    return "unknown"


def _cancel_reject_reason_name(reason: int) -> str:
    if reason == 1:
        return "unknown_order"
    if reason == 2:
        return "ioc_expired"
    if reason == 3:
        return "missing_symbology"
    return "unknown"


def _find_filled_order(subaccount: AccountSubaccountRow | None, external_order_id: int) -> FilledOrderRow | None:
    if subaccount is None:
        return None
    for row in subaccount.filled_orders:
        if row.external_order_id == external_order_id:
            return row
    return None


def _apply_recent_fill_order_aggregate(fill: FillRow, filled_order: FilledOrderRow | None) -> None:
    if filled_order is None:
        fill.order_settled_qty = 0
        fill.has_order_settled_qty = False
        return
    fill.order_settled_qty = filled_order.settled_qty
    fill.has_order_settled_qty = True
    if (
        not fill.settled
        and filled_order.filled_qty == fill.qty
        and filled_order.settled_qty >= fill.qty
        and filled_order.busted_qty == 0
    ):
        fill.settled = True


def _apply_position_delta(
    subaccount: AccountSubaccountRow,
    symbol: int,
    side: str,
    qty: int,
    price: int,
    fee: int,
    market: MarketInfo | None,
    reverse_value: bool,
) -> str:
    if qty > INT64_MAX:
        return "fill qty too large"
    is_ask = side == "ASK"
    signed_qty = int(qty)
    signed_position_qty = -signed_qty if is_ask else signed_qty
    cost_basis_delta = _cost_basis_delta_usd(market, price, signed_position_qty)
    fee_delta = -fee if reverse_value else fee
    for position in subaccount.positions:
        if position.symbol == symbol:
            position.net_qty += -signed_qty if is_ask else signed_qty
            position.cost_basis += cost_basis_delta
            position.pending_fees += fee_delta
            _sort_subaccount(subaccount)
            return ""
    subaccount.positions.append(
        PositionRow(
            symbol=symbol,
            net_qty=signed_position_qty,
            cost_basis=cost_basis_delta,
            pending_fees=fee_delta,
            funding_accrued=0,
            last_funding_index_e8=0,
        )
    )
    _sort_subaccount(subaccount)
    return ""


def _cost_basis_delta_usd(market: MarketInfo | None, price: int, signed_qty: int) -> int:
    if market is None:
        return 0
    scaled = price * signed_qty
    exponent = market.px_exponent - market.size_decimals
    if exponent > -6:
        scaled *= 10 ** (exponent + 6)
    elif exponent < -6:
        scaled //= 10 ** (-6 - exponent)
    if scaled < -INT64_MAX - 1 or scaled > INT64_MAX:
        raise OverflowError("position cost basis overflow")
    return scaled


def _mark_recent_fill_settled(recent_fills: list[FillRow], message: FillSettlementMessage) -> None:
    for fill in reversed(recent_fills):
        if (
            not fill.settled
            and fill.subaccount_id == message.subaccount_id
            and fill.external_order_id == message.external_order_id
            and fill.symbol == message.symbol
            and fill.side == message.side
            and fill.qty == message.qty
            and (message.price == 0 or fill.price == 0 or fill.price == message.price)
        ):
            fill.settled = True
            if message.gateway_send_unix_ns is not None and fill.has_event_unix_ns:
                fill.settle_delta_ns = max(0, message.gateway_send_unix_ns - fill.event_unix_ns)
                fill.has_settle_delta_ns = True
            return


def _prune_reconciled_filled_order(subaccount: AccountSubaccountRow, external_order_id: int) -> None:
    subaccount.filled_orders = [
        row
        for row in subaccount.filled_orders
        if row.external_order_id != external_order_id or row.pending_settlement_qty != 0
    ]
