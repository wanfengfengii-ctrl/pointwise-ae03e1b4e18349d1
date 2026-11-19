"""Bind the storage locations and create the first readable parent release."""

from pathlib import Path

from intake.request import DISTRICTS
from partitions.history import ensure_empty_content
from partitions.readback import read_content

from .catalog import ReleaseCatalog
from .manifest import ReleaseManifest


def initialize(directory: str | Path, east_db: str | Path, west_db: str | Path) -> ReleaseCatalog:
    catalog = ReleaseCatalog(directory)
    catalog.configure(east_db, west_db)
    with catalog.transaction() as connection:
        databases = catalog.partition_databases(connection=connection)
        current = catalog.current_release(connection=connection)
        if current is not None:
            for district in DISTRICTS:
                read_content(databases[district], current.binding_for(district))
            return catalog

        bindings = []
        for district in DISTRICTS:
            database = databases[district]
            database.ensure_schema()
            reference = ensure_empty_content(database)
            read_content(database, reference)
            bindings.append(reference)
        catalog.record_initial_release(connection, ReleaseManifest('R0', None, tuple(bindings)))
    return catalog
