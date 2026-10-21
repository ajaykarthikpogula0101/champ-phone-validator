"""
Turn an uploaded file into (rows, fieldnames) for validate_table().

Supported:
  .csv  - header row expected; a header-less single-column file of numbers also works
  .xlsx - first worksheet, first row is the header (header-less also works)
  .txt  - one phone number per line
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Iterable, Optional

from .validate import PHONE_COLUMN_CANDIDATES, _pick_column

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".txt")
PHONE_HEADER = "phone"

# a cell that is "mostly a phone number" rather than a header word
_LOOKS_LIKE_NUMBER = re.compile(r"^[\s+()\-.\d]{5,}$|^\d")


class UnsupportedFile(ValueError):
    pass


def _cell_to_text(value) -> str:
    """Excel hands back ints / floats for numeric cells; turn them back into digit strings."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


def _rows_from_matrix(matrix: list[list[str]]) -> tuple[list[dict], list[str]]:
    """Decide whether row 0 is a header; build dict rows either way."""
    matrix = [r for r in matrix if any(c.strip() for c in r)]
    if not matrix:
        return [], [PHONE_HEADER]
    width = max(len(r) for r in matrix)
    matrix = [r + [""] * (width - len(r)) for r in matrix]
    first = matrix[0]

    header_is_data = (
        _pick_column(first, None, PHONE_COLUMN_CANDIDATES) is None
        and any(_LOOKS_LIKE_NUMBER.match(c.strip()) for c in first if c.strip())
    )
    if header_is_data:
        fieldnames = [PHONE_HEADER] + [f"column_{i + 1}" for i in range(1, width)]
        body = matrix
    else:
        fieldnames, seen = [], set()
        for i, h in enumerate(first):
            name = h.strip() or f"column_{i + 1}"
            while name in seen:
                name += "_"
            seen.add(name)
            fieldnames.append(name)
        body = matrix[1:]
        # a single unnamed column is assumed to be the phone column
        if width == 1 and _pick_column(fieldnames, None, PHONE_COLUMN_CANDIDATES) is None:
            fieldnames = [PHONE_HEADER]
    rows = [dict(zip(fieldnames, r)) for r in body]
    return rows, fieldnames


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_csv_bytes(data: bytes) -> tuple[list[dict], list[str]]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    matrix = [[c for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    return _rows_from_matrix(matrix)


def read_txt_bytes(data: bytes) -> tuple[list[dict], list[str]]:
    lines = [ln.strip() for ln in _decode(data).splitlines()]
    lines = [ln for ln in lines if ln]
    if lines and _pick_column([lines[0]], None, PHONE_COLUMN_CANDIDATES) and not _LOOKS_LIKE_NUMBER.match(lines[0]):
        lines = lines[1:]  # drop a "phone" header line
    return [{PHONE_HEADER: ln} for ln in lines], [PHONE_HEADER]


def read_xlsx_bytes(data: bytes) -> tuple[list[dict], list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        matrix = [[_cell_to_text(v) for v in row] for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return _rows_from_matrix(matrix)


def read_upload(filename: str, data: bytes) -> tuple[list[dict], list[str]]:
    ext = Path(filename or "").suffix.lower()
    if ext == ".csv":
        return read_csv_bytes(data)
    if ext == ".xlsx":
        return read_xlsx_bytes(data)
    if ext == ".txt":
        return read_txt_bytes(data)
    raise UnsupportedFile(f"Unsupported file type {ext or '(none)'}; use {', '.join(SUPPORTED_EXTENSIONS)}")
