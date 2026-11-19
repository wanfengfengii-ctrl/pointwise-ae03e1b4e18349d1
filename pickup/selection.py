"""Select only the signed manifest named by the night shift."""

from release.catalog import ReleaseCatalog, UnknownRelease
from release.manifest import ReleaseManifest


def select_release(catalog: ReleaseCatalog, release_id: str, *, connection=None) -> ReleaseManifest:
    if not isinstance(release_id, str) or not release_id.strip():
        raise UnknownRelease('a signed release number is required')
    return catalog.get_release(release_id, connection=connection)
