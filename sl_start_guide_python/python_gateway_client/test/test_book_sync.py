from __future__ import annotations

import unittest

from dfba_client.book_sync import BookSyncEngine, SyncActionType, WireKind, WireLevel, WireMessage
from dfba_client.types import BookType


class BookSyncTest(unittest.TestCase):
    def test_snapshot_then_matching_delta_emits(self) -> None:
        engine = BookSyncEngine()
        snapshot = WireMessage(
            kind=WireKind.SNAPSHOT,
            book_type=BookType.MAKER,
            feed_id=7,
            book_cursor=10,
            bids=(WireLevel(100, 5),),
            asks=(WireLevel(101, 3),),
        )
        action = engine.handle_message(snapshot)
        self.assertEqual(action.type, SyncActionType.EMIT)
        delta = WireMessage(
            kind=WireKind.DELTA,
            book_type=BookType.MAKER,
            feed_id=7,
            prev_book_cursor=10,
            book_cursor=11,
            bids=(WireLevel(100, 7),),
        )
        action = engine.handle_message(delta)
        self.assertEqual(action.type, SyncActionType.EMIT)
        assert action.update is not None
        self.assertEqual(action.update.levels[0].bid_qty, 7)

    def test_gap_resets_book(self) -> None:
        engine = BookSyncEngine()
        engine.handle_message(
            WireMessage(
                kind=WireKind.SNAPSHOT,
                book_type=BookType.MAKER,
                feed_id=7,
                book_cursor=10,
                bids=(WireLevel(100, 5),),
            )
        )
        action = engine.handle_message(
            WireMessage(
                kind=WireKind.DELTA,
                book_type=BookType.MAKER,
                feed_id=7,
                prev_book_cursor=9,
                book_cursor=11,
            )
        )
        self.assertEqual(action.type, SyncActionType.GAP)
        self.assertFalse(engine.book_synced)
        self.assertEqual(len(engine.book_state), 0)

    def test_full_book_emit_uses_ordered_map_descending_prices(self) -> None:
        engine = BookSyncEngine()
        action = engine.handle_message(
            WireMessage(
                kind=WireKind.SNAPSHOT,
                book_type=BookType.MAKER,
                feed_id=7,
                book_cursor=10,
                bids=(WireLevel(100, 5), WireLevel(110, 2)),
                asks=(WireLevel(120, 3),),
            )
        )
        self.assertEqual(action.type, SyncActionType.EMIT)
        assert action.update is not None
        self.assertEqual([level.price for level in action.update.levels], [120, 110, 100])

    def test_delta_emit_deduplicates_changed_prices_in_descending_order(self) -> None:
        engine = BookSyncEngine()
        engine.handle_message(
            WireMessage(
                kind=WireKind.SNAPSHOT,
                book_type=BookType.MAKER,
                feed_id=7,
                book_cursor=10,
                bids=(WireLevel(100, 5),),
                asks=(WireLevel(101, 3),),
            )
        )
        action = engine.handle_message(
            WireMessage(
                kind=WireKind.DELTA,
                book_type=BookType.MAKER,
                feed_id=7,
                prev_book_cursor=10,
                book_cursor=11,
                bids=(WireLevel(100, 7),),
                asks=(WireLevel(100, 4), WireLevel(101, 0)),
            )
        )
        self.assertEqual(action.type, SyncActionType.EMIT)
        assert action.update is not None
        self.assertEqual([level.price for level in action.update.levels], [101, 100])
        self.assertEqual(action.update.levels[1].bid_qty, 7)
        self.assertEqual(action.update.levels[1].ask_qty, 4)


if __name__ == "__main__":
    unittest.main()
