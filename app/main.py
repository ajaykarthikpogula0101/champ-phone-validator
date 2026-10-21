"""
Champ Phone Validator web app.

    .venv\\Scripts\\python -m uvicorn app.main:app --port 8000
    open http://127.0.0.1:8000

Endpoints
---------
GET  /                 the single-page UI
POST /api/check        one number  -> JSON with status "Validated" / "Not Validated"
POST /api/bulk         upload a file (.csv / .xlsx / .txt) -> PDF, Word or CSV report download
GET  /api/sample       the synthetic 200-row demo file
GET  /api/health       liveness

Uploaded files are processed in memory and never written to disk.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from phone_validator.readers import UnsupportedFile, read_upload
from phone_validator.report import REPORT_BUILDERS
from phone_validator.validate import normalize_country, validate_number, validate_table

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
SAMPLE = ROOT / "data" / "samples" / "synthetic_200.csv"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_ROWS = 50_000
MAX_NUMBER_CHARS = 64

app = FastAPI(title="Champ Phone Validator", version="0.2.0")


class CheckRequest(BaseModel):
    number: str = Field(..., description="Phone number in any common format")
    country: str = Field("IN", description="Country to assume if the number has no + code (ISO-2 or name)")


def _region(value: str) -> str:
    region = normalize_country(value) if value else ""
    return region or "IN"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/check")
def check(req: CheckRequest) -> dict:
    number = (req.number or "").strip()
    if not number:
        raise HTTPException(422, "Enter a phone number.")
    if len(number) > MAX_NUMBER_CHARS:
        raise HTTPException(422, f"That is longer than {MAX_NUMBER_CHARS} characters; enter one number.")
    region = _region(req.country)
    # The picked country only fills in a missing country code; a number written with its own
    # +code (e.g. +44 ...) is judged on that code, not flagged as "wrong country".
    result = validate_number(number, country_in=None, default_region=region)
    out = result.to_public()
    out["country_given"] = region
    return out


def _safe_stem(filename: str) -> str:
    stem = Path(filename or "numbers").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "numbers"
    return stem[:60]


@app.post("/api/bulk")
async def bulk(
    file: UploadFile = File(...),
    format: str = Form("pdf"),
    country: str = Form("IN"),
) -> Response:
    fmt = (format or "pdf").lower()
    if fmt not in REPORT_BUILDERS:
        raise HTTPException(422, f"format must be one of {', '.join(REPORT_BUILDERS)}")

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data.strip():
        raise HTTPException(422, "The file is empty.")

    try:
        rows, fieldnames = read_upload(file.filename or "", data)
    except UnsupportedFile as e:
        raise HTTPException(415, str(e))
    except Exception as e:  # corrupt xlsx, etc.
        raise HTTPException(422, f"Could not read the file: {e}")

    if not rows:
        raise HTTPException(422, "No rows found in the file.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(413, f"The file has {len(rows):,} rows; the limit is {MAX_ROWS:,} per upload.")

    region = _region(country)
    try:
        table = validate_table(rows, fieldnames, default_region=region)
    except KeyError:
        raise HTTPException(
            422,
            "Could not find a phone column. Name the column 'phone' or 'mobile', "
            f"or upload a single column of numbers. Columns found: {', '.join(fieldnames)}",
        )

    media_type, ext, build = REPORT_BUILDERS[fmt]
    body = build(table, file.filename or "", datetime.now())
    out_name = f"{_safe_stem(file.filename)}_validation_report{ext}"
    s = table.summary
    summary = {
        "total": s["total_rows"],
        "validated": s["validated"],
        "not_validated": s["not_validated"],
        "needs_review": s["verdict_review"],
        "duplicates": s["duplicates"],
        "phone_column": table.phone_col,
        "country_column": table.country_col,
        "default_country": region,
    }
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Validation-Summary": json.dumps(summary),
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/sample")
def sample() -> FileResponse:
    return FileResponse(SAMPLE, media_type="text/csv", filename="sample_phone_numbers.csv")


@app.exception_handler(HTTPException)
async def http_error(_request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
