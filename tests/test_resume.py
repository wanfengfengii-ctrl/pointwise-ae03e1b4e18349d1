"""Regressions for the interrupted two-district handoff.

These cases pin down the night-shift repair:

* a district commit made for a pending request can no longer erase the rows of
  an already signed release;
* a manifest is only signed when both bindings are durably committed and can
  be read back in full;
* explicit empty replacements clear one district while the other district
  inherits the parent content unchanged;
* replays resume or return the original receipt, renamed parameters conflict,
  and later corrections never mutate earlier releases.
"""

from intake import ObservationRow as Row
from intake.admission import submit
from partitions.content import replacement_reference
from partitions.readback import PartitionContentMissing, PartitionReadError, read_content
from pickup import pickup
from reconcile import reconcile
from release import PendingReceipt, Receipt, ReleaseCatalog, RequestConflict, UnknownRelease, receipt
from release.finalize import finalize_release
from release.readiness import ReleaseNotReady
from tests import LakeCase


class TestInterruptedHandoff(LakeCase):
    def test_two_district_move_keeps_old_release_until_both_commit(self):
        first = submit(self.catalog, self.request('day', 'R0', east=[Row(1, 'r', 5)], west=[]))
        move = self.request('move', first.release_id, east=[], west=[Row(2, 'r', 7)])

        def interrupt(reference):
            if reference.district == 'west':
                raise InterruptedError('west commit interrupted')

        with self.assertRaisesRegex(InterruptedError, 'west commit interrupted'):
            submit(self.catalog, move, before_partition_commit=interrupt)

        # The unsigned request stays pending and no partial batch is signable.
        self.assertEqual(receipt(self.catalog, 'move'), PendingReceipt('move', first.release_id))
        with self.assertRaises(UnknownRelease):
            pickup(self.catalog, 'R:move')
        with self.assertRaises(ReleaseNotReady):
            finalize_release(self.catalog, move, {
                'east': replacement_reference('east', 'move'),
                'west': replacement_reference('west', 'move'),
            })

        # The signed parent remains fully claimable from its own bindings.
        old = pickup(self.catalog, first.release_id)
        self.assertEqual(tuple(part.rows for part in old.partitions), ((Row(1, 'r', 5),), ()))
        account = reconcile(self.catalog, first.release_id)
        self.assertEqual([(part.row_count, part.net_reading) for part in account.partition_totals],
                         [(1, 5), (0, 0)])
        self.assertEqual((account.batch_total.row_count, account.batch_total.net_reading), (1, 5))

        stores = self.catalog.partition_databases()
        old_east = self.catalog.get_release(first.release_id).binding_for('east')
        self.assertEqual(read_content(stores['east'], old_east).rows, (Row(1, 'r', 5),))
        with self.assertRaises(PartitionContentMissing):
            read_content(stores['west'], replacement_reference('west', 'move'))

        # Resume with the same parameters; the prepared east content is reused.
        resumed = submit(self.catalog, move)
        self.assertEqual(resumed, Receipt('move', 'R:move', 'C:move'))
        self.assertEqual(receipt(self.catalog, 'move'), resumed)
        self.assertEqual(submit(self.catalog, move), resumed)
        new_detail = pickup(self.catalog, 'R:move')
        self.assertEqual(tuple(part.rows for part in new_detail.partitions), ((), (Row(2, 'r', 7),)))
        new_account = reconcile(self.catalog, 'R:move')
        self.assertEqual([(part.row_count, part.net_reading) for part in new_account.partition_totals],
                         [(0, 0), (1, 7)])
        self.assertEqual((new_account.batch_total.row_count, new_account.batch_total.net_reading), (1, 7))

        # Reopening the directory must not change either frozen release.
        reopened = ReleaseCatalog(self.directory)
        self.assertEqual(pickup(reopened, first.release_id), old)
        self.assertEqual(pickup(reopened, 'R:move'), new_detail)
        self.assertEqual(reconcile(reopened, first.release_id), account)
        self.assertEqual(reconcile(reopened, 'R:move'), new_account)

    def test_same_name_with_different_parameters_is_rejected_after_resume(self):
        first = submit(self.catalog, self.request('day', 'R0', east=[Row(1, 'r', 5)]))
        move = self.request('move', first.release_id, east=[], west=[Row(2, 'r', 7)])

        def interrupt(reference):
            if reference.district == 'west':
                raise InterruptedError('west commit interrupted')

        with self.assertRaises(InterruptedError):
            submit(self.catalog, move, before_partition_commit=interrupt)
        submit(self.catalog, move)

        changed = self.request('move', first.release_id, east=[], west=[Row(2, 'r', 8)])
        with self.assertRaises(RequestConflict):
            submit(ReleaseCatalog(self.directory), changed)
        # The conflict must leave the signed release and its receipt intact.
        reopened = ReleaseCatalog(self.directory)
        self.assertEqual(receipt(reopened, 'move'), Receipt('move', 'R:move', 'C:move'))
        self.assertEqual(
            tuple(part.rows for part in pickup(reopened, 'R:move').partitions),
            ((), (Row(2, 'r', 7),)),
        )

    def test_explicit_empty_clears_one_district_and_other_district_inherits(self):
        east, west = Row(1, 'r', 5), Row(4, 'w', -2)
        old = submit(self.catalog, self.request('day', 'R0', east=[east], west=[west]))
        cleared = submit(self.catalog, self.request('clear', old.release_id, west=[]))
        reopened = ReleaseCatalog(self.directory)

        old_view = pickup(reopened, old.release_id)
        new_view = pickup(reopened, cleared.release_id)
        self.assertEqual(tuple(part.rows for part in old_view.partitions), ((east,), (west,)))
        self.assertEqual(tuple(part.rows for part in new_view.partitions), ((east,), ()))
        # The inherited east binding is literally the parent's binding, while
        # the cleared west points at a freshly prepared empty content.
        old_manifest = reopened.get_release(old.release_id)
        new_manifest = reopened.get_release(cleared.release_id)
        self.assertEqual(old_manifest.binding_for('east'), new_manifest.binding_for('east'))
        self.assertEqual(new_manifest.binding_for('west'), replacement_reference('west', 'clear'))
        statement = reconcile(reopened, cleared.release_id)
        self.assertEqual([(part.row_count, part.net_reading) for part in statement.partition_totals],
                         [(1, 5), (0, 0)])
        self.assertEqual((statement.batch_total.row_count, statement.batch_total.net_reading), (1, 5))
        # The old release still reports both rows through the same stores.
        self.assertEqual(reconcile(reopened, old.release_id).batch_total.net_reading, 3)

    def test_later_corrections_and_reopens_never_rewrite_history(self):
        first = submit(self.catalog, self.request('day', 'R0', east=[Row(1, 'r', 5)]))
        second = submit(self.catalog, self.request('next', first.release_id, east=[Row(1, 'r', 6)]))
        third = submit(self.catalog, self.request('again', second.release_id, west=[Row(3, 's', -2)]))
        reopened = ReleaseCatalog(self.directory)
        expected = {
            first.release_id: (((Row(1, 'r', 5),), ()), (1, 5)),
            second.release_id: (((Row(1, 'r', 6),), ()), (1, 6)),
            third.release_id: (((Row(1, 'r', 6),), (Row(3, 's', -2),)), (2, 4)),
        }
        for release_id, (detail, (row_count, net)) in expected.items():
            report = pickup(reopened, release_id)
            self.assertEqual(tuple(part.rows for part in report.partitions), detail)
            batch = reconcile(reopened, release_id).batch_total
            self.assertEqual((batch.row_count, batch.net_reading), (row_count, net))
        self.assertEqual(reopened.current_release().release_id, third.release_id)

    def test_unreadable_binding_blocks_signature_and_keeps_parent_claimable(self):
        first = submit(self.catalog, self.request('day', 'R0', east=[Row(1, 'r', 5)]))
        # Both districts carry non-empty replacements so the corruption below
        # violates the stored row count instead of looking like an explicit clear.
        move = self.request('move', first.release_id,
                            east=[Row(1, 'r', 0)], west=[Row(2, 'r', 7)])

        def interrupt(reference):
            if reference.district == 'west':
                raise InterruptedError('west commit interrupted')

        with self.assertRaises(InterruptedError):
            submit(self.catalog, move, before_partition_commit=interrupt)

        # Corrupting the prepared east content must make the candidate fail
        # closed instead of signing a manifest it cannot fully deliver.
        stores = self.catalog.partition_databases()
        east_ref = replacement_reference('east', 'move')
        west_ref = replacement_reference('west', 'move')
        with stores['east'].write_transaction() as connection:
            deleted = connection.execute(
                'DELETE FROM observations WHERE content_id = ?', (east_ref.content_id,)
            )
            self.assertEqual(deleted.rowcount, 1)
        with self.assertRaises(PartitionReadError):
            read_content(stores['east'], east_ref)
        with self.assertRaises(ReleaseNotReady):
            finalize_release(self.catalog, move, {'east': east_ref, 'west': west_ref})
        self.assertEqual(receipt(self.catalog, 'move'), PendingReceipt('move', first.release_id))
        self.assertEqual(self.catalog.current_release().release_id, first.release_id)
        # The parent's own east content is a separate, untouched materialization.
        self.assertEqual(reconcile(self.catalog, first.release_id).batch_total.net_reading, 5)
        with self.assertRaises(UnknownRelease):
            pickup(self.catalog, 'R:move')
