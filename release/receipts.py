"""Distinguish an unknown name, pending work and its eventual original receipt."""

from dataclasses import dataclass
import sqlite3

from .catalog import CatalogError, ReleaseCatalog, RequestRecord, UnknownRequest


@dataclass(frozen=True, slots=True)
class Receipt:
    request_id: str
    release_id: str
    receipt_id: str


@dataclass(frozen=True, slots=True)
class PendingReceipt:
    request_id: str
    parent_release: str


def completed_receipt(
    catalog: ReleaseCatalog, request: RequestRecord, *, connection: sqlite3.Connection
) -> Receipt:
    record = catalog.get_receipt_record(request.request_id, connection=connection)
    if request.release_id is None or record is None or record.release_id != request.release_id:
        raise CatalogError(f'inconsistent completed request {request.request_id!r}')
    return Receipt(record.request_id, record.release_id, record.receipt_id)


def receipt(catalog: ReleaseCatalog, request_id: str) -> Receipt | PendingReceipt:
    with catalog.snapshot() as connection:
        request = catalog.get_request(request_id, connection=connection)
        if request is None:
            raise UnknownRequest(f'unknown request {request_id!r}')
        if request.release_id is None:
            return PendingReceipt(request.request_id, request.parent_release)
        return completed_receipt(catalog, request, connection=connection)
