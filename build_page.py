#!/usr/bin/env python3
"""Build phones_a1_vs_telekom.html, a self-contained page for the comparison.

The JSON is embedded in the page, so it opens with a double-click (no server,
no internet). compare_phones.py calls this automatically; you can also run it
on its own: python build_page.py [comparison.json] [page.html]
"""

import json
import os
import sys


TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_template.html")


def build_page(result, path):
    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    # "</" inside a <script> tag would end it early, so escape it
    payload = json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    html = template.replace("/*DATA*/", payload)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    src = argv[0] if argv else "phones_a1_vs_telekom.json"
    dst = argv[1] if len(argv) > 1 else os.path.splitext(src)[0] + ".html"
    with open(src, encoding="utf-8") as f:
        result = json.load(f)
    build_page(result, dst)
    print(f"Wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
