"""
Layer 1 (format / normalisation) and layer 2 (offline type / carrier / country)
phone validation using the `phonenumbers` library (a port of Google libphonenumber).

Verdicts
--------
keep    valid mobile number in the expected country -> safe to call / SMS
drop    unusable without further spend: invalid, landline, toll-free, duplicate, ...
review  cannot be settled offline: fixed-or-mobile ranges (e.g. all US numbers),
        VoIP, wrong country column, mobile typed with an extension.
        These are the rows that need a paid HLR lookup (layers 3 and 4).
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional

import phonenumbers as pn
from phonenumbers import carrier, geocoder
from phonenumbers import PhoneNumberType as T

# --------------------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------------------

OUTPUT_COLUMNS = [
    "row", "input", "country_in", "normalized", "e164", "valid_format", "line_type",
    "country", "carrier", "region_hint", "extension", "verdict", "reason",
]

PHONE_COLUMN_CANDIDATES = (
    "phone", "mobile", "phone_number", "mobile_number", "number", "contact",
    "contact_number", "telephone", "tel", "msisdn", "cell", "phone no", "mobile no",
)
COUNTRY_COLUMN_CANDIDATES = ("country", "country_code", "region", "iso2", "nation")

# country column values we expect to see -> ISO 3166 alpha-2
COUNTRY_ALIASES = {
    "india": "IN", "in": "IN", "ind": "IN", "bharat": "IN",
    "united states": "US", "usa": "US", "us": "US", "u.s.": "US", "u.s.a.": "US",
    "united states of america": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "gb": "GB", "great britain": "GB", "england": "GB",
    "united arab emirates": "AE", "uae": "AE", "ae": "AE", "dubai": "AE",
    "canada": "CA", "ca": "CA", "australia": "AU", "au": "AU", "singapore": "SG", "sg": "SG",
    "germany": "DE", "de": "DE", "france": "FR", "fr": "FR",
}

# Human-readable line types
LINE_TYPE_LABEL = {
    T.MOBILE: "mobile",
    T.FIXED_LINE: "landline",
    T.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
    T.VOIP: "voip",
    T.TOLL_FREE: "toll_free",
    T.PREMIUM_RATE: "premium_rate",
    T.SHARED_COST: "shared_cost",
    T.PERSONAL_NUMBER: "personal_number",
    T.PAGER: "pager",
    T.UAN: "uan",
    T.VOICEMAIL: "voicemail",
    T.UNKNOWN: "unknown",
}

# Excel scientific notation, e.g. 9.19845E+11, 9.198450123450E11, 9,19845E+11
_SCI_RE = re.compile(r"^\s*\+?(\d+)(?:[.,](\d+))?[eE]\+?(\d+)\s*$")
# Excel float with trailing .0 / .00, e.g. 919845012345.0
_TRAILING_ZERO_RE = re.compile(r"^\s*(\+?\d+)\.0+\s*$")
# Minimum significant digits we accept when reversing scientific notation. Any national
# mobile number has at least 10 digits; a mantissa shorter than that has lost digits.
_MIN_SIG_DIGITS = 10


# --------------------------------------------------------------------------------------
# result type
# --------------------------------------------------------------------------------------

@dataclass
class ValidationResult:
    row: int
    input: str
    country_in: str = ""
    normalized: str = ""
    e164: str = ""
    valid_format: bool = False
    line_type: str = ""
    country: str = ""
    carrier: str = ""
    region_hint: str = ""
    extension: str = ""
    verdict: str = "drop"
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.reason.startswith("duplicate_of_row_")

    @property
    def base_reason(self) -> str:
        return "duplicate" if self.is_duplicate else self.reason

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("notes")
        d["valid_format"] = "yes" if self.valid_format else "no"
        if self.notes:
            d["reason"] = self.reason + " [" + "; ".join(self.notes) + "]"
        return d


# --------------------------------------------------------------------------------------
# layer 1: normalisation
# --------------------------------------------------------------------------------------

def normalize_country(value: Optional[str]) -> str:
    """Map a free-text country cell to ISO alpha-2, or '' if unknown/blank."""
    if value is None:
        return ""
    v = value.strip()
    if not v:
        return ""
    if v.lower() in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[v.lower()]
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return ""


def unmangle_excel(raw: str) -> tuple[str, Optional[str], bool]:
    """
    Undo the damage Excel does to numeric phone cells.

    Returns (cleaned_text, note, lost_digits):
      * scientific notation is expanded back into digits; if the mantissa carries fewer
        than _MIN_SIG_DIGITS significant digits the original digits are gone for good
        and lost_digits is True.
      * a trailing ".0" is stripped.
    """
    m = _SCI_RE.match(raw)
    if m:
        int_part, frac_part, exp = m.group(1), m.group(2) or "", int(m.group(3))
        digits = int_part + frac_part
        total_int_digits = len(int_part) + exp
        if total_int_digits < len(digits.rstrip("0")):
            # exponent smaller than the mantissa: value is not an integer -> garbage
            return raw, "excel_sci_notation_not_integer", True
        expanded = (digits + "0" * total_int_digits)[:total_int_digits]
        sig = len(digits.rstrip("0")) if digits.strip("0") else 0
        if sig < _MIN_SIG_DIGITS:
            return expanded, f"excel_sci_notation_lost_digits(sig={sig})", True
        return expanded, "excel_sci_notation_recovered", False
    m = _TRAILING_ZERO_RE.match(raw)
    if m:
        return m.group(1), "excel_trailing_dot_zero_stripped", False
    return raw, None, False


def normalize_text(raw: str) -> tuple[str, list[str], bool]:
    """Strip junk around the number. Returns (text, notes, unrecoverable)."""
    notes: list[str] = []
    text = (raw or "").strip().strip("'\"")
    # non-breaking spaces and unicode dashes
    text = text.replace(" ", " ").replace("–", "-").replace("—", "-")
    text, note, lost = unmangle_excel(text)
    if note:
        notes.append(note)
    return text, notes, lost


# --------------------------------------------------------------------------------------
# layer 2: parse + classify
# --------------------------------------------------------------------------------------

def _parse(text: str, region: str) -> Optional[pn.PhoneNumber]:
    try:
        return pn.parse(text, region or None)
    except pn.NumberParseException:
        return None


_NOT_POSSIBLE_LABEL = {
    pn.ValidationResult.TOO_SHORT: "too_short",
    pn.ValidationResult.TOO_LONG: "too_long",
    pn.ValidationResult.INVALID_COUNTRY_CODE: "invalid_country_code",
    pn.ValidationResult.INVALID_LENGTH: "invalid_length",
}


def validate_number(
    raw: str,
    country_in: Optional[str] = None,
    default_region: str = "IN",
    row: int = 0,
) -> ValidationResult:
    """Validate a single phone cell. `country_in` is the row's country column, if any."""
    res = ValidationResult(row=row, input=raw if raw is not None else "")
    expected_region = normalize_country(country_in)
    res.country_in = expected_region or (country_in or "").strip()

    text, notes, lost = normalize_text(res.input)
    res.notes.extend(notes)
    res.normalized = text

    if not text:
        res.verdict, res.reason = "drop", "blank"
        return res
    if lost:
        res.verdict, res.reason = "drop", "excel_float_lost_digits"
        return res

    # Parse with the row's own country first (so '0'-prefixed nationals resolve), else default.
    region = expected_region or default_region
    num = _parse(text, region)
    if num is None:
        res.verdict, res.reason = "drop", "unparseable"
        return res

    res.e164 = pn.format_number(num, pn.PhoneNumberFormat.E164)
    res.extension = num.extension or ""
    res.valid_format = pn.is_valid_number(num)
    res.country = pn.region_code_for_number(num) or ""
    res.region_hint = geocoder.description_for_number(num, "en") or ""
    res.carrier = carrier.name_for_number(num, "en") or ""

    if not res.valid_format:
        res.line_type = "unknown"
        if not pn.is_possible_number(num):
            why = pn.is_possible_number_with_reason(num)
            res.verdict, res.reason = "drop", "invalid_" + _NOT_POSSIBLE_LABEL.get(why, "not_possible")
        else:
            # right length, but the prefix is not an allocated range -> no offline answer
            res.verdict, res.reason = "drop", "invalid_unallocated_range"
        return res

    ntype = pn.number_type(num)
    res.line_type = LINE_TYPE_LABEL.get(ntype, "unknown")

    if ntype == T.MOBILE:
        res.verdict, res.reason = "keep", "valid_mobile"
    elif ntype == T.FIXED_LINE:
        res.verdict, res.reason = "drop", "landline"
    elif ntype == T.FIXED_LINE_OR_MOBILE:
        res.verdict, res.reason = "review", "fixed_or_mobile_needs_hlr"
    elif ntype == T.VOIP:
        res.verdict, res.reason = "review", "voip"
    elif ntype == T.UNKNOWN:
        res.verdict, res.reason = "review", "unknown_type"
    else:
        res.verdict, res.reason = "drop", res.line_type  # toll_free, premium_rate, pager, ...

    if res.extension and res.verdict == "keep":
        # mobiles do not have extensions; this is almost always a desk line typed as mobile
        res.verdict, res.reason = "review", "mobile_with_extension"

    if expected_region and res.country and res.country != expected_region:
        res.notes.append(f"country_column={expected_region} number={res.country}")
        if res.verdict != "drop":
            res.verdict, res.reason = "review", "wrong_country"
    return res


