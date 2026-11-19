"""Combine exactly the two district accounts of one signed release."""

from dataclasses import dataclass

from intake.request import DISTRICTS

from .partition_total import PartitionTotal


@dataclass(frozen=True, slots=True)
class BatchTotal:
    release_id: str
    row_count: int
    net_reading: int


def total_batch(release_id: str, partitions: tuple[PartitionTotal, ...]) -> BatchTotal:
    if len(partitions) != len(DISTRICTS) or {part.district for part in partitions} != set(DISTRICTS):
        raise ValueError('a batch account requires one total for each district')
    if any(part.release_id != release_id for part in partitions):
        raise ValueError('district totals must come from the same signed release')
    return BatchTotal(
        release_id,
        sum(part.row_count for part in partitions),
        sum(part.net_reading for part in partitions),
    )
