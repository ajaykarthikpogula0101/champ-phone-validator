"""
Generate a deterministic ~200-row synthetic phone CSV that exercises every verdict path.

No real customer data: every number is fabricated from allocated-looking prefixes with a
fixed random seed, so the file is safe to commit and the counts are reproducible.

Columns: id, name, phone, country, expected_verdict, note
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

import phonenumbers as pn

SEED = 2026_10_21
OUT = Path(__file__).resolve().parent.parent / "data" / "samples" / "synthetic_200.csv"

rng = random.Random(SEED)

FIRST = ["Aarav", "Diya", "Rohan", "Priya", "Kiran", "Meera", "Arjun", "Sneha", "Vikram", "Ananya",
         "John", "Emma", "Liam", "Olivia", "Noah", "Ava", "Omar", "Fatima", "Yusuf", "Layla"]
LAST = ["Sharma", "Reddy", "Patel", "Iyer", "Khan", "Nair", "Gupta", "Mehta", "Smith", "Brown",
        "Taylor", "Wilson", "Al Maktoum", "Haddad", "Rao", "Das"]

# Indian mobile prefixes (first 4 digits) from well-known allocated ranges
IN_MOBILE_PREFIX = ["9845", "9886", "9900", "9972", "9741", "9880", "9008", "9036",   # Karnataka
                    "9810", "9811", "9871", "9899", "9911", "9999",                   # Delhi
                    "9820", "9821", "9833", "9867", "9870", "9892",                   # Mumbai
                    "9000", "9030", "9052", "9100", "9177",                           # Hyderabad
                    "7000", "7001", "7200", "7300", "7400", "7500",                   # Jio-ish 7x
                    "6300", "6301", "6302", "8000", "8100", "8500", "8800", "8888"]
IN_LANDLINE_PREFIX = ["080", "011", "022", "044", "040", "033", "020"]


def _is_type(national: str, wanted: int) -> bool:
    """India's numbering plan is patchy (some 72xx / 080-5xxx blocks are 'fixed or mobile'),
    so re-draw until the library agrees the fabricated number is purely the type we want.
    The validator is still tested independently on these rows via expected_verdict."""
    try:
        return pn.number_type(pn.parse(national, "IN")) == wanted
    except pn.NumberParseException:
        return False


def in_mobile() -> str:
    while True:
        n = rng.choice(IN_MOBILE_PREFIX) + f"{rng.randint(0, 999999):06d}"
        if _is_type(n, pn.PhoneNumberType.MOBILE):
            return n


def in_landline() -> str:
    while True:
        n = rng.choice(IN_LANDLINE_PREFIX) + f"{rng.randint(20000000, 49999999):08d}"
        if _is_type(n, pn.PhoneNumberType.FIXED_LINE):
            return n


def us_number() -> str:
    area = rng.choice(["212", "415", "312", "617", "206", "305", "702", "503"])
    return f"{area}555{rng.randint(100, 199):04d}"  # 555-01xx are reserved fictional numbers


def uk_mobile() -> str:
    return "7" + rng.choice(["400", "411", "711", "800", "931", "500"]) + f"{rng.randint(0, 999999):06d}"


def uk_landline() -> str:
    return "20" + rng.choice(["7946", "3456", "8765"]) + f"{rng.randint(0, 9999):04d}"


def uk_voip() -> str:
    return "56" + f"{rng.randint(0, 99999999):08d}"


def ae_mobile() -> str:
    return rng.choice(["50", "52", "54", "55", "56", "58"]) + f"{rng.randint(0, 9999999):07d}"


def fmt_in(national: str, style: str) -> str:
    """Render an Indian 10-digit national mobile in the formats the plan lists."""
    a, b = national[:5], national[5:]
    return {
        "plus":        f"+91 {a} {b}",
        "plus_nospace": f"+91{national}",
        "zero":        f"0{national}",
        "91":          f"91{national}",
        "bare":        national,
        "dash":        f"{a}-{b}",
        "dashes_cc":   f"+91-{a}-{b}",
        "parens":      f"(+91) {a} {b}",
        "spaces":      f"{national[:3]} {national[3:6]} {national[6:]}",
        "dotted":      f"{a}.{b}",
    }[style]


rows: list[dict] = []


def add(phone: str, country: str, expected: str, note: str) -> None:
    n = len(rows) + 1
    rows.append({
        "id": f"C{n:04d}",
        "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
        "phone": phone,
        "country": country,
        "expected_verdict": expected,
        "note": note,
    })


# --- 1. valid Indian mobiles in all the formats the plan calls out (80 rows) -----------
styles = ["plus", "plus_nospace", "zero", "91", "bare", "dash", "dashes_cc", "parens", "spaces", "dotted"]
for i in range(80):
    style = styles[i % len(styles)]
    country = rng.choice(["India", "IN", "india", "IND", "In"])
    add(fmt_in(in_mobile(), style), country, "keep", f"in_mobile_{style}")

# --- 2. Indian landlines (15 rows) -----------------------------------------------------
for _ in range(15):
    ll = in_landline()
    style = rng.choice(["bare", "plus", "spaced"])
    text = {"bare": ll, "plus": f"+91 {ll[1:]}", "spaced": f"{ll[:3]} {ll[3:7]} {ll[7:]}"}[style]
    add(text, "India", "drop", "in_landline")

# --- 3. foreign numbers with correct country column (30 rows) --------------------------
for _ in range(10):
    n = us_number()
    add(f"+1 {n[:3]} {n[3:6]} {n[6:]}", rng.choice(["USA", "US", "United States"]), "review", "us_fixed_or_mobile")
for _ in range(6):
    n = uk_mobile()
    add(f"+44 {n[:4]} {n[4:]}", rng.choice(["UK", "GB", "United Kingdom"]), "keep", "uk_mobile")
for _ in range(4):
    n = uk_landline()
    add(f"+44 {n[:2]} {n[2:6]} {n[6:]}", "UK", "drop", "uk_landline")
for _ in range(4):
    n = uk_voip()
    add(f"+44 {n[:2]} {n[2:6]} {n[6:]}", "UK", "review", "uk_voip")
for _ in range(6):
    n = ae_mobile()
    add(f"+971 {n[:2]} {n[2:5]} {n[5:]}", rng.choice(["UAE", "AE", "United Arab Emirates"]), "keep", "ae_mobile")

# --- 4. toll-free / premium (5 rows) ----------------------------------------------------
for _ in range(3):
    add(f"+1 800 555 {rng.randint(100, 199):04d}", "US", "drop", "us_toll_free")
for _ in range(2):
    add(f"1800 {rng.randint(100, 999)} {rng.randint(1000, 9999)}", "India", "drop", "in_toll_free")

# --- 5. wrong country column (10 rows) --------------------------------------------------
for _ in range(4):
    n = uk_mobile()
    add(f"+44 {n[:4]} {n[4:]}", "India", "review", "uk_mobile_in_india_column")
for _ in range(3):
    add(f"+91 {in_mobile()[:5]} {rng.randint(0, 99999):05d}", "USA", "review", "in_mobile_in_usa_column")
for _ in range(3):
    n = ae_mobile()
    add(f"+971{n}", "IN", "review", "ae_mobile_in_india_column")

# --- 6. broken inputs (25 rows) --------------------------------------------------------
broken = [
    ("", "India", "drop", "blank"),
    ("   ", "India", "drop", "blank_spaces"),
    ("N/A", "India", "drop", "na_text"),
    ("call me", "India", "drop", "words"),
    ("12345", "India", "drop", "too_short"),
    ("98450", "India", "drop", "too_short_5"),
    ("984501234", "India", "drop", "nine_digits"),
    ("98450123456", "India", "drop", "eleven_digits"),
    ("+91 98450 123456", "India", "drop", "eleven_digits_cc"),
    ("+91 12345 67890", "India", "drop", "in_landline_range_typed_as_mobile"),
    ("+91 5845012345", "India", "drop", "unallocated_5_prefix"),
    ("+91 4845012345", "India", "drop", "unallocated_4_prefix"),
    ("0000000000", "India", "drop", "all_zeros"),
    ("9999999999", "India", "keep", "repeated_9s_is_a_valid_delhi_range"),
    ("+91 98450 1234O", "India", "drop", "letter_O_for_zero"),
    ("+9198450123", "India", "drop", "cc_then_short"),
    ("++91 9886011223", "India", "keep", "double_plus_tolerated_by_parser"),
    ("+91 (0) 97410 55667", "India", "keep", "zero_in_parens_after_cc"),
    ("+91 99000 77889 x123", "India", "review", "mobile_with_extension"),
    ("080 2345 6789 ext 45", "India", "drop", "landline_with_extension"),
    ("+1 212 555 0100 x9", "US", "review", "us_with_extension"),
    ("+999 1234567", "India", "drop", "invalid_country_code"),
    ("+91 98800 33445 ", "India", "keep", "trailing_nbsp"),
    ("‐90080–66778’", "India", "keep", "unicode_quotes_and_dash"),
    ("tel:+91-98100-99887", "India", "keep", "tel_uri_prefix"),
]
for ph, c, exp, note in broken:
    add(ph, c, exp, note)

# --- 7. Excel mangling (10 rows) ---------------------------------------------------------
excel = [
    ("9.19845E+11", "India", "drop", "excel_sci_6_sig_digits_lost"),
    ("9.18E+11", "India", "drop", "excel_sci_3_sig_digits_lost"),
    ("9.19886022334E+11", "India", "keep", "excel_sci_full_precision_recovered"),
    ("9,19972033445E+11", "India", "keep", "excel_sci_comma_decimal_recovered"),
    ("919845012346.0", "India", "keep", "excel_trailing_dot_zero"),
    ("9845012347.00", "India", "keep", "excel_trailing_dot_zero_zero_national"),
    ("4.47400123456E+11", "UK", "keep", "excel_sci_uk_recovered"),
    ("1.2125550123E+10", "US", "review", "excel_sci_us_recovered_fixed_or_mobile"),
    ("9.8450123E+9", "India", "drop", "excel_sci_national_lost_digits"),
    ("98450123450000", "India", "drop", "excel_expanded_too_long"),
]
for ph, c, exp, note in excel:
    add(ph, c, exp, note)

# --- 8. duplicates: same number, different spellings (25 rows: 5 originals + 20 dupes) ---
dupe_bases = [in_mobile() for _ in range(5)]
for i, base in enumerate(dupe_bases):
    add(fmt_in(base, "plus"), "India", "keep", f"dupe_set_{i}_original")
for i, base in enumerate(dupe_bases):
    for style in ["zero", "91", "bare", "dash"]:
        add(fmt_in(base, style), "India", "drop", f"dupe_set_{i}_{style}")

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["id", "name", "phone", "country", "expected_verdict", "note"])
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} rows -> {OUT}")
