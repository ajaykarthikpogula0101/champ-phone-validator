"""CLI: python -m phone_validator input.csv [--out output.csv] [--phone-col X] [--country-col Y] [--region IN]"""
import argparse
import sys

from .validate import validate_file, format_summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="phone_validator", description="Offline phone validation (layers 1+2).")
    p.add_argument("input", help="CSV with a phone column")
    p.add_argument("--out", help="output CSV path (default: <input>_validated.csv next to the input)")
    p.add_argument("--phone-col", help="phone column name (auto-detected if omitted)")
    p.add_argument("--country-col", help="country column name (auto-detected if omitted)")
    p.add_argument("--region", default="IN", help="default region for numbers without a country code (default IN)")
    p.add_argument("--no-extra-cols", action="store_true", help="do not copy the other input columns to the output")
    a = p.parse_args(argv)

    results, summary, out = validate_file(
        a.input, a.out, a.phone_col, a.country_col, a.region, keep_input_columns=not a.no_extra_cols
    )
    print(format_summary(summary, results))
    print()
    print("wrote", out)
    print("wrote", out.with_suffix(".summary.json"))
    print("wrote", out.with_suffix(".summary.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
