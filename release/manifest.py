"""Versioned catalog metadata for two independently stored districts."""

from dataclasses import dataclass

from partitions.content import ContentRef


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    release_id: str
    parent_release: str | None
    bindings: tuple[ContentRef, ...]

    def binding_for(self, district: str) -> ContentRef:
        matches = tuple(reference for reference in self.bindings if reference.district == district)
        if len(matches) != 1:
            raise ValueError(f'release {self.release_id!r} has no unique binding for {district!r}')
        return matches[0]


def release_number(request_id: str) -> str:
    return f'R:{request_id}'
