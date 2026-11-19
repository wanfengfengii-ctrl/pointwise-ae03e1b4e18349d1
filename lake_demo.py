"""Run a day and its next-shift correction against independent local SQLite files."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from intake.admission import submit
from pickup import pickup
from reconcile import reconcile
from release import PendingReceipt, Receipt, ReleaseCatalog, UnknownRelease, initialize, receipt


def observed(catalog, release_id):
    report = pickup(catalog, release_id)
    statement = reconcile(catalog, release_id)
    return {
        'release': report.release_id,
        'detail': [[part.district, [[row.line_number, row.observation_id, row.reading] for row in part.rows]]
                   for part in report.partitions],
        'district_totals': [[total.district, total.row_count, total.net_reading]
                            for total in statement.partition_totals],
        'batch': [statement.batch_total.release_id, statement.batch_total.row_count, statement.batch_total.net_reading],
    }


def check(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label}: expected {expected!r}, observed {actual!r}')


def run_shifts(root, sample):
    directory = root / 'management'
    catalog = initialize(directory, root / 'east.sqlite3', root / 'west.sqlite3')
    morning = submit(catalog, sample['morning'])
    old_expected = {
        'release': 'R:morning',
        'detail': [['east', [[1, 'r', 5], [2, 'zero', 0]]], ['west', [[1, 'w', -2]]]],
        'district_totals': [['east', 2, 5], ['west', 1, -2]],
        'batch': ['R:morning', 3, 3],
    }
    check(observed(catalog, morning.release_id), old_expected, 'morning handoff')

    def interrupt_west(reference):
        if reference.district == 'west':
            raise InterruptedError('west register interrupted')

    try:
        submit(catalog, sample['move'], before_partition_commit=interrupt_west)
    except InterruptedError as error:
        check(str(error), 'west register interrupted', 'partition checkpoint')
    else:
        raise AssertionError('the west interruption did not occur')

    catalog = ReleaseCatalog(directory)
    check(receipt(catalog, 'move'), PendingReceipt('move', 'R:morning'), 'pending request')
    during = observed(catalog, morning.release_id)
    check(during, old_expected, 'LAKE_PARTIAL_BATCH_VISIBLE')
    try:
        pickup(catalog, 'R:move')
    except UnknownRelease:
        pass
    else:
        raise AssertionError('LAKE_PARTIAL_BATCH_VISIBLE: unfinished correction can be picked up')

    def drop_confirmation(confirmed):
        raise ConnectionError(f'confirmation {confirmed.receipt_id} lost')

    try:
        submit(catalog, sample['move'], after_release_commit=drop_confirmation)
    except ConnectionError as error:
        check(str(error), 'confirmation C:move lost', 'confirmation checkpoint')
    else:
        raise AssertionError('the confirmation interruption did not occur')

    catalog = ReleaseCatalog(directory)
    final = receipt(catalog, 'move')
    check(final, Receipt('move', 'R:move', 'C:move'), 'original confirmation')
    check(submit(catalog, sample['move']), final, 'same-request replay')
    downstream_expected = {
        'old': old_expected,
        'new': {
            'release': 'R:move',
            'detail': [['east', [[2, 'zero', 0]]], ['west', [[1, 'w', -2], [2, 'r', 7]]]],
            'district_totals': [['east', 1, 0], ['west', 2, 5]],
            'batch': ['R:move', 3, 5],
        },
        'receipt': ['move', 'R:move', 'C:move'],
    }
    downstream_actual = {
        'old': observed(catalog, morning.release_id),
        'new': observed(catalog, final.release_id),
        'receipt': [final.request_id, final.release_id, final.receipt_id],
    }
    check(downstream_actual, downstream_expected, 'next-shift handoff')
    return {
        'case_id': 'v4.bound_read',
        'input': sample,
        'expected': old_expected,
        'actual': during,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['partitions/replacement.py', 'release/finalize.py', 'pickup/detail.py', 'reconcile/statement.py'],
        'oracle': 'Hand-count the three rows in each release. The old sum is 5 + 0 - 2, and the new sum is 0 - 2 + 7.',
    }


def main():
    sample_path = Path(__file__).resolve().parent / 'samples' / 'shifts.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        result = run_shifts(Path(directory), sample)
    print('POINTWISE_SCENARIO ' + json.dumps(result, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
