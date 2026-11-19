"""Read the committed working register."""

from .content import ContentRef, StoredContent
from .database import PartitionDatabase, PartitionError
from .history import fetch_content, fetch_loaded


class PartitionReadError(PartitionError):
    """The requested district content could not be read in full."""


class PartitionContentMissing(PartitionReadError):
    """The store opened successfully, but the exact content is absent."""


def read_content(database: PartitionDatabase, reference: ContentRef) -> StoredContent:
    if reference.district != database.district:
        raise PartitionReadError(f'{reference.district} reference sent to {database.district} store')
    try:
        with database.open() as connection:
            # Metadata and row slots must be checked in the same SQLite read view.
            connection.execute('BEGIN')
            content = fetch_content(connection, database, reference.content_id)
    except PartitionError as error:
        raise PartitionReadError(f'cannot read {reference.district} content {reference.content_id!r}') from error
    if content is None:
        raise PartitionContentMissing(f'absent {reference.district} content {reference.content_id!r}')
    return content


def read_loaded(database: PartitionDatabase) -> StoredContent:
    try:
        with database.open() as connection:
            connection.execute('BEGIN')
            content = fetch_loaded(connection, database)
    except PartitionError as error:
        raise PartitionReadError(f'cannot read loaded {database.district} register') from error
    if content is None:
        raise PartitionContentMissing(f'no loaded {database.district} register')
    return content
