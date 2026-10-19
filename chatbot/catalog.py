"""A1 phone catalog for the chatbot: load a1_devices.json, find the phones a
question is about, and format them as text for the model's prompt.

Standard library only. Phones are grouped by name with compare_phones.parse_a1,
so colours/SKUs of one model are one entry, as on the comparison page.
"""

import csv
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import compare_phones  # noqa: E402

EUR_TO_MKD = 61.5
PHONES_PAGE = "https://www.a1.mk/webshop/mk/phones"

# Columns of phone_specs.csv (see specs.py), with the label the model sees.
SPEC_COLUMNS = ["screen", "camera", "battery", "chip", "ram", "other"]
SPEC_LABELS = {"screen": "Screen", "camera": "Camera", "battery": "Battery",
               "chip": "Processor", "ram": "RAM", "other": "Other"}

# Cyrillic or colloquial spellings -> the words used in A1 names. Checked
# before the letter-by-letter transliteration below ("ајфон" is not "ajfon").
ALIASES = {
    "ајфон": "iphone", "айфон": "iphone", "ифон": "iphone", "епл": "apple", "ејпл": "apple",
    "самсунг": "samsung", "галакси": "galaxy", "ксиаоми": "xiaomi", "шаоми": "xiaomi",
    "сјаоми": "xiaomi", "редми": "redmi", "моторола": "motorola", "мото": "moto",
    "хонор": "honor", "виво": "vivo", "мобивајр": "mobiwire", "про": "pro", "макс": "max",
    "ултра": "ultra", "плус": "plus", "фолд": "fold", "флип": "flip", "еџ": "edge",
    "едж": "edge", "лајт": "lite", "ер": "air", "нот": "note", "ноте": "note",
}

TRANSLIT = dict(zip(
    "абвгдѓежзѕијклљмнњопрстќуфхцчџш",
    ["a", "b", "v", "g", "d", "gj", "e", "zh", "z", "dz", "i", "j", "k", "l", "lj", "m",
     "n", "nj", "o", "p", "r", "s", "t", "kj", "u", "f", "h", "c", "ch", "dz", "sh"]))

# Words that say nothing about which phone is meant.
STOP_WORDS = {
    "the", "a", "an", "is", "are", "do", "does", "you", "have", "has", "how", "much", "what",
    "which", "price", "cost", "costs", "phone", "phones", "mobile", "for", "and", "or", "in",
    "of", "with", "me", "i", "can", "it", "this", "that", "there", "any", "your", "a1",
    "5g", "4g", "gb", "mkd", "den", "denars", "eur", "euro", "euros",
    "kolku", "chini", "cena", "cenata", "telefon", "telefoni", "dali", "imate", "ima",
    "koj", "koja", "koe", "koi", "na", "za", "so", "od", "do", "e", "se", "li", "i", "mi",
    "sakam", "ke", "ve", "molam", "denari", "evra", "ден", "den.",
}

CHEAP_WORDS = {"cheapest", "cheap", "cheaper", "budget", "affordable",
               "najevtin", "najevtiniot", "najevtina", "evtin", "evtini", "evtina", "povoljen"}
MAX_WORDS = r"\b(?:to|under|below|less than|up to|max(?:imum)?|budget|до|под|помалку од|најмногу|макс)"
MIN_WORDS = r"\b(?:over|above|more than|at least|from|над|повеќе од|најмалку|(?<!помалку )од)"
MONEY_RE = r"(\d+(?:[.,\s]\d{3})*)\s*(k|к|илјади|thousand)?"
EURO_RE = r"€|\beur|\beuro|евра|евро"


def translit(text):
    """Lowercase, with Macedonian Cyrillic written in Latin letters."""
    out = []
    for word in re.findall(r"\w+|\W+", text.lower()):
        if word in ALIASES:
            out.append(ALIASES[word])
        else:
            out.append("".join(TRANSLIT.get(ch, ch) for ch in word))
    return "".join(out)


def tokens(text):
    """Search tokens: words, words split at letter/digit borders, and each
    pair of neighbouring words joined ("s25 edge" and "S25Edge" both give s25edge)."""
    text = re.sub(r"(\d+)\s*(gb|гб)\b", r"\1", text.lower())
    text = re.sub(r"(?<=\w)\+(?=\s|$)", "plus ", text)  # "S26+" -> "s26plus", "512+Buds" stays
    words = [w for w in re.split(r"[^a-z0-9]+", translit(text)) if w]
    out = set(words)
    for w in words:
        out.update(p for p in re.findall(r"[a-z]+|\d+", w))
        if w.endswith("plus") and len(w) > 4:
            out.add(w[:-4])  # "s26plus" is also an "s26"
    for a, b in zip(words, words[1:]):
        out.add(a + b)
    return out


def to_mkd(number_text, thousands, euro):
    n = int(re.sub(r"[\s.,]", "", number_text))
    if thousands:
        n *= 1000
    return int(n * EUR_TO_MKD) if euro else n


