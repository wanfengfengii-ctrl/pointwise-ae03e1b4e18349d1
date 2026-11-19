"""Check two durable bindings and publish one release with its original receipt."""

from collections.abc import Callable, Mapping

from intake.identity import matches_request
from intake.request import Submission
from partitions.content import ContentRef

from .catalog import ReleaseCatalog, RequestConflict, StaleParent, UnknownRequest
from .manifest import ReleaseManifest, release_number
from .readiness import select_ready_bindings
from .receipts import Receipt, completed_receipt


def finalize_release(
    catalog: ReleaseCatalog,
    submission: Submission,
    staged: Mapping[str, ContentRef],
    *,
    after_manifest: Callable[[ReleaseManifest], None] | None = None,
    after_commit: Callable[[Receipt], None] | None = None,
) -> Receipt:
    with catalog.transaction() as connection:
        request = catalog.get_request(submission.request_id, connection=connection)
        if request is None:
            raise UnknownRequest(f'no admitted request {submission.request_id!r}')
        if not matches_request(submission, request.identity):
            raise RequestConflict(f'request {submission.request_id!r} has different parameters')
        if request.release_id is not None:
            return completed_receipt(catalog, request, connection=connection)

        parent = catalog.current_release(connection=connection)
        if parent is None or parent.release_id != request.parent_release:
            raise StaleParent(f'parent {request.parent_release!r} is no longer current')
        databases = catalog.partition_databases(connection=connection)
        bindings = select_ready_bindings(submission, parent, staged, databases)
        manifest = ReleaseManifest(release_number(request.request_id), parent.release_id, bindings)
        confirmation_id = f'C:{request.request_id}'
        catalog.publish(connection, manifest, request.request_id, confirmation_id, after_manifest=after_manifest)
        confirm = Receipt(request.request_id, manifest.release_id, confirmation_id)

    if after_commit is not None:
        after_commit(confirm)
    return confirm
