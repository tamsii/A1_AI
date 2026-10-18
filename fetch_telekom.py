#!/usr/bin/env python3
"""Download every Telekom device in a category, following all pages.

Standard library only. The request (URL, method, headers, body, paging) is
described in a JSON config file, so it can be copied from the browser's
DevTools without changing code. See README.md, "Fetching Telekom from the API".

The result has the same shape as the API response ({"devices", "category",
"total"}) and can be passed straight to compare_phones.py --telekom.
"""

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; phone-price-compare/1.0)",
}


class FetchError(Exception):
    pass


def fill(value, variables):
    """Replace {page}, {offset}, {size}, {category} inside strings, lists and dicts.

    A string that is exactly one placeholder (e.g. "{page}") becomes the raw
    value, so numbers stay numbers in a JSON body.
    """
    if isinstance(value, str):
        for name, v in variables.items():
            token = "{" + name + "}"
            if value == token:
                return v
            value = value.replace(token, str(v))
        return value
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, variables) for k, v in value.items()}
    return value


def get_path(obj, path):
    """get_path(data, "result.items") -> data["result"]["items"]; "" -> obj."""
    for part in [p for p in path.split(".") if p]:
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def request_page(cfg, variables, timeout, retries):
    url = fill(cfg["url"], variables)
    params = fill(cfg.get("params") or {}, variables)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    method = cfg.get("method", "GET").upper()
    headers = dict(DEFAULT_HEADERS)
    headers.update(cfg.get("headers") or {})
    data = None
    if cfg.get("body") is not None:
        data = json.dumps(fill(cfg["body"], variables)).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8-sig")
            break
        except urllib.error.HTTPError as e:
            # 4xx (except 429) will not fix itself; retry 429 and 5xx
            if attempt == retries or (400 <= e.code < 500 and e.code != 429):
                raise FetchError(f"{method} {url} -> HTTP {e.code} {e.reason}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise FetchError(f"{method} {url} -> {getattr(e, 'reason', e)}")
        time.sleep(2 ** attempt)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise FetchError(f"{url} did not return JSON (starts with: {raw[:120]!r})")


def fetch_all(cfg, category, timeout, retries, delay, max_pages, log=print):
    paging = cfg.get("paging") or {}
    mode = paging.get("mode", "page")          # "page", "offset" or "none"
    size = int(paging.get("size", 20))
    page = int(paging.get("start", 1))
    devices_path = cfg.get("devices_path", "devices")
    total_path = cfg.get("total_path", "total")
    id_field = cfg.get("id_field", "Id")

    devices, seen, total, category_info, received = [], set(), None, None, 0
    for n in range(max_pages):
        # {page} counts pages, {offset} counts devices received so far
        variables = {"page": page, "offset": received, "size": size, "category": category}
        data = request_page(cfg, variables, timeout, retries)
        items = get_path(data, devices_path)
        if not isinstance(items, list):
            raise FetchError(f"No list at '{devices_path}' in the response. Top-level keys: "
                             f"{list(data)[:10] if isinstance(data, dict) else type(data).__name__}. "
                             f"Set devices_path in the config.")
        if total is None:
            t = get_path(data, total_path)
            total = int(t) if isinstance(t, (int, float, str)) and str(t).isdigit() else None
            category_info = data.get("category") if isinstance(data, dict) else None

        received += len(items)
        new = 0
        for d in items:
            key = d.get(id_field) if isinstance(d, dict) else None
            key = key or json.dumps(d, sort_keys=True)
            if key not in seen:
                seen.add(key)
                devices.append(d)
                new += 1
        log(f"  page {n + 1}: {len(items)} devices ({new} new), {len(devices)}"
            + (f"/{total}" if total else "") + " so far")

        if mode == "none":
            break
        if total is not None and len(devices) >= total:
            break
        if not items or new == 0:  # empty page or the API keeps returning the same page
            break
        page += 1
        time.sleep(delay)
    else:
        raise FetchError(f"Stopped after --max-pages={max_pages}; raise it if this is expected")

    if total is not None and len(devices) != total:
        raise FetchError(f"API says total={total} but {len(devices)} unique devices were "
                         f"downloaded. Check the paging settings in the config.")
    return {"devices": devices, "category": category_info,
            "total": total if total is not None else len(devices)}


def write_atomic(path, obj):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download all Telekom devices of a category.")
    ap.add_argument("--config", default="telekom_api.json",
                    help="request description (default: %(default)s)")
    ap.add_argument("--category", help="value for {category}; overrides the config's 'category'")
    ap.add_argument("--output", "-o", default="telekom_devices.json",
                    help="output file (default: %(default)s)")
    ap.add_argument("--timeout", type=float, default=30, help="seconds per request")
    ap.add_argument("--retries", type=int, default=3, help="retries per page on network/5xx errors")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between pages")
    ap.add_argument("--max-pages", type=int, default=100, help="safety limit")
    args = ap.parse_args(argv)

    try:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        if not cfg.get("url"):
            raise FetchError(f"{args.config} has no 'url'")
        category = args.category or cfg.get("category", "")
        print(f"Fetching Telekom category '{category}' ...")
        result = fetch_all(cfg, category, args.timeout, args.retries, args.delay, args.max_pages)
    except (OSError, json.JSONDecodeError, FetchError) as e:
        print(f"ERROR: {e}\nNothing written; {args.output} was left unchanged.", file=sys.stderr)
        return 1

    write_atomic(args.output, result)
    print(f"Wrote {len(result['devices'])} devices (total {result['total']}) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