def price_limits(question):
    """(min_price, max_price) in MKD from phrases like 'under 20000' or 'до 300 евра'.
    A number under 500 is a storage or model number ("до 128GB"), not a price."""
    q = question.lower()
    euro = bool(re.search(EURO_RE, q))
    limits = []
    for words in (MIN_WORDS, MAX_WORDS):
        m = re.search(words + r"\s*" + MONEY_RE, q)
        n = to_mkd(m.group(1), m.group(2), euro) if m else None
        limits.append(n if n is not None and n >= 500 else None)
    return tuple(limits)


def model_key(phone):
    """One key per model, without storage, colour or bundle:
    "Galaxy S26 5G 512" -> "samsung|s26"."""
    key = compare_phones.build_key(phone["brand"], phone["model_text"], None, [],
                                   compare_phones.COMMON_COLORS)
    return key.rstrip("|")


def read_specs(path):
    """phone_specs.csv -> {model key: {column: text}}. Accepts what Excel
    saves: ; or , between columns, UTF-8 or Windows Cyrillic text."""
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    lines = text.splitlines()
    if lines and lines[0].lower().startswith("sep="):
        lines = lines[1:]
    delimiter = ";" if lines and lines[0].count(";") >= lines[0].count(",") else ","
    specs = {}
    for row in csv.DictReader(lines, delimiter=delimiter):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if row.get("model"):
            specs[row["model"]] = {c: row.get(c, "") for c in SPEC_COLUMNS}
    return specs


def clean_url(url):
    """Percent-encode characters a browser would encode itself. A1's data has
    a non-breaking space in some links ("xiaomi-redmi\\xa0note-14-pro+..."),
    which breaks both fetching the page and turning the link into a link."""
    return urllib.parse.quote(url.strip(), safe=":/?#[]@!$&'()*+,;=%-._~")


# Text from the "Гаранција и сервис" box that older fetches read as table rows.
NOT_SPECS = {"гаранција и сервис", "провери статус на сервис", "гаранција од производител"}


def clean_fields(fields):
    """Specs from the a1.mk page, without page text that is not a spec, and
    with the warranty in months ("12" -> "12 месеци")."""
    out = {}
    for k, v in fields.items():
        if k.lower() in NOT_SPECS or k.isdigit():
            continue
        if k.lower() == "гаранција" and v.isdigit():
            v += " месеци"
        out[k] = v
    return out


def slug_key(url):
    """The product name part of an A1 link, letters and digits only:
    .../samsung-galaxy-s26+-5g-256/1000064140 -> samsunggalaxys265g256"""
    parts = [p for p in url.rstrip("/").split("/") if p and not p.isdigit()]
    return re.sub(r"[^a-z0-9]", "", parts[-1].lower()) if parts else ""


def fmt_mkd(n):
    return f"{n:,} MKD" if n is not None else "n/a"


