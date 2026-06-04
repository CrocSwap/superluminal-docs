from __future__ import annotations

import base64
import struct
import unittest
from unittest.mock import patch

import base58
from nacl.signing import SigningKey

from dfba_client.signer import (
    CREATE_VAULT_PROOF_BYTES,
    MODIFY_PROOF_BYTES,
    ORDER_PROOF_BYTES,
    Keypair,
    build_signed_create_vault_proof_base64,
    build_signed_modify_proof_base64,
    build_signed_order_proof_base64,
    sign_transaction_base64,
)
from dfba_client.types import DEFAULT_PARTITION_ID, OrderDraft, OrderMode, OrderRole


class SignerTest(unittest.TestCase):
    def test_signed_order_proof_wire_layout(self) -> None:
        keypair = Keypair(SigningKey(bytes(range(1, 33))))
        draft = OrderDraft(
            symbol=7,
            mode=OrderMode.GTC_LIMIT,
            role=OrderRole.MAKER,
            is_sell=False,
            qty=1000,
            price=5000000,
        )
        with patch("dfba_client.signer.time.time", return_value=1_710_000_000):
            proof = base64.b64decode(
                build_signed_order_proof_base64(keypair, DEFAULT_PARTITION_ID, 9, 77, draft)
            )
        self.assertEqual(len(proof), ORDER_PROOF_BYTES)
        self.assertEqual(proof[:32], keypair.public_key)
        self.assertEqual(struct.unpack_from("<H", proof, 32)[0], DEFAULT_PARTITION_ID)
        self.assertEqual(struct.unpack_from("<Q", proof, 34)[0], 9)
        self.assertEqual(struct.unpack_from("<Q", proof, 42)[0], 1_710_000_000)
        self.assertEqual(struct.unpack_from("<I", proof, 58)[0], 7)
        self.assertEqual(struct.unpack_from("<Q", proof, 62)[0], 5000000)
        self.assertEqual(struct.unpack_from("<Q", proof, 70)[0], 1000)
        self.assertEqual(proof[78], 1)
        self.assertEqual(proof[79], 0)
        self.assertEqual(proof[81], 0)
        self.assertEqual(proof[86], 0)
        self.assertEqual(struct.unpack_from("<Q", proof, 87)[0], 77)

    def test_signed_create_vault_proof_wire_layout(self) -> None:
        keypair = Keypair(SigningKey(bytes(range(1, 33))))
        with patch("dfba_client.signer.time.time", return_value=1_710_000_000):
            proof = base64.b64decode(
                build_signed_create_vault_proof_base64(
                    keypair,
                    DEFAULT_PARTITION_ID,
                    1,
                    9,
                    2,
                    1_000_000,
                    100_000,
                    77,
                )
        )
        self.assertEqual(len(proof), CREATE_VAULT_PROOF_BYTES)
        self.assertEqual(struct.unpack_from("<Q", proof, 34)[0], 1)
        self.assertEqual(struct.unpack_from("<Q", proof, 50)[0], 1_710_000_300)
        self.assertEqual(struct.unpack_from("<I", proof, 58)[0], 9)
        self.assertEqual(struct.unpack_from("<I", proof, 62)[0], 0)
        self.assertEqual(struct.unpack_from("<Q", proof, 66)[0], 2)
        self.assertEqual(struct.unpack_from("<Q", proof, 74)[0], 1_000_000)
        self.assertEqual(struct.unpack_from("<Q", proof, 82)[0], 100_000)
        self.assertEqual(struct.unpack_from("<Q", proof, 90)[0], 77)

    def test_signed_create_vault_rejects_invalid_fields_like_cpp(self) -> None:
        keypair = Keypair(SigningKey(bytes(range(1, 33))))
        invalid_cases = (
            (1, 9, 0, 1_000_000, 100_000, 77),
            (1, 9, 1, 1_000_000, 100_000, 77),
            (1, 9, 2, 0, 100_000, 77),
            (1, 9, 2, 1_000_000, 100_000, 0),
        )
        for source_subaccount_id, vault_id, operation_subaccount_id, collateral, fee, request_id in invalid_cases:
            with self.subTest(source_subaccount_id=source_subaccount_id, request_id=request_id):
                with self.assertRaisesRegex(ValueError, "invalid create vault proof fields"):
                    build_signed_create_vault_proof_base64(
                        keypair,
                        DEFAULT_PARTITION_ID,
                        source_subaccount_id,
                        vault_id,
                        operation_subaccount_id,
                        collateral,
                        fee,
                        request_id,
                    )

    def test_signed_modify_proof_matches_cpp_layout(self) -> None:
        keypair = Keypair(SigningKey(bytes(range(1, 33))))
        with patch("dfba_client.signer.time.time", return_value=1_710_000_000):
            proof = base64.b64decode(
                build_signed_modify_proof_base64(
                    keypair,
                    DEFAULT_PARTITION_ID,
                    1,
                    99,
                    77,
                    7,
                    OrderRole.MAKER,
                    False,
                    12345,
                    50,
                    -7,
                )
            )
        self.assertEqual(len(proof), MODIFY_PROOF_BYTES)
        self.assertEqual(struct.unpack_from("<Q", proof, 58)[0], 99)
        self.assertEqual(struct.unpack_from("<Q", proof, 66)[0], 77)
        self.assertEqual(struct.unpack_from("<I", proof, 74)[0], 7)
        self.assertEqual(proof[78], 0)
        self.assertEqual(proof[79], 0)
        self.assertEqual(proof[80], 1)
        self.assertEqual(proof[81], 1)
        self.assertEqual(proof[82], 0)
        self.assertEqual(struct.unpack_from("<Q", proof, 83)[0], 12345)
        self.assertEqual(struct.unpack_from("<Q", proof, 91)[0], 50)
        self.assertEqual(struct.unpack_from("<i", proof, 99)[0], -7)

    def test_sign_transaction_rejects_invalid_base64_like_cpp(self) -> None:
        keypair = Keypair(SigningKey(bytes(range(1, 33))))
        wallet_pubkey = base58.b58encode(keypair.public_key).decode("ascii")

        with self.assertRaisesRegex(ValueError, "invalid base64 payload"):
            sign_transaction_base64("AA!!", wallet_pubkey, keypair)


if __name__ == "__main__":
    unittest.main()
