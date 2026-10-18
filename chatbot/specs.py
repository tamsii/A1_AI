#!/usr/bin/env python3
"""Create or update phone_specs.csv, the table of phone specifications the
chatbot uses (screen, camera, battery, ...).

    python3 chatbot/specs.py

There is one row per phone model, not per storage size or bundle, so
"Galaxy S26 5G 256" and "Galaxy S26 5G 512" share the row "samsung|s26".
Running it again keeps everything already filled in and adds rows for new
phones in a1_devices.json. Fill the table in Excel or Google Sheets and save
it as CSV (UTF-8 if Excel asks). Empty cells are fine.
"""

import argparse
import csv
import os
import sys

from catalog import SPEC_COLUMNS, Catalog, read_specs

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Create or update the phone specs table.")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(HERE), "a1_devices.json"))
    ap.add_argument("--output", "-o", default=None,
                    help="specs file (default: phone_specs.csv next to the data file)")
    args = ap.parse_args(argv)

    catalog = Catalog(args.data)
    path = args.output or catalog.specs_path
    old = read_specs(path) if os.path.exists(path) else {}

    models = {}  # model key -> phone names, in catalog order
    for p in catalog.phones:
        models.setdefault(p["model_key"], []).append(p["title"])

    rows = []
    for key, names in models.items():
        row = {"model": key, "phones": " / ".join(names)}
        row.update({c: old.get(key, {}).get(c, "") for c in SPEC_COLUMNS})
        rows.append(row)
    # Keep rows for phones A1 no longer sells, at the end, in case they return.
    for key, specs in old.items():
        if key not in models:
            rows.append({"model": key, "phones": "(not in the current A1 list)", **specs})

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel shows Cyrillic right
        w = csv.DictWriter(f, fieldnames=["model", "phones", *SPEC_COLUMNS], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)

    filled = sum(1 for r in rows[:len(models)] if any(r[c] for c in SPEC_COLUMNS))
    print(f"Wrote {path}: {len(models)} phone models, {filled} with specs filled in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
