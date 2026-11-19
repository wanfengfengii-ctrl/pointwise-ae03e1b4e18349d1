"""Public pickup of the complete source detail for one signed release."""

from dataclasses import dataclass

from release.catalog import ReleaseCatalog

from .bindings import bind_partitions
from .detail import PartitionDetail, collect_detail
from .selection import select_release


@dataclass(frozen=True, slots=True)
class PickupReport:
    release_id: str
    partitions: tuple[PartitionDetail, ...]


def pickup(catalog: ReleaseCatalog, release_id: str) -> PickupReport:
    with catalog.snapshot() as connection:
        manifest = select_release(catalog, release_id, connection=connection)
        databases = catalog.partition_databases(connection=connection)
    bindings = bind_partitions(manifest, databases)
    return PickupReport(manifest.release_id, collect_detail(bindings))
