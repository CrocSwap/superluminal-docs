from __future__ import annotations

import unittest

from dfba_client.symbology import derive_symbology_url, parse_symbology_snapshot


class SymbologyTest(unittest.TestCase):
    def test_derive_symbology_url(self) -> None:
        self.assertEqual(
            derive_symbology_url("wss://example.com:8443/ws"),
            "https://example.com:8443/v1/symbology",
        )

    def test_parse_symbology_snapshot(self) -> None:
        snapshot = parse_symbology_snapshot(
            '{"version":123,"markets":[{"id":7,"symbol":"BTC","quote":"USDC",'
            '"px_exponent":-8,"size_decimals":5,"status":"active"}]}'
        )
        self.assertEqual(snapshot.version, 123)
        self.assertEqual(snapshot.markets[7].symbol, "BTC")

    def test_parse_symbology_accepts_uint32_market_id(self) -> None:
        snapshot = parse_symbology_snapshot(
            '{"version":123,"markets":[{"id":65536,"symbol":"BTC","quote":"USDC",'
            '"px_exponent":-8,"size_decimals":5,"status":"active"}]}'
        )
        self.assertEqual(snapshot.markets[65536].symbol, "BTC")

    def test_parse_symbology_rejects_out_of_int16_exponent(self) -> None:
        with self.assertRaisesRegex(ValueError, "px_exponent"):
            parse_symbology_snapshot(
                '{"version":123,"markets":[{"id":7,"symbol":"BTC","quote":"USDC",'
                '"px_exponent":32768,"size_decimals":5,"status":"active"}]}'
            )


if __name__ == "__main__":
    unittest.main()
