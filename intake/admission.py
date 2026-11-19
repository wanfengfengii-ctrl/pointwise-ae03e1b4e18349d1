"""Admit a complete register, resume local preparation, and request signature."""

from collections.abc import Callable, Mapping

from partitions.content import ContentRef
from partitions.replacement import stage_replacement
from release.catalog import CatalogError, ReleaseCatalog, RequestConflict, StaleParent
from release.finalize import finalize_release
from release.manifest import ReleaseManifest
from release.receipts import Receipt, completed_receipt

from .identity import matches_request, request_identity
from .normalization import normalize_submission
from .request import Submission
from .validation import validate_submission


def submit(
    catalog: ReleaseCatalog,
    request: Submission | Mapping,
    *,
    before_partition_commit: Callable[[ContentRef], None] | None = None,
    after_manifest: Callable[[ReleaseManifest], None] | None = None,
    after_release_commit: Callable[[Receipt], None] | None = None,
) -> Receipt:
    submission = normalize_submission(validate_submission(request))
    with catalog.transaction() as connection:
        existing = catalog.get_request(submission.request_id, connection=connection)
        if existing is not None:
            if not matches_request(submission, existing.identity):
                raise RequestConflict(f'request {submission.request_id!r} has different parameters')
            if existing.release_id is not None:
                return completed_receipt(catalog, existing, connection=connection)

        current = catalog.current_release(connection=connection)
        if current is None:
            raise CatalogError('initialize the catalog before submitting')
        if submission.parent_release != current.release_id:
            raise StaleParent(f'expected parent {current.release_id!r}, got {submission.parent_release!r}')
        if existing is None:
            catalog.register_pending(
                connection, submission.request_id, submission.parent_release, request_identity(submission)
            )
        databases = catalog.partition_databases(connection=connection)

    staged = {}
    for replacement in submission.replacements:
        staged[replacement.district] = stage_replacement(
            databases[replacement.district], submission.request_id, replacement,
            before_commit=before_partition_commit,
        )
    return finalize_release(
        catalog, submission, staged, after_manifest=after_manifest, after_commit=after_release_commit
    )
