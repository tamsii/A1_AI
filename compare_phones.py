#!/usr/bin/env python3
"""Compare A1 and Telekom device prices and write one combined JSON file.

Standard library only. Run with --help for the options.
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile

# Telekom Description prefix -> brand. Brand words are stripped from the model
# so "SAMSUNG Galaxy S26" (Telekom) and "Galaxy S26" (A1, brand Samsung) agree.
TELEKOM_BRAND_PREFIXES = [
    ("t phone", "Telekom"),
    ("apple", "Apple"),
    ("samsung", "Samsung"),
    ("honor", "Honor"),
    ("xiaomi", "Xiaomi"),
    ("redmi", "Xiaomi"),
    ("poco", "Xiaomi"),
    ("motorola", "Motorola"),
    ("moto", "Motorola"),
    ("vivo", "Vivo"),
    ("tcl", "TCL"),
    ("alcatel", "Alcatel"),
    ("nokia", "Nokia"),
]

# Words dropped from the model per brand (lowercase). "redmi" is kept on
# purpose: it is part of the model name on both sites.
BRAND_NOISE_WORDS = {
    "apple": {"apple"},
    "samsung": {"samsung", "galaxy"},
    "xiaomi": {"xiaomi"},
    "motorola": {"motorola", "moto"},
    "honor": {"honor"},
    "vivo": {"vivo"},
}

# Network tokens differ between the shops ("Galaxy S26 5G" vs "Galaxy S26").
NETWORK_TOKENS = {"5g", "4g", "lte"}

# Fallback colour words, in addition to the colours listed in the data.
COMMON_COLORS = {
    "black", "white", "silver", "gray", "grey", "blue", "green", "red", "pink",
    "purple", "violet", "gold", "orange", "yellow", "brown", "cream", "lavender",
    "lavander", "graphite", "titanium", "navy", "teal", "mint", "olive", "lilac",
    "midnight", "dark", "light", "sky", "deep", "ice", "cloud", "urban",
}

STORAGE_UNIT_RE = re.compile(r"(\d+)\s*(GB|TB)\b", re.IGNORECASE)
STORAGE_RAM_RE = re.compile(r"\b\d{1,2}/(\d{2,4})\b")
STORAGE_BARE_RE = re.compile(r"(?<![\w/])(32|64|128|256|512|1024)(?=$|\s|\+)")
BUNDLE_SPLIT_RE = re.compile(r"\s\+")


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ParseError(f"File not found: {path}")
    except json.JSONDecodeError as e:
        raise ParseError(f"Invalid JSON in {path}: {e}")


def to_int(value):
    """'98999' / 98999.0 -> 98999; 'n/a' / '' / None -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    s = str(value).strip().replace(",", "")
    if not s or s.lower() in ("n/a", "na", "null", "-"):
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def none_if_na(value):
    if value is None:
        return None
    s = str(value).strip()
    return None if not s or s.lower() == "n/a" else s


def fmt_storage(amount, unit):
    amount = int(amount)
    unit = unit.upper()
    if unit == "GB" and amount >= 1024 and amount % 1024 == 0:
        return f"{amount // 1024}TB"
    return f"{amount}{unit}"


def split_storage(text, allow_bare):
    """Return (model_part, storage, tail) for a product name.

    The tail is whatever follows the storage (colour and/or bundle items).
    """
    m = STORAGE_UNIT_RE.search(text)
    if m:
        return text[:m.start()], fmt_storage(m.group(1), m.group(2)), text[m.end():]
    if allow_bare:
        m = STORAGE_RAM_RE.search(text) or STORAGE_BARE_RE.search(text)
        if m:
            return text[:m.start()], fmt_storage(m.group(1), "GB"), text[m.end():]
    # No storage: a bundle starts at the first " +" ("17 Ultra +Robot+...").
    # A '+' glued to a word is part of the model ("Galaxy S26+", "Pro+").
    m = BUNDLE_SPLIT_RE.search(text)
    if m:
        return text[:m.start()], None, text[m.start():]
    return text, None, ""


