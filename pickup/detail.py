"""Read the full district detail before handing it to the reporting entry point."""

from dataclasses import dataclass

from intake.request import ObservationRow, row_order_key
from partitions.readback import read_loaded

from .bindings import BoundPartition


@dataclass(frozen=True, slots=True)
class PartitionDetail:
    district: str
    rows: tuple[ObservationRow, ...]


def collect_detail(bindings: tuple[BoundPartition, ...]) -> tuple[PartitionDetail, ...]:
    parts = []
    for bound in bindings:
        content = read_loaded(bound.database)
        ordered = tuple(sorted(content.rows, key=row_order_key))
        parts.append(PartitionDetail(bound.reference.district, ordered))
    return tuple(parts)
