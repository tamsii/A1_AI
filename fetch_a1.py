#!/usr/bin/env python3
"""Download the A1 phone list and save it as a1_devices.json.

Standard library only. The A1 endpoint returns every phone variant in one
response (an object keyed by SKU), so there is no paging.
"""

import argparse
import sys

from fetch_telekom import FetchError, request_page, write_atomic

A1_URL = ("https://www.a1.mk/INTERSHOP/web/WFS/A1MK-ShopMK-Site/mk_MK/-/MKD/"
          "GetCategoryProducts-Fetch?CatalogID=Phones&CategoryName=Phones")
HEADERS = {"Referer": "https://www.a1.mk/webshop/mk/phones"}


def count_products(data):
    """Number of entries that look like A1 products (have a deviceName)."""
    if isinstance(data, dict) and isinstance(data.get("devices"), list):
        items = data["devices"]
    elif isinstance(data, dict):
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        return 0
    return sum(1 for v in items if isinstance(v, dict) and v.get("deviceName"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download the A1 phone list.")
    ap.add_argument("--url", default=A1_URL, help="A1 GetCategoryProducts URL")
    ap.add_argument("--output", "-o", default="a1_devices.json",
                    help="output file (default: %(default)s)")
    ap.add_argument("--timeout", type=float, default=60, help="seconds for the request")
    ap.add_argument("--retries", type=int, default=3, help="retries on network/5xx errors")
    args = ap.parse_args(argv)

    print("Fetching A1 phones ...")
    try:
        data = request_page({"url": args.url, "method": "GET", "headers": HEADERS},
                            {}, args.timeout, args.retries)
        n = count_products(data)
        if n == 0:
            raise FetchError("The A1 response contains no products with a 'deviceName'. "
                             "The URL or the response format may have changed.")
    except FetchError as e:
        print(f"ERROR: {e}\nNothing written; {args.output} was left unchanged.", file=sys.stderr)
        return 1

    write_atomic(args.output, data)
    print(f"Wrote {n} A1 product variants to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
