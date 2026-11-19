"""The frozen business scenarios use independent on-disk test warehouses."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from intake import PartitionReplacement, Submission
from release import initialize


class LakeCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'management'
        self.east = self.root / 'east.sqlite3'
        self.west = self.root / 'west.sqlite3'
        self.catalog = initialize(self.directory, self.east, self.west)

    @staticmethod
    def request(request_id, parent_release, **by_district):
        return Submission(request_id, parent_release, tuple(
            PartitionReplacement(district, tuple(rows)) for district, rows in by_district.items()
        ))

    def counts(self):
        with self.catalog.snapshot() as connection:
            row = connection.execute(
                '''SELECT (SELECT count(*) FROM requests),
                          (SELECT count(*) FROM releases),
                          (SELECT count(*) FROM receipts)'''
            ).fetchone()
            return tuple(row)