def remove_colors(text, color_words):
    s = " " + text.lower() + " "
    for c in sorted(color_words, key=len, reverse=True):
        s = re.sub(r"(?<![\w.])" + re.escape(c) + r"(?![\w.])", " ", s)
    return " ".join(s.split())


def normalize_part(text, brand_key, color_words):
    s = remove_colors(text, color_words)
    noise = BRAND_NOISE_WORDS.get(brand_key, set()) | {brand_key}
    words = [w for w in s.split() if w not in noise and w not in NETWORK_TOKENS]
    return "".join(words)  # spaces dropped: "Z Fold8" == "Z Fold 8", "ProMax" == "Pro Max"


def build_key(brand, model, storage, bundle_items, color_words):
    brand_key = brand.lower().strip()
    model_key = normalize_part(model, brand_key, color_words)
    for item in bundle_items:
        part = normalize_part(item, brand_key, color_words)
        if part:
            model_key += "+" + part
    return f"{brand_key}|{model_key}|{(storage or '').lower()}"


def network_tokens(text):
    return {t for t in re.split(r"[\s+]+", text.lower()) if t in NETWORK_TOKENS}


def network_conflict(a1_name, telekom_desc):
    """A1 writes '5G' for its 5G phones, Telekom often omits it. So only the
    reverse (Telekom says 5G, A1 does not, e.g. 'Redmi 15C' vs 'Redmi 15C 5G')
    or an explicit 4G-vs-5G clash hints at two different models."""
    a, t = network_tokens(a1_name), network_tokens(telekom_desc)
    return ("5g" in t and "5g" not in a) or ("4g" in a and "5g" in t) or ("5g" in a and "4g" in t)


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("+", " plus "))
    return s.strip("-")


def fmt_money(n):
    return f"{n:,}"


# ---------------------------------------------------------------- A1

def parse_a1(data, log):
    if isinstance(data, dict) and isinstance(data.get("devices"), list):
        items = data["devices"]
    elif isinstance(data, dict):
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        raise ParseError("A1 file: unexpected top-level structure")
    if not items:
        raise ParseError("A1 file contains no devices")

    phones = {}  # deviceName -> phone dict, insertion-ordered
    for i, v in enumerate(items):
        try:
            name = v["deviceName"].strip()
            brand = (v.get("manufacturer") or "").strip()
        except (KeyError, TypeError, AttributeError):
            raise ParseError(f"A1 entry #{i} has no deviceName")
        if not brand:
            brand = name.split()[0]
        promo = v.get("promoPrice") or {}
        entry = {
            "name": name,
            "brand": brand,
            "available": bool(v.get("isActive", True)),
            "url": v.get("deviceUrl"),
            "price_outright": to_int(v.get("basePrice_10")),
            "price_installment_total": to_int(v.get("basePriceInstallment_K5")),
            "promo": {
                "tariff": none_if_na(promo.get("promotedTariffName")),
                "payment": none_if_na(promo.get("promotedPaymentType")),
                "monthly_price": to_int(promo.get("promotedPrice")),
            },
            "promo_business": {
                "tariff": none_if_na(promo.get("promotedTariffNameBusiness")),
                "payment": none_if_na(promo.get("promotedPaymentTypeBusiness")),
                "price": to_int(promo.get("promotedPriceBusiness")),
            },
            "colors": [c.get("colorName") for c in v.get("colors") or [] if c.get("colorName")],
            "variants": 1,
        }
        cur = phones.get(name)
        if cur is None:
            phones[name] = entry
            continue
        cur["variants"] += 1
        for c in entry["colors"]:
            if c not in cur["colors"]:
                cur["colors"].append(c)
        cur["available"] = cur["available"] or entry["available"]
        new_prices = (entry["price_outright"], entry["price_installment_total"])
        cur_prices = (cur["price_outright"], cur["price_installment_total"])
        if new_prices != cur_prices:
            note = (f"A1 '{name}': variants have different prices (outright/installment total "
                    f"{fmt_pair(cur_prices)} vs {fmt_pair(new_prices)}); keeping the lowest")
            if note not in log and \
                    f"A1 '{name}': variants have different prices (outright/installment total " \
                    f"{fmt_pair(new_prices)} vs {fmt_pair(cur_prices)}); keeping the lowest" not in log:
                log.append(note)
            if price_sort_key(new_prices) < price_sort_key(cur_prices):
                for k in ("url", "price_outright", "price_installment_total", "promo", "promo_business"):
                    cur[k] = entry[k]
        elif entry["promo"] != cur["promo"]:
            log.append(f"A1 '{name}': variants have different promos "
                       f"({fmt_promo(cur['promo'])} vs {fmt_promo(entry['promo'])}); "
                       f"keeping the lowest monthly price")
            if lower(entry["promo"]["monthly_price"], cur["promo"]["monthly_price"]):
                cur["promo"] = entry["promo"]
                cur["url"] = entry["url"]

    for p in phones.values():
        model, storage, tail = split_storage(p["name"], allow_bare=True)
        p["model_text"] = model.strip()
        p["storage"] = storage
        p["bundle"] = [b.strip() for b in tail.split("+") if b.strip()]
    return list(phones.values())


