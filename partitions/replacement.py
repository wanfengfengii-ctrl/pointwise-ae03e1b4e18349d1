"""Prepare a complete replacement in the district that owns its rows."""

from collections.abc import Callable

from intake.request import PartitionReplacement

from .content import ContentRef, StoredContent, replacement_reference
from .database import PartitionDatabase, PartitionError
from .history import find_prepared, store_content


def stage_replacement(
    database: PartitionDatabase,
    request_id: str,
    replacement: PartitionReplacement,
    *,
    before_commit: Callable[[ContentRef], None] | None = None,
) -> ContentRef:
    if replacement.district != database.district:
        raise PartitionError(f'cannot store {replacement.district} rows in {database.district}')

    reference = replacement_reference(database.district, request_id)
    proposed = StoredContent(reference, request_id, replacement.rows)
    with database.write_transaction() as connection:
        existing = find_prepared(connection, database, request_id)
        if existing is not None:
            if existing != proposed:
                raise PartitionError(f'prepared {database.district} content differs for {request_id!r}')
            return existing.reference
        store_content(connection, proposed)
        if before_commit is not None:
            before_commit(reference)
    return reference
