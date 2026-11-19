from intake import InvalidSubmission, ObservationRow as Row
from intake.admission import submit
from partitions.content import replacement_reference
from partitions.database import PartitionError
from partitions.readback import PartitionContentMissing, read_content
from pickup import pickup
from release import ConfigurationError, ReleaseCatalog, RequestConflict, StaleParent, UnknownRequest, initialize, receipt
from tests import LakeCase


class TestAdmission(LakeCase):
    def test_initialization_keeps_existing_observations(self):
        initial = pickup(self.catalog, 'R0')
        self.assertEqual(tuple(part.rows for part in initial.partitions), ((), ()))
        with self.assertRaises(UnknownRequest):
            receipt(self.catalog, 'R0')
        self.assertEqual(self.counts(), (0, 1, 0))

        recorded = submit(self.catalog, self.request('morning', 'R0', east=[Row(1, 'r', 5)]))
        for _ in range(2):
            self.catalog = initialize(self.directory, self.east, self.west)
            self.assertEqual(self.catalog.current_release().release_id, recorded.release_id)
            self.assertEqual(receipt(self.catalog, 'morning'), recorded)
            self.assertEqual(pickup(self.catalog, recorded.release_id).partitions[0].rows, (Row(1, 'r', 5),))
            self.assertEqual(self.counts(), (1, 2, 1))

        successor = submit(self.catalog, self.request('after-reopen', recorded.release_id, west=[Row(3, 's', -2)]))
        self.assertEqual(self.catalog.get_release(successor.release_id).parent_release, recorded.release_id)
        self.assertEqual(tuple(part.rows for part in pickup(self.catalog, successor.release_id).partitions),
                         ((Row(1, 'r', 5),), (Row(3, 's', -2),)))

    def test_rejects_rebinding_either_district(self):
        recorded = submit(self.catalog, self.request('morning', 'R0', east=[Row(1, 'r', 5)]))
        before = pickup(self.catalog, recorded.release_id)
        other = self.root / 'other.sqlite3'
        for east, west in ((other, self.west), (self.east, other), (self.east, self.east)):
            with self.subTest(east=east.name, west=west.name):
                with self.assertRaises(ConfigurationError):
                    initialize(self.directory, east, west)
                reopened = ReleaseCatalog(self.directory)
                self.assertEqual(pickup(reopened, recorded.release_id), before)
                self.assertEqual(receipt(reopened, 'morning'), recorded)
                self.assertEqual(reopened.current_release().release_id, recorded.release_id)
        self.assertFalse(other.exists())
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_partial_initialization_keeps_catalog_unsigned_until_both_stores_are_ready(self):
        directory = self.root / 'new-management'
        east = self.root / 'new-east.sqlite3'
        west = self.root / 'new-west.sqlite3'
        west.write_bytes(b'this file is a damaged district database fixture\n')
        with self.assertRaises(PartitionError):
            initialize(directory, east, west)
        pending = ReleaseCatalog(directory)
        self.assertIsNone(pending.current_release())
        east_db = pending.partition_databases()['east']
        self.assertTrue(east_db.path.is_file())
        west.unlink()
        completed = initialize(directory, east, west)
        self.assertEqual(completed.current_release().release_id, 'R0')
        self.assertEqual(tuple(part.rows for part in pickup(completed, 'R0').partitions), ((), ()))

    def test_invalid_rows_do_not_register_or_stage_any_district(self):
        first = submit(self.catalog, self.request('morning', 'R0', east=[Row(1, 'r', 5)]))
        self.catalog = ReleaseCatalog(self.directory)
        baseline = pickup(self.catalog, first.release_id)
        east_db = self.catalog.partition_databases()['east']
        invalid_rows = (
            {'line_number': 2, 'observation_id': 'bad'},
            {'line_number': 2, 'observation_id': 'bad', 'reading': None},
            {'line_number': 2, 'observation_id': 'bad', 'reading': 1.25},
            {'line_number': 2, 'observation_id': 'bad', 'reading': True},
        )
        for bad_row in invalid_rows:
            with self.subTest(row=bad_row):
                request = {
                    'request_id': 'bad', 'parent_release': first.release_id,
                    'replacements': [
                        {'district': 'east', 'rows': [{'line_number': 1, 'observation_id': 'valid', 'reading': 99}]},
                        {'district': 'west', 'rows': [bad_row]},
                    ],
                }
                with self.assertRaises(InvalidSubmission):
                    submit(self.catalog, request)
                with self.assertRaises(UnknownRequest):
                    receipt(self.catalog, 'bad')
                with self.assertRaises(PartitionContentMissing):
                    read_content(east_db, replacement_reference('east', 'bad'))
                self.assertEqual(pickup(self.catalog, first.release_id), baseline)
                self.assertEqual(receipt(self.catalog, 'morning'), first)
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_invalid_replacement_scope_is_rejected(self):
        invalid_scopes = (
            [],
            [{'district': 'north', 'rows': []}],
            [{'district': 'east', 'rows': []}, {'district': 'east', 'rows': []}],
        )
        for replacements in invalid_scopes:
            with self.subTest(replacements=replacements):
                with self.assertRaises(InvalidSubmission):
                    submit(self.catalog, {'request_id': 'invalid', 'parent_release': 'R0', 'replacements': replacements})
        self.assertEqual(self.counts(), (0, 1, 0))

    def test_changed_request_rejected(self):
        row = Row(2, 'r', 7)
        other = Row(1, 's', 0)
        original = submit(self.catalog, self.request('west-register', 'R0', west=[row, other], east=[]))
        reopened = ReleaseCatalog(self.directory)
        equivalent = self.request('west-register', 'R0', east=[], west=[other, row])
        self.assertEqual(submit(reopened, equivalent), original)
        changed = self.request('west-register', 'R0', east=[], west=[other, Row(2, 'r', 8)])
        with self.assertRaises(RequestConflict):
            submit(reopened, changed)
        omitted = self.request('west-register', 'R0', west=[other, row])
        with self.assertRaises(RequestConflict):
            submit(reopened, omitted)
        self.assertEqual(receipt(reopened, 'west-register'), original)
        self.assertEqual(pickup(reopened, original.release_id).partitions[1].rows, (other, row))
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_stale_parent_rejected(self):
        first = submit(self.catalog, self.request('first', 'R0', east=[Row(1, 'r', 5)]))
        second = submit(self.catalog, self.request('second', first.release_id, east=[Row(1, 'r', 6)]))
        stale = self.request('stale', first.release_id, west=[Row(2, 's', 7)])
        with self.assertRaises(StaleParent):
            submit(self.catalog, stale)
        with self.assertRaises(UnknownRequest):
            receipt(self.catalog, 'stale')
        with self.assertRaises(PartitionContentMissing):
            read_content(self.catalog.partition_databases()['west'], replacement_reference('west', 'stale'))
        self.assertEqual(self.catalog.current_release().release_id, second.release_id)
        self.assertEqual(pickup(self.catalog, first.release_id).partitions[0].rows, (Row(1, 'r', 5),))
        self.assertEqual(pickup(self.catalog, second.release_id).partitions[0].rows, (Row(1, 'r', 6),))
        self.assertEqual(self.counts(), (2, 3, 2))
