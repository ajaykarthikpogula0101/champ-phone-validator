"""Edge-case tests for layers 1 and 2. Run: python -m pytest -q"""
import csv
import subprocess
import sys
from pathlib import Path

import pytest

from phone_validator import validate_number, validate_rows, summarize, validate_file
from phone_validator.validate import unmangle_excel, normalize_country

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "samples" / "synthetic_200.csv"


# ---------------------------------------------------------------- prefixes and separators
@pytest.mark.parametrize("raw", [
    "+91 98450 12345", "+919845012345", "09845012345", "919845012345", "9845012345",
    "98450-12345", "+91-98450-12345", "(+91) 98450 12345", "984 501 2345", "98450.12345",
    "tel:+91-98450-12345", "++91 9845012345", "+91 (0) 98450 12345", "+91 98450 12345 ",
    "‐98450–12345’",
])
def test_indian_mobile_formats_all_normalise_to_same_e164(raw):
    r = validate_number(raw, "India")
    assert r.e164 == "+919845012345"
    assert r.verdict == "keep" and r.reason == "valid_mobile"
    assert r.line_type == "mobile" and r.country == "IN" and r.carrier


def test_default_region_applies_when_no_country_column():
    assert validate_number("9845012345").e164 == "+919845012345"
    # same digits read as a US number land in a North-Carolina fixed-or-mobile range
    r = validate_number("9845012345", default_region="US")
    assert (r.country, r.verdict) == ("US", "review")


# ---------------------------------------------------------------- line types
def test_indian_landline_is_dropped_with_city_hint():
    r = validate_number("080 2345 6789", "IN")
    assert (r.verdict, r.reason, r.line_type) == ("drop", "landline", "landline")
    assert "Bangalore" in r.region_hint


def test_us_numbers_cannot_be_settled_offline():
    r = validate_number("+1 212 555 0123", "US")
    assert (r.verdict, r.reason) == ("review", "fixed_or_mobile_needs_hlr")


def test_toll_free_and_voip():
    assert validate_number("+1 800 555 0199", "US").reason == "toll_free"
    assert validate_number("1800 123 4567", "IN").reason == "toll_free"
    assert validate_number("+44 56 0123 4567", "GB").reason == "voip"


# ---------------------------------------------------------------- extensions
def test_extension_is_captured_and_mobile_with_extension_goes_to_review():
    r = validate_number("+91 98450 12345 x123", "IN")
    assert r.extension == "123" and r.e164 == "+919845012345"
    assert (r.verdict, r.reason) == ("review", "mobile_with_extension")
    r = validate_number("080 2345 6789 ext 45", "IN")
    assert r.extension == "45" and r.reason == "landline"


# ---------------------------------------------------------------- Excel float mangling
@pytest.mark.parametrize("raw,expanded,lost", [
    ("9.19845012345E+11", "919845012345", False),
    ("9,19845012345E+11", "919845012345", False),
    ("9.19845E+11", "919845000000", True),
    ("9.18E+11", "918000000000", True),
    ("919845012345.0", "919845012345", False),
    ("9845012345.00", "9845012345", False),
])
def test_unmangle_excel(raw, expanded, lost):
    text, note, lost_flag = unmangle_excel(raw)
    assert text == expanded and lost_flag is lost and note


def test_excel_lost_digits_is_dropped_not_kept():
    r = validate_number("9.19845E+11", "IN")
    assert (r.verdict, r.reason) == ("drop", "excel_float_lost_digits")
    assert "excel_sci_notation_lost_digits(sig=6)" in r.notes


def test_excel_full_precision_is_recovered():
    r = validate_number("9.19845012345E+11", "IN")
    assert r.e164 == "+919845012345" and r.verdict == "keep"


