from __future__ import annotations

import unittest

from dfba_client.codec import decode_gateway_message
from dfba_client.commands import encode_signed_create_vault
from dfba_client.messages import (
    AccountFundingUpdateMessage,
    AccountSnapshotErrorMessage,
    AccountSnapshotMessage,
    AckMessage,
    BookSnapshotMessage,
    CreateVaultStatusMessage,
    ModifyAckMessage,
    OrderRejectMessage,
    UnknownMessage,
)
from dfba_client.types import BookType, DecodeError


class CodecTest(unittest.TestCase):
    def test_ack_decoder_reads_reason_and_order_ids(self) -> None:
        msg = decode_gateway_message(
            '{"type":"ack","status":"rejected","reason":"risk_rejected",'
            '"client_instruction_id":12,"external_order_id":34}'
        )
        self.assertIsInstance(msg, AckMessage)
        assert isinstance(msg, AckMessage)
        self.assertEqual(msg.status, "rejected")
        self.assertEqual(msg.reason, "risk_rejected")
        self.assertEqual(msg.client_instruction_id, 12)
        self.assertEqual(msg.external_order_id, 34)

    def test_signed_create_vault_encoder_uses_live_gateway_command(self) -> None:
        self.assertEqual(encode_signed_create_vault("abc"), '{"type":"signed_create_vault","msg_b64":"abc"}')

    def test_create_vault_status_decoder(self) -> None:
        msg = decode_gateway_message(
            '{"type":"CreateVaultStatus","client_instruction_id":77,'
            '"source_subaccount_id":1,"operation_subaccount_id":2,'
            '"vault_id":3,"status":0,"error_code":0,"total_vault_shares":123456}'
        )
        self.assertIsInstance(msg, CreateVaultStatusMessage)
        assert isinstance(msg, CreateVaultStatusMessage)
        self.assertEqual(msg.vault_id, 3)
        self.assertEqual(msg.total_vault_shares, 123456)

    def test_account_snapshot_decoder_reads_position_cost_basis(self) -> None:
        msg = decode_gateway_message(
            '{"type":"AccountSnapshot","last_seq":77,"subaccounts":[{"subaccount_id":7,'
            '"collateral_balance":123456789,'
            '"positions":[{"symbol":1,"net_qty":10,"cost_basis":1234560,"pending_fees":-4,'
            '"funding_accrued":-25,"last_funding_index_e8":25000000}],'
            '"working_orders":[],"filled_orders":[]}],'
            '"vault_accounts":[{"vault_id":3,"operation_subaccount_id":7,'
            '"leader_source_subaccount_id":0,"collateral_amount":123456789,'
            '"total_vault_shares":120000000,"last_seq":76}]}'
        )
        self.assertIsInstance(msg, AccountSnapshotMessage)
        assert isinstance(msg, AccountSnapshotMessage)
        self.assertEqual(msg.last_seq, 77)
        self.assertEqual(msg.subaccounts[0].collateral_balance, 123456789)
        self.assertEqual(msg.subaccounts[0].positions[0].cost_basis, 1234560)
        self.assertEqual(len(msg.vault_accounts), 1)
        self.assertEqual(msg.vault_accounts[0].vault_id, 3)
        self.assertEqual(msg.vault_accounts[0].operation_subaccount_id, 7)

    def test_book_snapshot_decoder(self) -> None:
        msg = decode_gateway_message(
            '{"type":"BookSnapshot","seq":101,"bookType":"maker","id":7,"symbol":"BTC",'
            '"book_cursor":5000,"matcher_unix_ns":171,"bids":[{"price":100,"qty":5}],'
            '"asks":[{"price":101,"qty":3}]}'
        )
        self.assertIsInstance(msg, BookSnapshotMessage)
        assert isinstance(msg, BookSnapshotMessage)
        self.assertEqual(msg.book_type, BookType.MAKER)
        self.assertEqual(msg.bids[0].price, 100)

    def test_order_reject_decoder_reads_notifier_event(self) -> None:
        msg = decode_gateway_message(
            '{"seq":51,"type":"OrderReject","reason":3,"subaccount_id":7,'
            '"external_order_id":88,"client_instruction_id":99,"symbol":1,'
            '"side":"BID","role":"TAKER","tif":"IOC","order_type":1,'
            '"price":12345,"qty":50,"delta_ppm":-7}'
        )
        self.assertIsInstance(msg, OrderRejectMessage)
        assert isinstance(msg, OrderRejectMessage)
        self.assertEqual(msg.reason, 3)
        self.assertEqual(msg.delta_ppm, -7)

    def test_optional_event_symbol_range_accepts_uint32(self) -> None:
        order_reject = decode_gateway_message(
            '{"seq":51,"type":"OrderReject","reason":3,"subaccount_id":7,'
            '"external_order_id":88,"symbol":65536}'
        )
        modify_ack = decode_gateway_message(
            '{"seq":52,"type":"ModifyAck","subaccount_id":7,'
            '"external_order_id":88,"symbol":65536,"qty":10}'
        )
        self.assertIsInstance(order_reject, OrderRejectMessage)
        self.assertIsInstance(modify_ack, ModifyAckMessage)
        assert isinstance(order_reject, OrderRejectMessage)
        assert isinstance(modify_ack, ModifyAckMessage)
        self.assertEqual(order_reject.symbol, 65536)
        self.assertEqual(modify_ack.symbol, 65536)

    def test_invalid_integer_type_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            decode_gateway_message('{"type":"pong","client_unix_ns":true}')

    def test_private_event_malformed_values_rejected_at_decode(self) -> None:
        bad_payloads = (
            '{"type":"CancelAck","seq":0,"subaccount_id":7,"external_order_id":88}',
            '{"type":"CancelReject","seq":1,"subaccount_id":7,"external_order_id":0}',
            '{"type":"ModifyAck","seq":1,"subaccount_id":7,"external_order_id":88,"qty":0}',
            '{"type":"ModifyReject","seq":0,"subaccount_id":7,"external_order_id":88}',
            '{"type":"Fill","seq":1,"subaccount_id":7,"external_order_id":88,'
            '"symbol":1,"side":"NOPE","qty":1}',
            '{"type":"FillSettled","seq":1,"subaccount_id":7,"external_order_id":88,'
            '"symbol":1,"side":"BID","qty":0}',
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(DecodeError):
                    decode_gateway_message(payload)

    def test_account_funding_update_uses_market_id_as_symbol_fallback(self) -> None:
        msg = decode_gateway_message(
            '{"type":"AccountFundingUpdate","seq":1,"subaccount_id":7,"market_id":70000,'
            '"prev_funding_index_e8":5,"last_funding_index_e8":6}'
        )
        self.assertIsInstance(msg, AccountFundingUpdateMessage)
        assert isinstance(msg, AccountFundingUpdateMessage)
        self.assertEqual(msg.market_id, 70000)
        self.assertEqual(msg.symbol, 70000)

    def test_account_funding_update_accepts_uint32_optional_symbol(self) -> None:
        msg = decode_gateway_message(
            '{"type":"AccountFundingUpdate","seq":1,"subaccount_id":7,"market_id":17,'
            '"symbol":65536,"prev_funding_index_e8":5,"last_funding_index_e8":6}'
        )
        self.assertIsInstance(msg, AccountFundingUpdateMessage)
        assert isinstance(msg, AccountFundingUpdateMessage)
        self.assertEqual(msg.market_id, 17)
        self.assertEqual(msg.symbol, 65536)

    def test_account_funding_update_decodes_malformed_semantic_event_like_cpp(self) -> None:
        msg = decode_gateway_message(
            '{"type":"AccountFundingUpdate","seq":0,"subaccount_id":7,"market_id":0,'
            '"prev_funding_index_e8":5,"last_funding_index_e8":5}'
        )
        self.assertIsInstance(msg, AccountFundingUpdateMessage)
        assert isinstance(msg, AccountFundingUpdateMessage)
        self.assertEqual(msg.seq, 0)
        self.assertEqual(msg.market_id, 0)
        self.assertEqual(msg.prev_funding_index_e8, 5)
        self.assertEqual(msg.last_funding_index_e8, 5)

    def test_account_snapshot_error_decoder(self) -> None:
        msg = decode_gateway_message(
            '{"type":"AccountSnapshotError","wallet_pubkey":"wallet","message":"bad snapshot"}'
        )
        self.assertIsInstance(msg, AccountSnapshotErrorMessage)
        assert isinstance(msg, AccountSnapshotErrorMessage)
        self.assertEqual(msg.wallet_pubkey, "wallet")
        self.assertEqual(msg.message, "bad snapshot")

    def test_unknown_decoder_preserves_raw_payload_for_ingestion_notice(self) -> None:
        raw = '{"type":"FutureMessage","field":123}'
        msg = decode_gateway_message(raw)
        self.assertIsInstance(msg, UnknownMessage)
        assert isinstance(msg, UnknownMessage)
        self.assertEqual(msg.message_type, "FutureMessage")
        self.assertEqual(msg.payload, raw)


if __name__ == "__main__":
    unittest.main()
