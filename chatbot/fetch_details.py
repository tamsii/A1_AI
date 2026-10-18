#!/usr/bin/env python3
"""Read the "Детални карактеристики" table (and "Што има во кутијата") from
each phone's page on a1.mk and save them to phone_details.json, which the
chatbot uses next to the prices.

    python3 chatbot/fetch_details.py --test https://www.a1.mk/webshop/mk/phones/apple-iphone-18-pro-512gb/1000069047
    python3 chatbot/fetch_details.py              # all phones in a1_devices.json
    python3 chatbot/fetch_details.py --only-missing

--test shows what was found on one page (a URL or a saved .html file) without
saving anything. Run it first: if the table comes out wrong, the page layout
is different from what this script expects.

Standard library only. The table is read from the page text, not from fixed
HTML classes: the rows after the "Детални карактеристики" heading are taken
as label / value pairs.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

from catalog import Catalog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetch_telekom import write_atomic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HEADERS = {"User-Agent": "Mozilla/5.0 (A1 phone assistant; specs fetch)",
           "Accept-Language": "mk,en;q=0.8"}

TABLE_HEADING = "детални карактеристики"
BOX_HEADING = "што има во кутијата"
# Labels seen on a1.mk. Other labels are read too; these help keep label and
# value in step when a value cell is empty.
KNOWN_LABELS = {
    "мрежа", "тип на sim", "екран", "големина на екранот", "камера", "оперативен систем",
    "cpu", "процесор", "вградена меморија", "ram меморија", "батерија", "македонско мени",
    "телефонот работи само со a1 sim картичка", "предна камера", "димензии", "тежина",
    "гаранција", "резолуција", "bluetooth", "wi-fi", "nfc", "боја",
}
# Text that means the table is over.
STOP_TEXTS = ("што има во кутијата", "основни карактеристики", "слични", "препорач",
              "поврзани", "додади во", "купи", "контакт", "©", "колачиња", "cookie")
MAX_ROWS = 40

INLINE_TAGS = {"b", "strong", "i", "em", "u", "sup", "sub", "small", "abbr", "font"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


class TextChunks(HTMLParser):
    """The page as a list of text pieces, split wherever an element starts or
    ends (except inline formatting), so table cells and spans come out separately."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.cur = []
        self.skip = 0

    def _cut(self):
        text = " ".join("".join(self.cur).split())
        if text:
            self.chunks.append(text)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag not in INLINE_TAGS:
            self._cut()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag not in INLINE_TAGS:
            self._cut()

    def handle_data(self, data):
        if not self.skip:
            self.cur.append(data)

    def close(self):
        super().close()
        self._cut()


def is_stop(text):
    t = text.lower()
    return any(t.startswith(s) for s in STOP_TEXTS)


def looks_like_label(text):
    return text.lower() in KNOWN_LABELS or (len(text) <= 45 and not text.endswith((".", "!", "?")))


def parse_page(html):
    """{"fields": {label: value}, "in_box": [items]} from a phone page."""
    p = TextChunks()
    p.feed(html)
    p.close()
    chunks = p.chunks
    lower = [c.lower().rstrip(":?") for c in chunks]

    fields = {}
    if TABLE_HEADING in lower:
        i = lower.index(TABLE_HEADING) + 1
        while i < len(chunks) and len(fields) < MAX_ROWS:
            label = chunks[i]
            if is_stop(label) or not looks_like_label(label):
                break
            value = chunks[i + 1] if i + 1 < len(chunks) else ""
            if value.lower() in KNOWN_LABELS or is_stop(value):
                i += 1  # empty value cell: the next text is already a label
                continue
            fields[label.rstrip(":")] = value
            i += 2

    in_box = []
    if BOX_HEADING in lower:
        i = lower.index(BOX_HEADING) + 1
        while i < len(chunks) and len(in_box) < 15 and not is_stop(chunks[i]) \
                and lower[i] != TABLE_HEADING and len(chunks[i]) <= 80:
            in_box.append(chunks[i])
            i += 1
    return {"fields": fields, "in_box": in_box}


def get(url, timeout=30, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(resp.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            if attempt == retries or (400 <= e.code < 500 and e.code != 429):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries:
                raise
        time.sleep(2 ** attempt)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read phone specs from the a1.mk phone pages.")
    ap.add_argument("--test", metavar="URL_OR_FILE",
                    help="show what is read from one page, save nothing")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(HERE), "a1_devices.json"))
    ap.add_argument("--output", "-o", default=None,
                    help="default: phone_details.json next to the data file")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip phones that already have details")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many pages")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between pages")
    args = ap.parse_args(argv)

    if args.test:
        if os.path.exists(args.test):
            with open(args.test, encoding="utf-8", errors="replace") as f:
                html = f.read()
        else:
            html = get(args.test)
        result = parse_page(html)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["fields"]:
            print("\nNo 'Детални карактеристики' table found on this page.", file=sys.stderr)
            return 1
        return 0

    catalog = Catalog(args.data)
    path = args.output or catalog.details_path
    details = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            details = json.load(f)

    phones = [p for p in catalog.phones if p["url"]]
    if args.only_missing:
        phones = [p for p in phones if not details.get(p["name"], {}).get("fields")]
    if args.limit:
        phones = phones[:args.limit]

    today = datetime.date.today().isoformat()
    found = empty = failed = 0
    for n, p in enumerate(phones, 1):
        print(f"[{n}/{len(phones)}] {p['title']} ...", end=" ", flush=True)
        try:
            result = parse_page(get(p["url"]))
        except (urllib.error.URLError, OSError) as e:
            print(f"failed: {e}")
            failed += 1
            continue
        if result["fields"]:
            details[p["name"]] = {"url": p["url"], "checked": today, **result}
            found += 1
            print(f"{len(result['fields'])} rows")
        else:
            empty += 1
            print("no table on the page")
        if n % 10 == 0:
            write_atomic(path, details)  # keep progress if it stops halfway
        time.sleep(args.delay)

    write_atomic(path, details)
    print(f"\nWrote {path}: {found} phones read now, {empty} without a table, "
          f"{failed} failed; {len(details)} phones in the file.")
    return 0 if found or not phones else 1


if __name__ == "__main__":
    sys.exit(main())
