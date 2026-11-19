"""Compute a complete account from a single pickup of the chosen release."""

from dataclasses import dataclass

from intake.request import DISTRICTS
from pickup.report import pickup
from release.catalog import ReleaseCatalog

from .batch_total import BatchTotal, total_batch
from .partition_total import PartitionTotal, total_partition
from .row_audit import RowAudit, audit_rows


@dataclass(frozen=True, slots=True)
class ReconciliationStatement:
    release_id: str
    rows: tuple[RowAudit, ...]
    partition_totals: tuple[PartitionTotal, ...]
    batch_total: BatchTotal


def reconcile(catalog: ReleaseCatalog, release_id: str) -> ReconciliationStatement:
    report = pickup(catalog, release_id)
    districts = tuple(part.district for part in report.partitions)
    if districts != DISTRICTS:
        raise ValueError('pickup did not supply complete ordered district detail')
    rows = audit_rows(report)
    by_district = {district: [] for district in districts}
    for row in rows:
        by_district[row.district].append(row)
    totals = tuple(
        total_partition(report.release_id, district, by_district[district])
        for district in districts
    )
    return ReconciliationStatement(report.release_id, rows, totals, total_batch(report.release_id, totals))