# ---------------------------------------------------------------- country column
@pytest.mark.parametrize("value,expected", [
    ("India", "IN"), ("IND", "IN"), ("in", "IN"), ("USA", "US"), ("United States", "US"),
    ("UK", "GB"), ("United Kingdom", "GB"), ("UAE", "AE"), ("", ""), (None, ""), ("Narnia", ""),
])
def test_normalize_country(value, expected):
    assert normalize_country(value) == expected


def test_number_in_wrong_country_column_goes_to_review():
    r = validate_number("+44 7400 123456", "India")
    assert (r.verdict, r.reason, r.country, r.country_in) == ("review", "wrong_country", "GB", "IN")
    assert any(n.startswith("country_column=IN number=GB") for n in r.notes)


def test_wrong_country_does_not_rescue_a_drop():
    r = validate_number("+44 20 7946 0958", "India")  # UK landline under an India column
    assert r.verdict == "drop" and r.reason == "landline"


def test_national_number_resolves_against_row_country_before_default():
    r = validate_number("07400 123456", "UK", default_region="IN")
    assert r.e164 == "+447400123456" and r.verdict == "keep"


# ---------------------------------------------------------------- invalid inputs
@pytest.mark.parametrize("raw,reason", [
    ("", "blank"), ("   ", "blank"), ("N/A", "unparseable"), ("call me", "unparseable"),
    ("12345", "invalid_too_short"), ("98450123450000", "invalid_too_long"),
    ("+91 98450 123456", "invalid_unallocated_range"),  # 11 digits is a *possible* IN length
    ("+91 5845012345", "invalid_unallocated_range"), ("+999 1234567", "unparseable"),
])
def test_invalid_inputs_are_dropped_with_reason(raw, reason):
    r = validate_number(raw, "IN")
    assert r.verdict == "drop" and r.reason == reason


# ---------------------------------------------------------------- duplicates
def test_duplicates_detected_across_formats_first_occurrence_wins():
    rows = [{"phone": p} for p in ["+91 98450 12345", "09845012345", "919845012345", "+91 98860 11223"]]
    res = validate_rows(rows, "phone", None)
    assert [r.verdict for r in res] == ["keep", "drop", "drop", "keep"]
    assert res[1].reason == "duplicate_of_row_1" and res[2].reason == "duplicate_of_row_1"
    assert "was:keep/valid_mobile" in res[1].notes
    assert summarize(res)["duplicates"] == 2


# ---------------------------------------------------------------- whole-file run
def test_synthetic_sample_matches_every_expected_verdict(tmp_path):
    results, summary, out = validate_file(SAMPLE, tmp_path / "out.csv")
    with SAMPLE.open(encoding="utf-8-sig", newline="") as fh:
        expected = [r["expected_verdict"] for r in csv.DictReader(fh)]
    got = [r.verdict for r in results]
    mismatches = [(i + 1, e, g) for i, (e, g) in enumerate(zip(expected, got)) if e != g]
    assert mismatches == []
    assert summary["total_rows"] == 200
    assert summary["settled_offline_keep_plus_drop"] + summary["needs_paid_lookup_review"] == 200
    assert out.exists() and out.with_suffix(".summary.json").exists()
    with out.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert header[:13] == ["row", "input", "country_in", "normalized", "e164", "valid_format", "line_type",
                           "country", "carrier", "region_hint", "extension", "verdict", "reason"]
    assert "expected_verdict" in header  # extra input columns are carried through


def test_cli_runs_end_to_end(tmp_path):
    out = tmp_path / "cli_out.csv"
    proc = subprocess.run(
        [sys.executable, "-m", "phone_validator", str(SAMPLE), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert "verdict_keep" in proc.stdout and out.exists()


def test_phone_column_autodetect_and_missing_column(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Name,Mobile No,Country\nA,9845012345,India\n", encoding="utf-8")
    results, _, _ = validate_file(p, tmp_path / "o.csv")
    assert results[0].e164 == "+919845012345"
    p.write_text("Name,Email\nA,a@b.c\n", encoding="utf-8")
    with pytest.raises(KeyError):
        validate_file(p, tmp_path / "o2.csv")
