#!/usr/bin/env python3
"""Download A1 and Telekom prices, then build the comparison.

Stops at the first failure; the files from the last good run stay as they were.
Any extra arguments are passed to compare_phones.py (e.g. --currency MKD).
"""

import sys

import compare_phones
import fetch_a1
import fetch_telekom


def main():
    for step, run in (("A1 download", lambda: fetch_a1.main([])),
                      ("Telekom download", lambda: fetch_telekom.main([])),
                      ("comparison", lambda: compare_phones.main(sys.argv[1:]))):
        if run() != 0:
            print(f"\nStopped: {step} failed. Nothing after it was run.", file=sys.stderr)
            return 1
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
