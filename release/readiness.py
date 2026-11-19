"""Assemble the district bindings of a planned release.

A release is only ready once every binding it will cite — both the freshly
replaced districts and the districts inherited from the parent — is durably
present in its own district store and can be read back in full. Comparing
references in memory is not enough: a local commit may have been interrupted
before the store contains the content, and reading another district's head
must never be mistaken for a successful two-district handoff.
"""

from collections.abc import Mapping

from intake.request import DISTRICTS, Submission, row_order_key
from partitions.content import ContentRef, replacement_reference
from partitions.database import PartitionDatabase, PartitionError
from partitions.readback import read_content

from .catalog import CatalogError
from .manifest import ReleaseManifest


class ReleaseNotReady(CatalogError):
    """At least one district binding cannot be signed for this request."""


def _verify_binding(
    district: str,
    reference: ContentRef,
    expected_rows,
    expected_request_id: str | None,
    databases: Mapping[str, PartitionDatabase],
) -> ContentRef:
    database = databases.get(district)
    if database is None or database.district != district:
        raise ReleaseNotReady(f'{district} store is not configured')
    if reference.district != district:
        raise ReleaseNotReady(f'{district} binding points at the {reference.district!r} store')
    try:
        stored = read_content(database, reference)
    except PartitionError as error:
        raise ReleaseNotReady(f'{district} binding {reference.content_id!r} is not fully readable') from error
    if expected_request_id is not None and stored.request_id != expected_request_id:
        raise ReleaseNotReady(
            f'{district} binding {reference.content_id!r} was not prepared for request {expected_request_id!r}'
        )
    if expected_rows is not None and tuple(sorted(stored.rows, key=row_order_key)) != tuple(
        sorted(expected_rows, key=row_order_key)
    ):
        raise ReleaseNotReady(f'{district} binding {reference.content_id!r} does not match the admitted rows')
    return reference


def select_ready_bindings(
    submission: Submission,
    parent: ReleaseManifest,
    staged: Mapping[str, ContentRef],
    databases: Mapping[str, PartitionDatabase],
) -> tuple[ContentRef, ...]:
    replacements = {item.district: item for item in submission.replacements}
    if set(staged) != set(replacements):
        raise ReleaseNotReady('prepared district scope differs from the admitted replacement scope')

    bindings = []
    for district in DISTRICTS:
        replacement = replacements.get(district)
        if replacement is None:
            # Unspecified districts inherit the signed parent content, which
            # must itself still be fully readable in the local store.
            reference = parent.binding_for(district)
            bindings.append(_verify_binding(district, reference, None, None, databases))
            continue

        reference = staged[district]
        wanted = replacement_reference(district, submission.request_id)
        if reference != wanted:
            raise ReleaseNotReady(f'{district} reference belongs to a different request or district')
        bindings.append(
            _verify_binding(district, reference, replacement.rows, submission.request_id, databases)
        )
    return tuple(bindings)