# --------------------------------------------------------------------------------------
# file level: dedupe + summary
# --------------------------------------------------------------------------------------

def _pick_column(fieldnames: Iterable[str], explicit: Optional[str], candidates: tuple[str, ...]) -> Optional[str]:
    names = list(fieldnames)
    if explicit:
        for n in names:
            if n.strip().lower() == explicit.strip().lower():
                return n
        raise KeyError(f"column {explicit!r} not found; available: {names}")
    lowered = {n.strip().lower(): n for n in names}
    for c in candidates:
        if c in lowered:
            return lowered[c]
    for n in names:  # fuzzy: any header containing a candidate word
        low = n.strip().lower()
        if any(c in low for c in candidates):
            return n
    return None


def validate_rows(
    rows: list[dict],
    phone_col: str,
    country_col: Optional[str],
    default_region: str = "IN",
) -> list[ValidationResult]:
    """Validate every row and mark repeats of the same E.164 number as duplicates."""
    results: list[ValidationResult] = []
    seen: dict[str, int] = {}
    for i, r in enumerate(rows, start=1):
        res = validate_number(
            r.get(phone_col, ""),
            r.get(country_col) if country_col else None,
            default_region=default_region,
            row=i,
        )
        # Only de-duplicate well-formed numbers: an invalid cell keeps its own drop reason.
        if res.e164 and res.valid_format:
            first = seen.get(res.e164)
            if first is None:
                seen[res.e164] = i
            else:
                res.notes.append(f"was:{res.verdict}/{res.reason}")
                res.verdict, res.reason = "drop", f"duplicate_of_row_{first}"
        results.append(res)
    return results


