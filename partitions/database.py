"""Local SQLite schema and transaction boundaries for a district."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3


class PartitionError(RuntimeError):
    """A district store could not provide or persist the requested content."""


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS partition_owner (
           singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
           catalog_owner TEXT NOT NULL,
           district TEXT NOT NULL CHECK (district IN ('east', 'west'))
       )''',
    '''CREATE TABLE IF NOT EXISTS contents (
           content_id TEXT PRIMARY KEY NOT NULL,
           request_id TEXT UNIQUE,
           row_count INTEGER NOT NULL CHECK (row_count >= 0)
       )''',
    '''CREATE TABLE IF NOT EXISTS active_register (
           singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
           content_id TEXT NOT NULL REFERENCES contents(content_id) ON DELETE CASCADE
       )''',
    '''CREATE TABLE IF NOT EXISTS observations (
           slot INTEGER PRIMARY KEY CHECK (slot >= 0),
           line_number TEXT NOT NULL,
           observation_id TEXT NOT NULL,
           reading TEXT NOT NULL
       )''',
)


@dataclass(frozen=True, slots=True)
class PartitionDatabase:
    path: Path
    district: str
    catalog_owner: str

    @contextmanager
    def _connect(self, mode: str):
        try:
            uri = f'{self.path.as_uri()}?mode={mode}'
            with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA foreign_keys = ON')
                yield connection
        except sqlite3.Error as error:
            raise PartitionError(f'{self.district} store: {error}') from error

    def _verify_owner(self, connection: sqlite3.Connection) -> None:
        owner = connection.execute(
            'SELECT catalog_owner, district FROM partition_owner WHERE singleton = 1'
        ).fetchone()
        if owner is None or tuple(owner) != (self.catalog_owner, self.district):
            raise PartitionError(f'{self.district} store belongs to a different catalog or district')

    @contextmanager
    def open(self, *, writable: bool = False):
        with self._connect('rw' if writable else 'ro') as connection:
            self._verify_owner(connection)
            yield connection

    def ensure_schema(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PartitionError(f'cannot create directory for {self.district} store') from error
        with self._connect('rwc') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                connection.execute(_SCHEMA[0])
                connection.execute(
                    'INSERT OR IGNORE INTO partition_owner VALUES (1, ?, ?)',
                    (self.catalog_owner, self.district),
                )
                self._verify_owner(connection)
                for statement in _SCHEMA[1:]:
                    connection.execute(statement)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def write_transaction(self):
        with self.open(writable=True) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
