"""SQLite catalog for configuration, signed manifests and original confirmations."""

from collections.abc import Callable
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
import sqlite3

from partitions.content import ContentRef
from partitions.database import PartitionDatabase

from .manifest import ReleaseManifest


class CatalogError(RuntimeError):
    """The directory could not perform a catalog operation."""


class ConfigurationError(CatalogError):
    """Storage locations conflict with the directory configuration."""


class UnknownRequest(CatalogError):
    """No request is registered under the supplied name."""


class UnknownRelease(CatalogError):
    """The supplied number has no signed manifest."""


class StaleParent(CatalogError):
    """The submitted parent is no longer the current signed release."""


class RequestConflict(CatalogError):
    """A registered name was reused with different parameters."""


@dataclass(frozen=True, slots=True)
class RequestRecord:
    request_id: str
    parent_release: str
    identity: str
    release_id: str | None


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    request_id: str
    release_id: str
    receipt_id: str


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS configuration (
           singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
           east_path TEXT NOT NULL,
           west_path TEXT NOT NULL,
           CHECK (east_path <> west_path)
       )''',
    '''CREATE TABLE IF NOT EXISTS releases (
           release_id TEXT PRIMARY KEY NOT NULL,
           parent_release TEXT REFERENCES releases(release_id),
           east_content TEXT NOT NULL,
           west_content TEXT NOT NULL
       )''',
    '''CREATE TABLE IF NOT EXISTS current_state (
           singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
           release_id TEXT NOT NULL REFERENCES releases(release_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS requests (
           request_id TEXT PRIMARY KEY NOT NULL,
           parent_release TEXT NOT NULL REFERENCES releases(release_id),
           identity TEXT NOT NULL,
           release_id TEXT UNIQUE REFERENCES releases(release_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS receipts (
           request_id TEXT PRIMARY KEY NOT NULL REFERENCES requests(request_id),
           release_id TEXT NOT NULL UNIQUE REFERENCES releases(release_id),
           receipt_id TEXT NOT NULL UNIQUE
       )''',
)


class ReleaseCatalog:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.path = self.directory / 'catalog.sqlite3'

    @contextmanager
    def _open(self, mode: str):
        try:
            uri = f'{self.path.as_uri()}?mode={mode}'
            with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA foreign_keys = ON')
                yield connection
        except sqlite3.Error as error:
            raise CatalogError(f'catalog operation failed: {error}') from error

    @contextmanager
    def _write(self, mode: str):
        with self._open(mode) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def configure(self, east_db: str | Path, west_db: str | Path) -> None:
        try:
            east, west = Path(east_db).resolve(), Path(west_db).resolve()
            for first, second in combinations((self.path.resolve(), east, west), 2):
                if first == second or (first.exists() and second.exists() and first.samefile(second)):
                    raise ConfigurationError('catalog, east store and west store must be distinct files')
            self.directory.mkdir(parents=True, exist_ok=True)
        except (OSError, RuntimeError) as error:
            if isinstance(error, ConfigurationError):
                raise
            raise ConfigurationError('cannot resolve or prepare the catalog locations') from error

        proposed = str(east), str(west)
        with self._write('rwc') as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            row = connection.execute(
                'SELECT east_path, west_path FROM configuration WHERE singleton = 1'
            ).fetchone()
            if row is None:
                connection.execute('INSERT INTO configuration VALUES (1, ?, ?)', proposed)
            elif tuple(row) != proposed:
                raise ConfigurationError('catalog is already bound to different district locations')

    @contextmanager
    def transaction(self):
        with self._write('rw') as connection:
            yield connection

    @contextmanager
    def snapshot(self):
        with self._open('ro') as connection:
            connection.execute('BEGIN')
            yield connection

    def _read(self, connection):
        return self.snapshot() if connection is None else nullcontext(connection)

    def partition_databases(self, *, connection=None) -> dict[str, PartitionDatabase]:
        with self._read(connection) as view:
            row = view.execute('SELECT east_path, west_path FROM configuration WHERE singleton = 1').fetchone()
        if row is None:
            raise ConfigurationError('catalog has no district configuration')
        return {
            'east': PartitionDatabase(Path(row['east_path']), 'east', str(self.path)),
            'west': PartitionDatabase(Path(row['west_path']), 'west', str(self.path)),
        }

    def get_request(self, request_id: str, *, connection=None) -> RequestRecord | None:
        with self._read(connection) as view:
            row = view.execute(
                'SELECT request_id, parent_release, identity, release_id FROM requests WHERE request_id = ?',
                (request_id,),
            ).fetchone()
        return None if row is None else RequestRecord(*row)

    def register_pending(
        self, connection: sqlite3.Connection, request_id: str, parent_release: str, identity: str
    ) -> None:
        connection.execute(
            'INSERT INTO requests (request_id, parent_release, identity) VALUES (?, ?, ?)',
            (request_id, parent_release, identity),
        )

    @staticmethod
    def _manifest(row: sqlite3.Row) -> ReleaseManifest:
        return ReleaseManifest(
            row['release_id'], row['parent_release'],
            (ContentRef('east', row['east_content']), ContentRef('west', row['west_content'])),
        )

    def get_release(self, release_id: str, *, connection=None) -> ReleaseManifest:
        with self._read(connection) as view:
            row = view.execute('SELECT * FROM releases WHERE release_id = ?', (release_id,)).fetchone()
        if row is None:
            raise UnknownRelease(f'no signed release {release_id!r}')
        return self._manifest(row)

    def current_release(self, *, connection=None) -> ReleaseManifest | None:
        with self._read(connection) as view:
            row = view.execute(
                '''SELECT releases.* FROM current_state
                   JOIN releases USING (release_id) WHERE current_state.singleton = 1'''
            ).fetchone()
        return None if row is None else self._manifest(row)

    @staticmethod
    def _insert_manifest(connection: sqlite3.Connection, manifest: ReleaseManifest) -> None:
        if len(manifest.bindings) != 2:
            raise CatalogError('a signed manifest requires two district bindings')
        east = manifest.binding_for('east')
        west = manifest.binding_for('west')
        connection.execute(
            'INSERT INTO releases (release_id, parent_release, east_content, west_content) VALUES (?, ?, ?, ?)',
            (manifest.release_id, manifest.parent_release, east.content_id, west.content_id),
        )

    def record_initial_release(self, connection: sqlite3.Connection, manifest: ReleaseManifest) -> None:
        if manifest.parent_release is not None or self.current_release(connection=connection) is not None:
            raise CatalogError('initial release requires an empty catalog and no parent')
        self._insert_manifest(connection, manifest)
        connection.execute('INSERT INTO current_state VALUES (1, ?)', (manifest.release_id,))

    def publish(
        self,
        connection: sqlite3.Connection,
        manifest: ReleaseManifest,
        request_id: str,
        receipt_id: str,
        *,
        after_manifest: Callable[[ReleaseManifest], None] | None = None,
    ) -> None:
        self._insert_manifest(connection, manifest)
        if after_manifest is not None:
            after_manifest(manifest)

        completed = connection.execute(
            '''UPDATE requests SET release_id = ?
               WHERE request_id = ? AND parent_release = ? AND release_id IS NULL''',
            (manifest.release_id, request_id, manifest.parent_release),
        )
        if completed.rowcount != 1:
            raise CatalogError(f'cannot complete pending request {request_id!r}')
        advanced = connection.execute(
            'UPDATE current_state SET release_id = ? WHERE singleton = 1 AND release_id = ?',
            (manifest.release_id, manifest.parent_release),
        )
        if advanced.rowcount != 1:
            raise StaleParent(f'parent {manifest.parent_release!r} is no longer current')
        connection.execute(
            'INSERT INTO receipts (request_id, release_id, receipt_id) VALUES (?, ?, ?)',
            (request_id, manifest.release_id, receipt_id),
        )

    def get_receipt_record(self, request_id: str, *, connection=None) -> ReceiptRecord | None:
        with self._read(connection) as view:
            row = view.execute(
                'SELECT request_id, release_id, receipt_id FROM receipts WHERE request_id = ?',
                (request_id,),
            ).fetchone()
        return None if row is None else ReceiptRecord(*row)
