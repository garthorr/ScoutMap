"""Pluggable import pipeline registry.

To add a new public data source:
1. Create a module in this package (e.g. ``my_source.py``).
2. Implement a function matching the ``ImporterFunc`` signature.
3. Register it in ``IMPORTERS``.
"""

from typing import Callable, Dict
from sqlalchemy.orm import Session

# Signature: (db_session, file_path, import_id) -> int  (records imported)
ImporterFunc = Callable

IMPORTERS: Dict[str, ImporterFunc] = {}


def register_importer(source_name: str, func: ImporterFunc):
    IMPORTERS[source_name] = func


def get_importer(source_name: str) -> ImporterFunc | None:
    return IMPORTERS.get(source_name)