def fmt_pair(prices):
    return "/".join(fmt_n(x) for x in prices)


def price_sort_key(prices):
    return tuple(float("inf") if x is None else x for x in prices)


def lower(a, b):
    return a is not None and (b is None or a < b)


def fmt_promo(p):
    return f"{p['tariff']}, {p['payment']}, {p['monthly_price']}"


# ---------------------------------------------------------------- Telekom

def parse_telekom(datasets, log):
    devices, totals = [], []
    for path, data in datasets:
        if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
            raise ParseError(f"Telekom file {path}: expected an object with a 'devices' list")
        devices.extend(data["devices"])
        if data.get("total") is not None:
            totals.append(int(data["total"]))

    seen, unique = set(), []
    for d in devices:  # the same device can appear on two pages
        did = d.get("Id") or d.get("OfferId") or d.get("Description")
        if did in seen:
            continue
        seen.add(did)
        unique.append(d)

    total = max(totals) if totals else None
    if total is not None and len(unique) != total:
        raise ParseError(f"Telekom data incomplete: API says total={total} but the file(s) "
                         f"contain {len(unique)} unique devices. Download all pages and pass "
                         f"them all with --telekom.")
    if not unique:
        raise ParseError("Telekom file contains no devices")

    entries, negative_prices = [], []
    for i, d in enumerate(unique):
        desc = (d.get("Description") or d.get("Name") or "").strip()
        if not desc:
            raise ParseError(f"Telekom device #{i} has no Description/Name")
        if "DevicePrice" not in d:
            raise ParseError(f"Telekom device '{desc}' has no DevicePrice")
        brand, rest = telekom_brand(desc)
        model, storage, tail = split_storage(rest, allow_bare=False)
        parts = tail.split("+")
        color_text = parts[0].strip()
        bundle = [b.strip() for b in parts[1:] if b.strip()]
        colors = []
        for c in d.get("Colors") or []:
            for k in ("PublicName", "Name"):
                if c.get(k) and c[k] not in colors:
                    colors.append(c[k])

        plan = d.get("PreferredTariffPlan") or {}
        promos, promo_ids = [], set()
        for p in d.get("Promotions") or []:
            if p.get("IsActive") is False:
                continue
            pid = p.get("Id") or p.get("Name")
            if pid in promo_ids:
                continue
            promo_ids.add(pid)
            promos.append({"name": (p.get("Name") or p.get("Title") or "").strip() or None,
                           "ends": clean_date(p.get("EndDate"))})

        tariff_price = to_int(d.get("DevicePreferredTariffPrice"))
        business_price = to_int(d.get("DevicePreferredTariffPriceBusiness"))
        if any(v is not None and v < 0 for v in (tariff_price, business_price)):
            negative_prices.append(desc)

        entries.append({
            "description": desc,
            "name": (d.get("Name") or desc).strip(),
            "brand": brand,
            "model_text": model.strip(),
            "storage": storage,
            "color_text": color_text,
            "bundle": bundle,
            "colors": colors,
            "available": bool(d.get("IsVisible", True)),
            "price_outright": to_int(d.get("DevicePrice")),
            "discount": to_int(d.get("DeviceDiscountPrice")),
            "price_with_tariff": clamp0(tariff_price),
            "price_with_tariff_business": clamp0(business_price),
            "tariff": plan.get("TariffName"),
            "installment": {
                "months": to_int(plan.get("NumberOfInstallments")),
                "monthly_price": round(float(plan["InstallmentAmount"]), 2)
                if plan.get("InstallmentAmount") is not None else None,
            },
            "promos": promos,
        })
    if negative_prices:
        log.append(f"Telekom: {len(negative_prices)} devices have a negative price with tariff "
                   f"(tariff budget exceeds the device price); stored as 0")
    return entries, total


