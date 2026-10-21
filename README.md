# champ-phone-validator

Mobile number validation for the Data Team. CSV in, CSV out.

Layers 1 and 2 run fully offline on the [`phonenumbers`](https://pypi.org/project/phonenumbers/)
library (Python port of Google libphonenumber). Layers 3 and 4 (HLR / DND lookups) are a paid
API step and are proposed separately; this tool decides which rows actually need that spend.

| Layer | What it does | Status |
|------:|--------------|--------|
| 1 | Normalise the cell: `+91` / `0` / `91` prefixes, spaces, dashes, dots, parentheses, `tel:` prefix, unicode dashes, Excel float / scientific-notation damage, extensions | done |
| 2 | Offline classification: valid format, line type (mobile / landline / VoIP / toll-free), country, carrier, region hint, wrong-country check, de-duplication on E.164 | done |
| 3 | HLR lookup (is the SIM live, ported carrier) | proposal, needs capped API key |
| 4 | DND / TRAI NDNC scrub | proposal |

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

# generate the synthetic 200-row test file (no real data)
.\.venv\Scripts\python scripts\make_sample.py

# validate any CSV that has a phone column (country column optional)
.\.venv\Scripts\python -m phone_validator data\samples\synthetic_200.csv --out data\output\synthetic_200_validated.csv

# tests
.\.venv\Scripts\python -m pytest -q
```

Options: `--phone-col`, `--country-col` (both auto-detected from common header names),
`--region IN` (default country for numbers written without a country code), `--no-extra-cols`.

## Output

`<name>_validated.csv` with one row per input row, same order:

| column | meaning |
|--------|---------|
| `row` | 1-based input row |
| `input` | the cell exactly as received |
| `country_in` | the row's country column, normalised to ISO-2 |
| `normalized` | the cell after layer-1 cleanup |
| `e164` | canonical `+<cc><number>` form, used for de-duplication |
| `valid_format` | `yes` if the number is an allocated range in its country |
| `line_type` | `mobile`, `landline`, `fixed_or_mobile`, `voip`, `toll_free`, `premium_rate`, `unknown` |
| `country` | ISO-2 country the number actually belongs to |
| `carrier` | original carrier for the prefix (see caveat below) |
| `region_hint` | city / state / country from the numbering plan |
| `extension` | any `x123` / `ext 45` suffix |
| `verdict` | `keep`, `drop`, or `review` |
| `reason` | why, plus bracketed notes (Excel repair, country mismatch, what the verdict was before de-dup) |

Any other input columns are copied through unchanged.

Alongside it: `<name>_validated.summary.json` and `.summary.txt` with the count table.

## Verdict rules

| verdict | when |
|---------|------|
| `keep` | valid, type is `mobile`, country matches the country column (if present), first occurrence |
| `drop` | blank / unparseable / wrong length / unallocated range / landline / toll-free / premium / Excel lost the digits / duplicate of an earlier row |
| `review` | cannot be settled offline: `fixed_or_mobile` ranges (every US and Canadian number, some Indian blocks), VoIP, number is from a different country than the column says, mobile written with an extension |

`keep + drop` = rows settled with zero paid lookups. `review` = rows that need layer 3.

## Synthetic sample results (200 rows, 21 Oct 2026)

| metric | count |
|--------|------:|
| total rows | 200 |
| valid format | 182 |
| invalid format | 16 |
| mobile | 139 |
| landline | 22 |
| fixed-or-mobile (needs HLR) | 12 |
| VoIP | 4 |
| toll-free | 5 |
| wrong country | 10 |
| duplicates | 20 |
| Excel-mangled cells | 9 (6 repaired, 3 unrecoverable) |
| **keep** | **108** |
| **drop** | **65** |
| **review** | **27** |
| settled offline | 173 / 200 (86.5%) |
| needs paid lookup | 27 / 200 (13.5%) |

Every row's verdict matches the `expected_verdict` column in the sample (checked by the test suite).
The sample is deliberately adversarial (a quarter of it is broken, foreign or duplicated), so
the real Data Team file should settle a higher share offline.

## Caveats

- **Carrier is the original allocation, not the current one.** After mobile number portability
  the prefix still says Airtel even if the SIM moved to Jio. Only an HLR lookup (layer 3) knows.
- **Offline cannot tell if a number is live.** `keep` means "well-formed Indian mobile", not
  "will ring". That is the layer 3 question.
- **US / Canada numbers are always `review`.** The North American plan does not separate
  mobile from landline ranges.
- **Carrier and region data for India is only as fresh as the `phonenumbers` release.** Pin
  bumps in `requirements.txt` refresh it.

## Data handling

No customer or Data Team phone lists are committed. `data/output/` and `data/real/` are
git-ignored; real samples stay on the VM. Only `data/samples/synthetic_200.csv` (fabricated
numbers, fixed seed) is in the repo.
