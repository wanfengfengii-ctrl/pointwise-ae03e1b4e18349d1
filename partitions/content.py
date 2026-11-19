"""Identities and materialized contents of one independent partition."""

from dataclasses import dataclass

from intake.request import ObservationRow


@dataclass(frozen=True, slots=True)
class ContentRef:
    district: str
    content_id: str


@dataclass(frozen=True, slots=True)
class StoredContent:
    reference: ContentRef
    request_id: str | None
    rows: tuple[ObservationRow, ...]


def replacement_reference(district: str, request_id: str) -> ContentRef:
    return ContentRef(district, f'P:{request_id}')