def telekom_brand(desc):
    low = desc.lower()
    for prefix, brand in TELEKOM_BRAND_PREFIXES:
        if low == prefix or low.startswith(prefix + " "):
            # keep "Redmi"/"Moto" in the text; noise words are removed later
            return brand, desc if prefix in ("redmi", "poco", "moto") else desc[len(prefix):]
    first = desc.split()[0]
    return first, desc[len(first):]


def clean_date(value):
    """'2026-10-20T21:45:00.0000000Z' -> '2026-10-20T21:45:00Z' (UTC, as in the API)."""
    if not value:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", str(value))
    return f"{m.group(1)}T{m.group(2)}Z" if m else str(value)


def clamp0(n):
    return None if n is None else max(n, 0)


# ---------------------------------------------------------------- overrides

def load_overrides(path, log):
    if not path or not os.path.exists(path):
        return {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise ParseError(f"{path} must be a JSON object: {{\"telekom name\": \"match key\"}}")
    out = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue  # comments
        if v is not None and (not isinstance(v, str) or v.count("|") != 2):
            raise ParseError(f"{path}: override for '{k}' must be 'brand|model|storage' or null")
        out[k.strip().lower()] = v.lower() if isinstance(v, str) else None
    return out


# ---------------------------------------------------------------- main logic

def build(a1_phones, tk_entries, overrides, currency, log, warnings):
    a1_colors = {c.lower() for p in a1_phones for c in p["colors"]}

    for p in a1_phones:
        p["key"] = build_key(p["brand"], p["model_text"], p["storage"], p["bundle"],
                             a1_colors | COMMON_COLORS)

    used_overrides = set()
    tk_by_key, excluded = {}, []
    for t in tk_entries:
        colors = {c.lower() for c in t["colors"]} | COMMON_COLORS
        if t["color_text"]:
            colors.add(t["color_text"].lower())
        t["key"] = build_key(t["brand"], t["model_text"], t["storage"], t["bundle"], colors)
        for name in (t["description"].lower(), t["name"].lower()):
            if name in overrides:
                used_overrides.add(name)
                t["auto_key"], t["key"] = t["key"], overrides[name]
                break
        if t["key"] is None:
            excluded.append(t)
            continue
        cur = tk_by_key.get(t["key"])
        if cur is None:
            tk_by_key[t["key"]] = t
            continue
        if cur["price_outright"] != t["price_outright"]:
            log.append(f"Telekom key '{t['key']}': '{cur['description']}' = {cur['price_outright']} "
                       f"vs '{t['description']}' = {t['price_outright']}; keeping the lowest")
        keep, other = (t, cur) if lower(t["price_outright"], cur["price_outright"]) else (cur, t)
        for c in other["colors"]:
            if c not in keep["colors"]:
                keep["colors"].append(c)
        tk_by_key[t["key"]] = keep

    for name in set(overrides) - used_overrides:
        warnings.append(f"overrides.json: '{name}' does not match any Telekom device")

    phones, matched_keys, used_ids = [], set(), set()
    for p in a1_phones:
        display = p["name"] if p["name"].lower().startswith(p["brand"].lower()) \
            else f"{p['brand']} {p['name']}"
        pid = slugify(display)
        n = 2
        while pid in used_ids:
            pid = f"{slugify(display)}-{n}"
            n += 1
        used_ids.add(pid)

        t = tk_by_key.get(p["key"])
        phone = {
            "id": pid,
            "name": display,
            "brand": p["brand"],
            "storage": p["storage"],
            "colors": p["colors"],
            "match_key": p["key"],
            "a1": {
                "available": p["available"],
                "url": p["url"],
                "price_outright": p["price_outright"],
                "price_installment_total": p["price_installment_total"],
                "promo": p["promo"],
                "promo_business": p["promo_business"],
            },
        }
        if t is None:
            phone["telekom"] = {"available": False,
                                "message": "This phone is not available at Telekom."}
            phone["comparison"] = None
        else:
            matched_keys.add(p["key"])
            promo = t["promos"][0] if t["promos"] else None
            phone["telekom"] = {
                "available": True,
                "name": t["name"],
                "description": t["description"],
                "price_outright": t["price_outright"],
                "price_with_tariff": t["price_with_tariff"],
                "price_with_tariff_business": t["price_with_tariff_business"],
                "tariff": t["tariff"],
                "installment": t["installment"],
                "promo": promo,
            }
            if len(t["promos"]) > 1:
                phone["telekom"]["other_promos"] = t["promos"][1:]
            phone["comparison"] = compare(p["price_outright"], t, currency)
            if network_conflict(p["name"], t["description"]):
                warnings.append(f"Check match: A1 '{p['name']}' <-> Telekom '{t['description']}' "
                                f"(Telekom says 5G, A1 does not; may be a different model)")
            if t["discount"]:
                log.append(f"Telekom '{t['description']}': DeviceDiscountPrice {t['discount']} "
                           f"not applied to price_outright")
        phones.append(phone)

    tk_unmatched = [t for k, t in tk_by_key.items() if k not in matched_keys]
    return phones, tk_unmatched, excluded


def compare(a1_price, t, currency):
    tk_price = t["price_outright"]
    if a1_price is None or tk_price is None:
        return {"outright_gap": None, "cheaper_at": None,
                "note": "Outright price missing on one side; cannot compare."}
    gap = tk_price - a1_price
    if gap > 0:
        cheaper, note = "A1", f"A1 is {fmt_money(gap)} {currency} cheaper (outright price)."
    elif gap < 0:
        cheaper, note = "Telekom", f"Telekom is {fmt_money(-gap)} {currency} cheaper (outright price)."
    else:
        cheaper, note = "Equal", "Same outright price at A1 and Telekom."
    if t["discount"]:
        note += (f" Telekom also lists an extra device discount of "
                 f"{fmt_money(abs(t['discount']))} {currency}, not included here.")
    return {"outright_gap": gap, "cheaper_at": cheaper, "note": note}


def write_atomic(path, obj):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def report(phones, a1_phones, tk_entries, tk_unmatched, excluded, total, log, warnings, args):
    matched = [p for p in phones if p["comparison"] is not None]
    unmatched = [p for p in phones if p["comparison"] is None]
    tk_by_model = {}
    for t in tk_entries:
        if t.get("key"):
            tk_by_model.setdefault(t["key"].rsplit("|", 1)[0], []).append(t["storage"] or "?")

    out = []
    w = out.append
    w("=" * 72)
    w(f"A1 vs Telekom  |  currency {args.currency}  |  last_checked {args.date}")
    w("=" * 72)
    w(f"Telekom devices read      : {len(tk_entries)}" + (f" (API total {total})" if total else ""))
    w(f"A1 phones (deduplicated)  : {len(phones)}")
    w(f"  matched at Telekom      : {len(matched)}")
    w(f"  not at Telekom          : {len(unmatched)}")
    w(f"Telekom devices matching no A1 phone: {len(tk_unmatched)}")
    if excluded:
        w(f"Telekom devices excluded by overrides: {len(excluded)}")
    w("")
    w("MATCHED (outright prices)")
    w(f"  {'Phone':<40} {'A1':>9} {'Telekom':>9} {'Gap':>8}  Cheaper")
    for p in matched:
        c = p["comparison"]
        gap = "" if c["outright_gap"] is None else f"{c['outright_gap']:+,}"
        w(f"  {p['name'][:40]:<40} {fmt_n(p['a1']['price_outright']):>9} "
          f"{fmt_n(p['telekom']['price_outright']):>9} {gap:>8}  {c['cheaper_at']}")
    w("")
    w("A1 PHONES NOT AT TELEKOM")
    for p in unmatched:
        other = tk_by_model.get(p["match_key"].rsplit("|", 1)[0])
        hint = f"  [Telekom has this model only in: {', '.join(other)}]" if other else ""
        w(f"  {p['name']}{hint}")
    w("")
    w("TELEKOM DEVICES WITH NO A1 MATCH")
    for t in tk_unmatched:
        w(f"  {t['description']}  ->  key {t['key']}")
    if warnings:
        w("")
        w("WARNINGS")
        for x in warnings:
            w(f"  ! {x}")
    if log:
        w("")
        w("DATA NOTES")
        for x in log:
            w(f"  - {x}")
    print("\n".join(out))


def fmt_n(n):
    return "-" if n is None else fmt_money(n)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compare A1 and Telekom phone prices.")
    ap.add_argument("--a1", default="a1_devices.json", help="A1 API response (default: %(default)s)")
    ap.add_argument("--telekom", nargs="+", default=["telekom_devices.json"],
                    help="Telekom API response file(s); pass every page (default: %(default)s)")
    ap.add_argument("--overrides", default="overrides.json",
                    help="manual matches, telekom name -> match key (default: %(default)s)")
    ap.add_argument("--output", "-o", default="phones_a1_vs_telekom.json",
                    help="output file (default: %(default)s)")
    ap.add_argument("--html", default=None,
                    help="web page to write (default: output name with .html; 'none' to skip)")
    ap.add_argument("--currency", default="MKD", help="currency label (default: %(default)s)")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="value for last_checked (default: today)")
    args = ap.parse_args(argv)

    log, warnings = [], []
    try:
        a1_phones = parse_a1(load_json(args.a1), log)
        tk_entries, total = parse_telekom([(p, load_json(p)) for p in args.telekom], log)
        overrides = load_overrides(args.overrides, log)
        phones, tk_unmatched, excluded = build(a1_phones, tk_entries, overrides,
                                               args.currency, log, warnings)
    except ParseError as e:
        print(f"ERROR: {e}\nNothing written; {args.output} was left unchanged.", file=sys.stderr)
        return 1

    result = {"currency": args.currency, "last_checked": args.date, "phones": phones}
    write_atomic(args.output, result)
    report(phones, a1_phones, tk_entries, tk_unmatched, excluded, total, log, warnings, args)
    print(f"\nWrote {len(phones)} phones to {args.output}")
    html = args.html or os.path.splitext(args.output)[0] + ".html"
    if html.lower() != "none":
        from build_page import build_page
        build_page(result, html)
        print(f"Wrote web page {html} (open it in your browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
