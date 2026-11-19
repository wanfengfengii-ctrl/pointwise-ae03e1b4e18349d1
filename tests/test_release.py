from intake import ObservationRow as Row
from intake.admission import submit
from partitions.content import ContentRef
from partitions.readback import PartitionContentMissing, PartitionReadError, read_content
from pickup import pickup
from reconcile import reconcile
from release import PendingReceipt, ReleaseCatalog, RequestConflict, UnknownRelease, receipt
from release.finalize import finalize_release
from release.readiness import ReleaseNotReady
from tests import LakeCase


class TestRelease(LakeCase):
    def _interrupt_move(self):
        first = submit(self.catalog, self.request('day', 'R0', east=[Row(1, 'r', 5)], west=[]))
        move = self.request('move', first.release_id, east=[], west=[Row(2, 'r', 7)])
        attempted = {}

        def interrupt(reference):
            attempted[reference.district] = reference
            if reference.district == 'west':
                raise InterruptedError('west commit interrupted')

        with self.assertRaisesRegex(InterruptedError, 'west commit interrupted'):
            submit(self.catalog, move, before_partition_commit=interrupt)
        self.assertEqual(tuple(attempted), ('east', 'west'))
        self.catalog = ReleaseCatalog(self.directory)
        return first, move, attempted

    def test_move_survives_interrupted_partition_commit(self):
        first, move, attempted = self._interrupt_move()
        marker = 'LAKE_PARTIAL_BATCH_VISIBLE'
        self.assertEqual(receipt(self.catalog, 'move'), PendingReceipt('move', first.release_id))
        try:
            original = pickup(self.catalog, first.release_id)
        except PartitionContentMissing as error:
            self.fail(f'{marker}: old signed content was lost after the east commit: {error}')
        self.assertEqual(original.release_id, first.release_id, marker)
        self.assertEqual(tuple(part.rows for part in original.partitions), ((Row(1, 'r', 5),), ()), marker)
        old_account = reconcile(self.catalog, first.release_id)
        self.assertEqual([(part.row_count, part.net_reading) for part in old_account.partition_totals],
                         [(1, 5), (0, 0)], marker)
        for read in (pickup, reconcile):
            with self.assertRaises(UnknownRelease, msg=marker):
                read(self.catalog, 'R:move')

        stores = self.catalog.partition_databases()
        old_east = self.catalog.get_release(first.release_id).binding_for('east')
        self.assertEqual(read_content(stores['east'], old_east).rows, (Row(1, 'r', 5),), marker)
        prepared_east = read_content(stores['east'], attempted['east'])
        self.assertEqual((prepared_east.request_id, prepared_east.rows), ('move', ()))
        with self.assertRaises(PartitionContentMissing):
            read_content(stores['west'], attempted['west'])
        self.assertEqual(self.catalog.current_release().release_id, first.release_id)
        self.assertEqual(self.counts(), (2, 2, 1))

        committed = []
        replies = []

        def drop_reply(original_receipt):
            replies.append(original_receipt)
            raise ConnectionError('confirmation not delivered')

        with self.assertRaisesRegex(ConnectionError, 'confirmation not delivered'):
            submit(self.catalog, move, before_partition_commit=committed.append, after_release_commit=drop_reply)
        self.assertEqual(committed, [attempted['west']])
        self.assertEqual(len(replies), 1)
        reopened = ReleaseCatalog(self.directory)
        self.assertEqual(submit(reopened, move), replies[0])
        self.assertEqual(receipt(reopened, 'move'), replies[0])
        self.assertEqual(reopened.get_release('R:move').binding_for('east'), prepared_east.reference)
        changed = self.request('move', first.release_id, east=[], west=[Row(2, 'r', 8)])
        with self.assertRaises(RequestConflict):
            submit(reopened, changed)
        self.assertEqual(pickup(reopened, first.release_id), original)
        self.assertEqual(reconcile(reopened, first.release_id), old_account)
        new_detail = pickup(reopened, 'R:move')
        self.assertEqual(tuple(part.rows for part in new_detail.partitions), ((), (Row(2, 'r', 7),)))
        new_account = reconcile(reopened, 'R:move')
        self.assertEqual([(part.row_count, part.net_reading) for part in new_account.partition_totals],
                         [(0, 0), (1, 7)])
        self.assertEqual((new_account.batch_total.row_count, new_account.batch_total.net_reading), (1, 7))
        self.assertEqual((old_account.batch_total.row_count, old_account.batch_total.net_reading), (1, 5))
        self.assertEqual(self.counts(), (2, 3, 2))

    def test_ready_requires_local_commit(self):
        first, move, attempted = self._interrupt_move()
        old_west = self.catalog.get_release(first.release_id).binding_for('west')
        for proposed in (
            attempted,
            {'east': attempted['east'], 'west': old_west},
            {'east': attempted['east'], 'west': ContentRef('east', attempted['west'].content_id)},
        ):
            with self.subTest(candidate=proposed):
                with self.assertRaises(ReleaseNotReady, msg='LAKE_PARTIAL_BATCH_VISIBLE'):
                    finalize_release(self.catalog, move, proposed)
                with self.assertRaises(UnknownRelease):
                    pickup(self.catalog, 'R:move')
                self.assertEqual(receipt(self.catalog, 'move'), PendingReceipt('move', first.release_id))
                self.assertEqual(self.catalog.current_release().release_id, first.release_id)
                self.assertEqual(reconcile(self.catalog, first.release_id).batch_total.net_reading, 5)
                self.assertEqual(self.counts(), (2, 2, 1))
        signed = submit(self.catalog, move)
        self.assertEqual(receipt(self.catalog, 'move'), signed)
        self.assertEqual(reconcile(self.catalog, signed.release_id).batch_total.net_reading, 7)
        self.assertEqual(self.counts(), (2, 3, 2))

    def test_single_bound_partition_read_error_aborts_pickup_and_statement(self):
        first, move, _ = self._interrupt_move()
        signed = submit(self.catalog, move)
        old = self.catalog.get_release(first.release_id)
        stores = self.catalog.partition_databases()
        with stores['west'].write_transaction() as connection:
            deleted = connection.execute(
                'DELETE FROM contents WHERE content_id = ?', (old.binding_for('west').content_id,)
            )
            self.assertEqual(deleted.rowcount, 1)
        self.assertEqual(read_content(stores['east'], old.binding_for('east')).rows, (Row(1, 'r', 5),))
        for read in (pickup, reconcile):
            with self.subTest(read=read.__name__):
                with self.assertRaisesRegex(PartitionReadError, 'west'):
                    read(self.catalog, first.release_id)
        self.assertEqual(reconcile(self.catalog, signed.release_id).batch_total.net_reading, 7)

    def test_lost_confirmation_returns_original_receipt(self):
        request = self.request('day', 'R0', east=[Row(1, 'r', 5)])
        lost = []

        def lose_confirmation(result):
            lost.append(result)
            raise ConnectionError('reply lost after commit')

        with self.assertRaisesRegex(ConnectionError, 'reply lost after commit'):
            submit(self.catalog, request, after_release_commit=lose_confirmation)
        self.assertEqual(len(lost), 1)
        reopened = ReleaseCatalog(self.directory)
        original = receipt(reopened, 'day')
        self.assertEqual(original, lost[0])
        self.assertEqual((original.release_id, original.receipt_id), ('R:day', 'C:day'))
        self.assertEqual(submit(reopened, request), original)
        self.assertEqual(self.counts(), (1, 2, 1))

        later = submit(reopened, self.request('next', original.release_id, west=[Row(2, 's', -2)]))
        self.assertEqual(reopened.current_release().release_id, later.release_id)
        self.assertEqual(receipt(reopened, 'day'), original)
        self.assertEqual(submit(reopened, request), original)
        self.assertEqual(receipt(reopened, 'next'), later)
        self.assertEqual(reconcile(reopened, original.release_id).batch_total.net_reading, 5)
        self.assertEqual(reconcile(reopened, later.release_id).batch_total.net_reading, 3)
        self.assertEqual(self.counts(), (2, 3, 2))

    def test_catalog_rolls_back_manifest_before_receipt(self):
        original = submit(self.catalog, self.request('day', 'R0', east=[Row(1, 'r', 5)]))
        correction = self.request('next', original.release_id, west=[Row(2, 'w', -2)])
        tentative = []

        def stop_after_manifest(manifest):
            tentative.append(manifest.release_id)
            raise InterruptedError('receipt transaction interrupted')

        with self.assertRaisesRegex(InterruptedError, 'receipt transaction interrupted'):
            submit(self.catalog, correction, after_manifest=stop_after_manifest)
        self.assertEqual(tentative, ['R:next'])
        reopened = ReleaseCatalog(self.directory)
        self.assertEqual(reopened.current_release().release_id, original.release_id)
        self.assertEqual(receipt(reopened, 'day'), original)
        self.assertEqual(receipt(reopened, 'next'), PendingReceipt('next', original.release_id))
        self.assertIsNone(reopened.get_receipt_record('next'))
        with self.assertRaises(UnknownRelease):
            pickup(reopened, 'R:next')
        self.assertEqual(self.counts(), (2, 2, 1))

        commits = []
        result = submit(reopened, correction, before_partition_commit=commits.append)
        self.assertEqual(commits, [])
        self.assertEqual((result.release_id, result.receipt_id), ('R:next', 'C:next'))
        self.assertEqual(receipt(reopened, 'next'), result)
        self.assertEqual(submit(reopened, correction), result)
        self.assertEqual(self.counts(), (2, 3, 2))