class Catalog:
    def __init__(self, path):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        self.specs_path = os.path.join(folder, "phone_specs.csv")
        self.details_path = os.path.join(folder, "phone_details.json")  # from fetch_details.py
        self.mtime = None
        self.phones = []
        self.reload_if_changed()

    def reload_if_changed(self):
        """Re-read the files when they change, so a new fetch_a1.py run or an
        edited phone_specs.csv is picked up without a restart."""
        def mtime_of(p):
            return os.path.getmtime(p) if os.path.exists(p) else None
        mtime = (os.path.getmtime(self.path), mtime_of(self.specs_path), mtime_of(self.details_path))
        if mtime == self.mtime:
            return
        try:
            specs = read_specs(self.specs_path) if mtime[1] else {}
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            print(f"WARNING: could not read {self.specs_path}: {e}", file=sys.stderr)
            specs = {}
        try:
            with open(self.details_path, encoding="utf-8") as f:
                page_details = json.load(f) if mtime[2] else {}
        except (OSError, ValueError) as e:
            if mtime[2]:
                print(f"WARNING: could not read {self.details_path}: {e}", file=sys.stderr)
            page_details = {}
        data = compare_phones.load_json(self.path)
        phones = compare_phones.parse_a1(data, [])
        # Every real product link, by its SKU number and by its name part, to
        # correct links the model rewrites or makes up (see fix_link).
        items = data.values() if isinstance(data, dict) else data
        urls = [clean_url(v["deviceUrl"]) for v in items if isinstance(v, dict) and v.get("deviceUrl")]
        self.links = set(urls) | {PHONES_PAGE, "https://www.a1.mk", "https://www.a1.mk/"}
        self.by_sku = {u.rstrip("/").rsplit("/", 1)[-1]: u for u in urls}
        self.by_slug = {slug_key(u): u for u in urls}
        for p in phones:
            p["title"] = p["name"] if p["name"].lower().startswith(p["brand"].lower()) \
                else f"{p['brand']} {p['name']}"
            p["tokens"] = tokens(p["title"]) | {p["brand"].lower()}
            p["model_key"] = model_key(p)
            p["url"] = clean_url(p["url"]) if p["url"] else p["url"]
            p["specs"] = {k: v for k, v in specs.get(p["model_key"], {}).items() if v}
            page = page_details.get(p["name"]) or {}
            p["page_fields"] = clean_fields(page.get("fields") or {})
            p["in_box"] = page.get("in_box") or []
        self.phones = [p for p in phones if p["available"]]
        self.mtime = mtime

    @property
    def updated(self):
        import datetime
        return datetime.date.fromtimestamp(self.mtime[0]).isoformat()

    def fix_link(self, url):
        """A link from the model's answer, made safe: a real A1 product link
        stays as it is, a changed one is matched back to the real link by its
        SKU number or its name, and anything else on a1.mk becomes the phones page."""
        core = url.replace("\\", "")  # Markdown escapes such as "s26\\+"
        tail = ""
        while core and core[-1] in ".,;:!?":
            core, tail = core[:-1], core[-1] + tail
        if not re.match(r"https?://(www\.)?a1\.mk(/|$)", core):
            return url
        if core in self.links:
            return core + tail
        sku = re.search(r"/(\d{6,})/?$", core)
        real = (self.by_sku.get(sku.group(1)) if sku else None) or self.by_slug.get(slug_key(core))
        return (real or PHONES_PAGE) + tail

    def overview(self):
        """One line per phone, sorted by brand then price, for the system prompt."""
        rows = sorted(self.phones, key=lambda p: (p["brand"], p["price_outright"] or 0))
        def line(p):
            short = "; ".join(p["specs"][c] for c in ("camera", "battery", "screen") if c in p["specs"]) \
                or "; ".join(f"{k} {p['page_fields'][k]}" for k in ("Камера", "Големина на екранот")
                             if k in p["page_fields"])
            return f"- {p['title']}: {fmt_mkd(p['price_outright'])}" + (f" ({short})" if short else "")
        return "\n".join(map(line, rows))

    def search(self, question, limit=5):
        """Phones the question is about, best match first."""
        # Prices are not model numbers: "до 15.000" must not match "Redmi 15".
        no_prices = re.sub(r"\d+(?:[.,\s]\d{3})+|\d{4,}|\d+\s*(?:k|к)\b", " ", question.lower())
        q_tokens = tokens(no_prices) - STOP_WORDS
        lo, hi = price_limits(question)
        cheap = bool(q_tokens & CHEAP_WORDS)

        def in_range(p):
            price = p["price_outright"]
            return price is not None and (lo is None or price >= lo) and (hi is None or price <= hi)

        scored = []
        for p in self.phones:
            hit = q_tokens & p["tokens"]
            # Model numbers ("s26", "17", "a57") say more than words like "pro".
            score = sum(3 if any(c.isdigit() for c in t) else 1 for t in hit)
            if score:
                scored.append((score, p))
        if scored:
            best = max(s for s, _ in scored)
            scored = [(s, p) for s, p in scored if s >= best * 0.6 and in_range(p)]
            scored.sort(key=lambda sp: (0 if cheap else -sp[0], sp[1]["price_outright"] or 0))
            found = [p for _, p in scored]
        elif lo is not None or hi is not None or cheap:
            found = sorted((p for p in self.phones if in_range(p)),
                           key=lambda p: p["price_outright"] or 0)
            if hi is not None and not cheap:
                found.reverse()  # "under 20000": the best phones near the budget first
        else:
            found = []
        return found[:limit]

    @staticmethod
    def details(phone):
        """Everything the data says about one phone, as prompt text."""
        p = phone
        lines = [f"### {p['title']}",
                 f"- Price (pay in full): {fmt_mkd(p['price_outright'])}"]
        if p["price_installment_total"]:
            lines.append(f"- Total price when paid in installments: "
                         f"{fmt_mkd(p['price_installment_total'])}")
        promo = p["promo"]
        if promo["tariff"] and promo["monthly_price"] is not None:
            if promo["payment"] and promo["payment"].startswith("0"):
                lines.append(f"- With the {promo['tariff']} tariff: {fmt_mkd(promo['monthly_price'])} "
                             f"one-time payment for the phone")
            else:
                lines.append(f"- With the {promo['tariff']} tariff: {fmt_mkd(promo['monthly_price'])} "
                             f"per month for the phone ({promo['payment']})")
        biz = p["promo_business"]
        if biz["tariff"] and biz["price"] is not None:
            how = "one-time payment" if (biz["payment"] or "").startswith("0") else biz["payment"]
            lines.append(f"- Business customers, with the {biz['tariff']} tariff: "
                         f"{fmt_mkd(biz['price'])} for the phone ({how})")
        if p["storage"]:
            lines.append(f"- Storage: {p['storage']}")
        if p["bundle"]:
            lines.append(f"- Comes bundled with: {', '.join(p['bundle'])}")
        for c in SPEC_COLUMNS:
            if c in p["specs"]:
                lines.append(f"- {SPEC_LABELS[c]}: {p['specs'][c]}")
        if p["page_fields"]:
            lines.append("- Specifications from the a1.mk page:")
            lines += [f"  - {k}: {v}" for k, v in p["page_fields"].items()]
        if p["in_box"]:
            lines.append(f"- In the box: {', '.join(p['in_box'])}")
        if p["colors"]:
            lines.append(f"- Colours: {', '.join(dict.fromkeys(p['colors']))}")
        if p["url"]:
            lines.append(f"- Link: {p['url']}")
        return "\n".join(lines)
