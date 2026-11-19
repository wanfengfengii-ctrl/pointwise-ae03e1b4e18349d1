"""Named complete district replacements and their source rows."""

from dataclasses import dataclass

DISTRICTS = ('east', 'west')


@dataclass(frozen=True, slots=True)
class ObservationRow:
    line_number: int
    observation_id: str
    reading: int


@dataclass(frozen=True, slots=True)
class PartitionReplacement:
    district: str
    rows: tuple[ObservationRow, ...]


@dataclass(frozen=True, slots=True)
class Submission:
    request_id: str
    parent_release: str
    replacements: tuple[PartitionReplacement, ...]


def row_order_key(row: ObservationRow) -> tuple[int, str, int]:
    return row.line_number, row.observation_id, row.reading
