"""Streaming CSV downloads that are safe to open in Excel.

Row generators passed to csv_response run after the request's get_db session
has been closed, so they must open (and close) their own session.
"""

import csv
import io
import json
import re
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CHUNK_ROWS = 500


def safe_cell(value: Any) -> str:
    """Turn a value into CSV cell text that a spreadsheet won't run as a formula."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", filename or "") or "export.csv"


def _stream(header: Sequence[Any], rows: Iterable[Sequence[Any]]):
    buf = io.StringIO()
    writer = csv.writer(buf)

    def take() -> bytes:
        data = buf.getvalue()
        buf.seek(0)
        buf.truncate()
        return data.encode("utf-8")

    writer.writerow([safe_cell(h) for h in header])
    yield "﻿".encode("utf-8") + take()
    pending = 0
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
        pending += 1
        if pending >= _CHUNK_ROWS:
            yield take()
            pending = 0
    if pending:
        yield take()


def _close(rows: Iterable[Sequence[Any]]) -> None:
    close = getattr(rows, "close", None)
    if close is not None:
        close()


def csv_response(filename: str, header: Sequence[Any], rows: Iterable[Sequence[Any]]) -> StreamingResponse:
    # A client disconnect cancels the stream without closing the generator, so its
    # session would stay checked out until a cyclic GC pass. Starlette still runs
    # the background task after a disconnect, so close the rows there.
    return StreamingResponse(
        _stream(header, rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(filename)}"',
            "Cache-Control": "no-store",
        },
        background=BackgroundTask(_close, rows),
    )
