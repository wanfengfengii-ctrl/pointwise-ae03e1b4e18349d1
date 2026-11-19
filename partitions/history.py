"""Persist every district content version and its preparation metadata.

Rows are materialized per ``content_id`` so that a newer replacement never
overwrites the rows of an already signed release. The catalog only keeps the
content identifiers; the district store remains the single source of truth.
"""

import sqlite3

from intake.request import ObservationRow, row_order_key

from .content import ContentRef, StoredContent
from .database import PartitionDatabase, PartitionError


def fetch_content(
    connection: sqlite3.Connection, database: PartitionDatabase, content_id: str
) -> StoredContent | None:
    metadata = connection.execute(
        'SELECT request_id, row_count FROM contents WHERE content_id = ?',
        (content_id,),
    ).fetchone()
    if metadata is None:
        return None
    records = connection.execute(
        '''SELECT slot, line_number, observation_id, reading
           FROM observations WHERE content_id = ? ORDER BY slot''',
        (content_id,),
    ).fetchall()
    if len(records) != metadata['row_count'] or any(
        record['slot'] != slot for slot, record in enumerate(records)
    ):
        raise PartitionError(f'incomplete {database.district} content {content_id!r}')
    try:
        rows = tuple(
            ObservationRow(int(record['line_number'], 16), record['observation_id'], int(record['reading'], 16))
            for record in records
        )
    except (TypeError, ValueError) as error:
        raise PartitionError(f'invalid integer in {database.district} content {content_id!r}') from error
    return StoredContent(ContentRef(database.district, content_id), metadata['request_id'], rows)


def find_prepared(
    connection: sqlite3.Connection, database: PartitionDatabase, request_id: str
) -> StoredContent | None:
    result = connection.execute(
        'SELECT content_id FROM contents WHERE request_id = ?', (request_id,)
    ).fetchone()
    if result is None:
        return None
    return fetch_content(connection, database, result['content_id'])


def fetch_loaded(connection: sqlite3.Connection, database: PartitionDatabase) -> StoredContent | None:
    head = connection.execute('SELECT content_id FROM active_register WHERE singleton = 1').fetchone()
    return None if head is None else fetch_content(connection, database, head['content_id'])


def store_content(connection: sqlite3.Connection, content: StoredContent) -> None:
    content_id = content.reference.content_id
    ordered_rows = tuple(sorted(content.rows, key=row_order_key))
    recorded = connection.execute(
        '''INSERT INTO contents (content_id, request_id, row_count) VALUES (?, ?, ?)
           ON CONFLICT(content_id) DO UPDATE SET row_count = excluded.row_count
           WHERE contents.request_id IS excluded.request_id''',
        (content_id, content.request_id, len(ordered_rows)),
    )
    if recorded.rowcount != 1:
        raise PartitionError(f'content {content_id!r} belongs to another request')
    connection.execute(
        '''INSERT INTO active_register (singleton, content_id) VALUES (1, ?)
           ON CONFLICT(singleton) DO UPDATE SET content_id = excluded.content_id''',
        (content_id,),
    )
    # Re-staging the same id must only replace its own rows; other content
    # versions (including the parent release) stay untouched and readable.
    connection.execute('DELETE FROM observations WHERE content_id = ?', (content_id,))
    connection.executemany(
        '''INSERT INTO observations (content_id, slot, line_number, observation_id, reading)
           VALUES (?, ?, ?, ?, ?)''',
        (
            (content_id, slot, format(row.line_number, 'x'), row.observation_id, format(row.reading, 'x'))
            for slot, row in enumerate(ordered_rows)
        ),
    )


def ensure_empty_content(database: PartitionDatabase) -> ContentRef:
    reference = ContentRef(database.district, 'P0')
    empty = StoredContent(reference, None, ())
    with database.write_transaction() as connection:
        saved = fetch_content(connection, database, reference.content_id)
        if saved is None:
            store_content(connection, empty)
        elif saved != empty:
            raise PartitionError(f'initial {database.district} content is inconsistent')
    return reference