def summarize(results: list[ValidationResult]) -> "OrderedDict[str, int]":
    c: Counter = Counter()
    c["total_rows"] = len(results)
    for r in results:
        c["duplicates"] += r.is_duplicate
        c["blank_or_unparseable"] += r.reason in ("blank", "unparseable")
        c["excel_mangled"] += any(n.startswith("excel_") for n in r.notes)
        c["valid_format"] += r.valid_format
        c["invalid_format"] += (not r.valid_format) and r.reason != "blank"
        if r.valid_format:
            c[f"type_{r.line_type}"] += 1
        c["wrong_country"] += any(n.startswith("country_column=") for n in r.notes)
        c["with_extension"] += bool(r.extension)
        c[f"verdict_{r.verdict}"] += 1
    c["settled_offline_keep_plus_drop"] = c["verdict_keep"] + c["verdict_drop"]
    c["needs_paid_lookup_review"] = c["verdict_review"]
    order = [
        "total_rows", "valid_format", "invalid_format", "blank_or_unparseable", "excel_mangled",
        "type_mobile", "type_landline", "type_fixed_or_mobile", "type_voip", "type_toll_free",
        "type_premium_rate", "wrong_country", "duplicates", "with_extension",
        "verdict_keep", "verdict_drop", "verdict_review",
        "settled_offline_keep_plus_drop", "needs_paid_lookup_review",
    ]
    out: "OrderedDict[str, int]" = OrderedDict()
    for k in order:
        out[k] = int(c.get(k, 0))
    for k in sorted(c):  # any extra type_* buckets
        if k not in out:
            out[k] = int(c[k])
    return out


def format_summary(summary: dict, results: Optional[list[ValidationResult]] = None) -> str:
    lines = ["metric                             count", "-" * 40]
    for k, v in summary.items():
        lines.append(f"{k:34} {v:5d}")
    if results:
        lines += ["", "verdict / reason breakdown", "-" * 40]
        pairs = Counter((r.verdict, r.base_reason) for r in results)
        for (verdict, reason), n in sorted(pairs.items()):
            lines.append(f"{verdict:7} {reason:26} {n:5d}")
    return "\n".join(lines)


def validate_file(
    in_path: str | Path,
    out_path: Optional[str | Path] = None,
    phone_col: Optional[str] = None,
    country_col: Optional[str] = None,
    default_region: str = "IN",
    keep_input_columns: bool = True,
) -> tuple[list[ValidationResult], dict, Path]:
    """CSV in -> CSV out (+ .summary.json / .summary.txt next to it)."""
    in_path = Path(in_path)
    with in_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    pcol = _pick_column(fieldnames, phone_col, PHONE_COLUMN_CANDIDATES)
    if pcol is None:
        raise KeyError(f"could not find a phone column in {fieldnames}; pass --phone-col")
    ccol = _pick_column(fieldnames, country_col, COUNTRY_COLUMN_CANDIDATES)
    if ccol == pcol:
        ccol = None

    results = validate_rows(rows, pcol, ccol, default_region)
    summary = summarize(results)

    out_path = Path(out_path) if out_path else in_path.with_name(in_path.stem + "_validated.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    extra_cols = [c for c in fieldnames if c != pcol and c not in OUTPUT_COLUMNS] if keep_input_columns else []
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS + extra_cols)
        w.writeheader()
        for r, src in zip(results, rows):
            d = r.to_row()
            for c in extra_cols:
                d[c] = src.get(c, "")
            w.writerow(d)
    with out_path.with_suffix(".summary.json").open("w", encoding="utf-8") as fh:
        json.dump({"input": str(in_path), "phone_column": pcol, "country_column": ccol,
                   "default_region": default_region, "counts": summary}, fh, indent=2)
    with out_path.with_suffix(".summary.txt").open("w", encoding="utf-8") as fh:
        fh.write(format_summary(summary, results) + "\n")
    return results, summary, out_path
