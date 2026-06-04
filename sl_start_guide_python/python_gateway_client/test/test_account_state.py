from __future__ import annotations

import unittest

from dfba_client.account_state import AccountState
from dfba_client.messages import (
    AccountSnapshotMessage,
    AccountSubaccountMessage,
    FillSettlementMessage,
    ModifyRejectMessage,
)
from dfba_client.types import AccountSubaccountRow, FilledOrderRow, MarketInfo, PositionRow


class AccountStateTest(unittest.TestCase):
    def test_fill_busted_reverses_original_fill_side(self) -> None:
        state = AccountState(has_snapshot=True, last_seq=1)
        subaccount = AccountSubaccountRow(1)
        subaccount.positions.append(PositionRow(7, 10, 0, 0, 0, 0))
        subaccount.filled_orders.append(FilledOrderRow(88, 99, 7, "BID", 100, 10, 3, 7, 0))
        state.subaccounts[1] = subaccount

        ok, notice, error = state.apply_fill_busted(
            FillSettlementMessage(
                message_type="FillBusted",
                seq=2,
                subaccount_id=1,
                external_order_id=88,
                symbol=7,
                side="BID",
                price=100,
                qty=3,
                fee=0,
            ),
            MarketInfo(7, "BTC", "USDC", 0, 0),
        )

        self.assertTrue(ok)
        self.assertEqual(notice, "fill busted oid=88 qty=3")
        self.assertEqual(error, "")
        self.assertEqual(subaccount.positions[0].net_qty, 7)
        self.assertEqual(subaccount.filled_orders, [])

    def test_fill_settled_updates_last_price(self) -> None:
        state = AccountState(has_snapshot=True, last_seq=1)
        subaccount = AccountSubaccountRow(1)
        subaccount.filled_orders.append(FilledOrderRow(88, 99, 7, "BID", 100, 10, 3, 7, 0))
        state.subaccounts[1] = subaccount

        ok, notice, error = state.apply_fill_settled(
            FillSettlementMessage(
                message_type="FillSettled",
                seq=2,
                subaccount_id=1,
                external_order_id=88,
                symbol=7,
                side="BID",
                price=105,
                qty=2,
                fee=0,
            )
        )

        self.assertTrue(ok)
        self.assertEqual(notice, "fill settled oid=88 qty=2")
        self.assertEqual(error, "")
        self.assertEqual(subaccount.filled_orders[0].last_price, 105)

    def test_modify_reject_notice_matches_cpp(self) -> None:
        state = AccountState(has_snapshot=True, last_seq=1)

        notice = state.apply_modify_reject(
            ModifyRejectMessage(
                seq=2,
                subaccount_id=1,
                external_order_id=88,
                client_instruction_id=99,
                reason=7,
            )
        )

        self.assertEqual(notice, "modify rejected oid=88 reason=7")

    def test_snapshot_preserves_collateral_balance(self) -> None:
        state = AccountState()

        notice = state.apply_snapshot(
            AccountSnapshotMessage(
                last_seq=7,
                subaccounts=(
                    AccountSubaccountMessage(
                        subaccount_id=1,
                        collateral_balance=123456789,
                        positions=(),
                        working_orders=(),
                        filled_orders=(),
                    ),
                ),
            )
        )

        self.assertEqual(notice, "account snapshot seq=7")
        self.assertEqual(state.subaccounts[1].collateral_balance, 123456789)


if __name__ == "__main__":
    unittest.main()
