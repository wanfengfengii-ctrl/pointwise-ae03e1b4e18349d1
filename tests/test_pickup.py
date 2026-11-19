import json

from intake import ObservationRow as Row
from intake.admission import submit
from pickup import pickup
from reconcile import reconcile
from release import ReleaseCatalog, UnknownRelease
from tests import LakeCase


class TestPickup(LakeCase):
    def test_totals_preserve_zero_row(self):
        zero, observation, west = Row(2, 'zero', 0), Row(1, 'r', 5), Row(4, 'w', -2)
        signed = submit(self.catalog, self.request('day', 'R0', west=[west], east=[zero, observation]))
        report = pickup(self.catalog, signed.release_id)
        self.assertEqual(report.release_id, 'R:day')
        self.assertEqual([(part.district, part.rows) for part in report.partitions],
                         [('east', (observation, zero)), ('west', (west,))])

        statement = reconcile(self.catalog, signed.release_id)
        self.assertEqual(statement.release_id, report.release_id)
        self.assertEqual([(row.release_id, row.district, row.line_number, row.observation_id, row.reading)
                          for row in statement.rows],
                         [('R:day', 'east', 1, 'r', 5), ('R:day', 'east', 2, 'zero', 0),
                          ('R:day', 'west', 4, 'w', -2)])
        self.assertEqual([(part.release_id, part.district, part.row_count, part.net_reading)
                          for part in statement.partition_totals],
                         [('R:day', 'east', 2, 5), ('R:day', 'west', 1, -2)])
        self.assertEqual((statement.batch_total.release_id, statement.batch_total.row_count,
                          statement.batch_total.net_reading), ('R:day', 3, 3))

    def test_exact_integer_reconciliation(self):
        east = [Row(2, 'zero', 0), Row(1, 'large', 9007199254740993)]
        west = [Row(1, 'offset', -9007199254740992)]
        signed = submit(self.catalog, self.request('precision', 'R0', east=east, west=west))
        statement = reconcile(ReleaseCatalog(self.directory), signed.release_id)
        expected = {'rows': [9007199254740993, 0, -9007199254740992],
                    'districts': [['east', 2, 9007199254740993], ['west', 1, -9007199254740992]]}
        actual = {'rows': [row.reading for row in statement.rows],
                  'districts': [[part.district, part.row_count, part.net_reading] for part in statement.partition_totals]}
        downstream_expected = {'release': 'R:precision', 'row_count': 3, 'net': 1}
        downstream_actual = {'release': statement.batch_total.release_id,
                             'row_count': statement.batch_total.row_count, 'net': statement.batch_total.net_reading}
        self.assertEqual(actual, expected)
        self.assertEqual(downstream_actual, downstream_expected)
        self.assertIs(type(statement.batch_total.net_reading), int)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v2.exact_integer',
            'input': {'east': [[2, 'zero', 0], [1, 'large', 9007199254740993]],
                      'west': [[1, 'offset', -9007199254740992]]},
            'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': ['partitions/history.py', 'reconcile/partition_total.py', 'reconcile/batch_total.py'],
            'oracle': 'The nonzero readings are 2**53 + 1 and -2**53. The third row is zero and is counted.',
        }, separators=(',', ':')))

    def test_integers_beyond_sqlite_integer_range_round_trip(self):
        large = 1 << 200
        signed = submit(self.catalog, self.request('wide', 'R0', east=[Row(1, 'wide', large)],
                                                   west=[Row(1, 'offset', -large + 2)]))
        reopened = ReleaseCatalog(self.directory)
        report = pickup(reopened, signed.release_id)
        self.assertEqual(tuple(part.rows[0].reading for part in report.partitions), (large, -large + 2))
        self.assertEqual(reconcile(reopened, signed.release_id).batch_total.net_reading, 2)

    def test_detail_orders_same_line_numbers_and_preserves_occurrences(self):
        a = Row(2, 'a', 4)
        b = Row(2, 'a', -1)
        c = Row(2, 'b', 0)
        first = Row(1, 'c', 2)
        signed = submit(self.catalog, self.request('ties', 'R0', east=[c, a, first, b, a]))
        report = pickup(self.catalog, signed.release_id)
        self.assertEqual(report.partitions[0].rows, (first, b, a, a, c))
        account = reconcile(self.catalog, signed.release_id)
        self.assertEqual((account.batch_total.row_count, account.batch_total.net_reading), (5, 9))

    def test_empty_replacement_and_inheritance(self):
        east, west = Row(1, 'r', 5), Row(4, 'w', -2)
        old = submit(self.catalog, self.request('day', 'R0', east=[east], west=[west]))
        cleared = submit(self.catalog, self.request('clear', old.release_id, west=[]))
        reopened = ReleaseCatalog(self.directory)
        old_view = pickup(reopened, old.release_id)
        new_view = pickup(reopened, cleared.release_id)
        self.assertEqual(tuple(part.rows for part in old_view.partitions), ((east,), (west,)))
        self.assertEqual(tuple(part.rows for part in new_view.partitions), ((east,), ()))
        old_manifest = reopened.get_release(old.release_id)
        new_manifest = reopened.get_release(cleared.release_id)
        self.assertEqual(old_manifest.binding_for('east'), new_manifest.binding_for('east'))
        self.assertNotEqual(old_manifest.binding_for('west'), new_manifest.binding_for('west'))
        account = reconcile(reopened, cleared.release_id)
        self.assertEqual([(part.district, part.row_count, part.net_reading) for part in account.partition_totals],
                         [('east', 1, 5), ('west', 0, 0)])
        self.assertEqual((account.batch_total.row_count, account.batch_total.net_reading), (1, 5))
        self.assertEqual(reconcile(reopened, old.release_id).batch_total.net_reading, 3)

    def test_unknown_release_has_no_statement(self):
        for read in (pickup, reconcile):
            with self.subTest(read=read.__name__):
                with self.assertRaises(UnknownRelease):
                    read(self.catalog, 'R:unsigned')
        self.assertEqual(self.counts(), (0, 1, 0))
