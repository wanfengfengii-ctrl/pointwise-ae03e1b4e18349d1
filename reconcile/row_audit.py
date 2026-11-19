"""Retain the facts a shift needs to reproduce the arithmetic."""

from dataclasses import dataclass

from pickup.report import PickupReport


@dataclass(frozen=True, slots=True)
class RowAudit:
    release_id: str
    district: str
    line_number: int
    observation_id: str
    reading: int


def audit_rows(report: PickupReport) -> tuple[RowAudit, ...]:
    return tuple(
        RowAudit(report.release_id, part.district, row.line_number, row.observation_id, row.reading)
        for part in report.partitions
        for row in part.rows
    )
