"""Join fixed manifest bindings to the configured district stores."""

from collections.abc import Mapping
from dataclasses import dataclass

from intake.request import DISTRICTS
from partitions.content import ContentRef
from partitions.database import PartitionDatabase
from partitions.readback import PartitionReadError
from release.manifest import ReleaseManifest


@dataclass(frozen=True, slots=True)
class BoundPartition:
    database: PartitionDatabase
    reference: ContentRef


def bind_partitions(
    manifest: ReleaseManifest, databases: Mapping[str, PartitionDatabase]
) -> tuple[BoundPartition, ...]:
    if len(manifest.bindings) != len(DISTRICTS):
        raise PartitionReadError(f'incomplete bindings for release {manifest.release_id!r}')
    bound = []
    for district in DISTRICTS:
        database = databases.get(district)
        if database is None or database.district != district:
            raise PartitionReadError(f'missing store for {district} in release {manifest.release_id!r}')
        try:
            reference = manifest.binding_for(district)
        except ValueError as error:
            raise PartitionReadError(f'invalid {district} binding in release {manifest.release_id!r}') from error
        bound.append(BoundPartition(database, reference))
    return tuple(bound)
