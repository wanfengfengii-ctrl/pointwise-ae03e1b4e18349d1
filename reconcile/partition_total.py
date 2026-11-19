"""Settle a district against all its rows, including zero readings."""

from collections.abc import Iterable
from dataclasses import dataclass

from .row_audit import RowAudit


@dataclass(frozen=True, slots=True)
class PartitionTotal:
    release_id: str
    district: str
    row_count: int
    net_reading: int


def total_partition(release_id: str, district: str, rows: Iterable[RowAudit]) -> PartitionTotal:
    count = net = 0
    for row in rows:
        if row.release_id != release_id or row.district != district:
            raise ValueError('row audit does not belong to the stated release and district')
        count += 1
        net += row.reading
    return PartitionTotal(release_id, district, count, net)
