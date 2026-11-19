"""Assemble the district bindings of a planned release."""

from collections.abc import Mapping

from intake.request import DISTRICTS, Submission
from partitions.content import ContentRef, replacement_reference
from partitions.database import PartitionDatabase

from .catalog import CatalogError
from .manifest import ReleaseManifest


class ReleaseNotReady(CatalogError):
    """At least one district binding cannot be signed for this request."""


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
            reference = parent.binding_for(district)
        else:
            reference = staged[district]
            wanted = replacement_reference(district, submission.request_id)
            if reference != wanted:
                raise ReleaseNotReady(f'{district} reference belongs to a different request or district')
        database = databases.get(district)
        if database is None or database.district != district:
            raise ReleaseNotReady(f'{district} store is not configured')
        bindings.append(reference)
    return tuple(bindings)
