# A1 vs Telekom phone price comparison

`compare_phones.py` reads the A1 and Telekom device API responses and writes
one combined file, `phones_a1_vs_telekom.json`, that compares the **outright**
price of every A1 phone with the same phone (brand + model + storage) at
Telekom. It needs Python 3.8+ and nothing outside the standard library.

## Run

```bash
python3 compare_phones.py
```

With the defaults it reads `a1_devices.json`, `telekom_devices.json` and
`overrides.json` from the current folder and writes `phones_a1_vs_telekom.json`.
It then prints a report of the counts, the matched prices, the A1 phones that
are not at Telekom, the Telekom devices that match no A1 phone, warnings and
data notes.

| Option | Default | Meaning |
|---|---|---|
| `--a1 PATH` | `a1_devices.json` | A1 API response |
| `--telekom PATH [PATH ...]` | `telekom_devices.json` | Telekom API response. If the API is paged, pass every page; they are merged. |
| `--overrides PATH` | `overrides.json` | Manual matches (optional file) |
| `-o, --output PATH` | `phones_a1_vs_telekom.json` | Output file |
| `--currency CODE` | `MKD` | Currency label used in the output and notes |
| `--date YYYY-MM-DD` | today | Value for `last_checked` |

Example:

```bash
python3 compare_phones.py --a1 data/a1.json \
    --telekom data/telekom_p1.json data/telekom_p2.json \
    --output out/phones.json --currency MKD
```

The output is written to a temporary file and then moved into place, and only
after both inputs have parsed. If anything fails (a missing file, invalid JSON,
a missing required field, or fewer Telekom devices than the API's `total`),
the script prints `ERROR: ...`, exits with code 1, and leaves the previous
output file untouched.

## Fields used

| Output | A1 field | Telekom field |
|---|---|---|
| name / storage | `deviceName` | `Description` (e.g. `Apple iPhone 18 Pro 256GB Silver`) |
| brand | `manufacturer` | first word of `Description` (`Redmi` → Xiaomi, `Moto` → Motorola) |
| colors | `colors[].colorName` (all variants) | — |
| url | `deviceUrl` | — |
| price_outright | `basePrice_10` | `DevicePrice` |
| price_installment_total | `basePriceInstallment_K5` | — |
| promo | `promoPrice.promotedTariffName / promotedPaymentType / promotedPrice` | `Promotions[].Name / EndDate` |
| promo_business | `promoPrice.*Business` | — |
| price_with_tariff | — | `DevicePreferredTariffPrice` |
| price_with_tariff_business | — | `DevicePreferredTariffPriceBusiness` |
| tariff, installment | — | `PreferredTariffPlan.TariffName / NumberOfInstallments / InstallmentAmount` |

## How phones are matched

1. **A1**: variants (colours/SKUs) are grouped by `deviceName`. If variants
   have different prices, the cheapest (outright, then installment total)
   is kept. If only their promos differ, the lowest promo monthly price is kept.
   Both cases are listed under DATA NOTES.
2. **Telekom**: brand, model and storage are parsed from `Description`.
   Whatever follows the storage is a colour plus optional bundle items
   (`... 512GB Wht+Sound+Charger`). There is one entry per
   brand+model+storage, and the lowest price is kept if they differ (this is logged).
3. **Match key**: `brand|model|storage`, lowercase, with colour words, brand
   words (`Samsung`, `Galaxy`, `Apple`, `Moto`, ...) and `5G`/`4G`
   removed, and all spaces dropped. So `SAMSUNG Z Fold8 256GB` and
   `Galaxy Z Fold 8 5G 256` both become `samsung|zfold8|256gb`, and
   `iPhone 17 ProMax256GB` and `iPhone 17 Pro Max 256GB` both become
   `apple|iphone17promax|256gb`. Bundles stay in the key (`+sound+charger`),
   so a bundle only matches the same bundle.
4. `comparison` is based on the **outright price only**:
   `outright_gap = telekom - a1`, `cheaper_at` is `A1`, `Telekom` or `Equal`.
   Tariff and installment prices are included for reference only.
5. Every A1 phone is in the output. When there is no Telekom match:
   `"telekom": {"available": false, "message": "This phone is not available at Telekom."}`
   and `"comparison": null`.

Extra fields beyond the requested schema: `match_key` (to help write
overrides), `telekom.description` (the raw Telekom name with colour), and
`telekom.other_promos` (when Telekom lists more than one promotion; the first
one is in `telekom.promo`).

Other data handling:
- Telekom returns negative "price with tariff" values when the tariff budget is
  larger than the device price. These are stored as `0`.
- `DeviceDiscountPrice` is not subtracted from `price_outright`. When it is
  set, the comparison note mentions it.
- `promo.ends` is the API's `EndDate` in UTC (`2026-10-20T21:45:00Z`).

## overrides.json

Use this file to fix matches by hand. Each key is a Telekom `Description` (or
`Name`, not case-sensitive) and each value is the A1 match key to use. Use
`null` to stop a Telekom device from matching anything. Keys starting with `_`
are ignored, so they can hold comments. The A1 key of each phone is in the
output as `match_key`.

```json
{
  "Moto Edge70 Fusion 256GB Black+MotoWatch": "motorola|edge70fusion|256gb",
  "Redmi 15C 5G 128GB Midnight Black": null
}
```

The report warns about overrides that match no Telekom device, and about
automatic matches that look doubtful. For example, Telekom says `5G` but A1
does not, as in `Redmi 15C` vs `Redmi 15C 5G`.

## Viewing the results

Every run of `compare_phones.py` (and so `run_all.py`) also writes
**`phones_a1_vs_telekom.html`**. Double-click it to open it in your browser.
The data is embedded in the page, so it works offline with no server. On the
page you can:

- see a summary of how many phones are cheaper at A1, cheaper at Telekom, or only at A1;
- search by name, filter by brand, and switch between All, A1 cheaper,
  Telekom cheaper, At both and Only at A1;
- sort by name, biggest difference or A1 price;
- click a phone to see its tariff, installment and promo details, with a link to a1.mk.

The page layout lives in `page_template.html`. Use `--html other.html` to
choose a different file name, or `--html none` to skip the page. To rebuild the
page from an existing JSON file: `python3 build_page.py phones_a1_vs_telekom.json`.

## Updating both shops in one go

```bash
python3 run_all.py            # fetch_a1.py + fetch_telekom.py + compare_phones.py
```

`run_all.py` runs three steps in order:
1. `fetch_a1.py` downloads A1's phone list (`GetCategoryProducts-Fetch?CatalogID=Phones`)
   to `a1_devices.json`.
2. `fetch_telekom.py` downloads Telekom's phones to `telekom_devices.json`.
3. `compare_phones.py` builds the comparison.

It stops at the first step that fails, and the files from the last good run
stay as they were. Any extra arguments go to `compare_phones.py`, e.g.
`python3 run_all.py --currency MKD`. Each script can also be run on its own.

## Fetching Telekom from the API

`fetch_telekom.py` downloads every device in a Telekom category, following all
pages, and writes `telekom_devices.json` in the same shape as the API response.
Then run the comparison as usual:

```bash
python3 fetch_telekom.py --list-categories   # show category IDs (GetAllCategories)
python3 fetch_telekom.py                     # phones (MKT00006) -> telekom_devices.json
python3 compare_phones.py
```

`telekom_api.json` is already set up for the Telekom webshop API: it POSTs to
`https://www.telekom.mk/api/webshop/getDevices` with the same body as the
website, asking for 500 devices per page. Category IDs come from
`https://www.telekom.mk/api/webshop/GetAllCategories`. Use `--category ID`
to download a different category. The browser sends `MinPrice` 990 and
`MaxPrice` 137990 (the current price range). The config widens these to 0 and
9999999 so that new devices outside that range are not dropped. If the API
returns 0 devices with the wide range, put the browser's values back.

The sections below explain how to point the fetcher at a different API.

### 1. Find the request

1. Open the Telekom webshop phones page in Chrome/Firefox and open DevTools (F12) → **Network** → filter **Fetch/XHR**.
2. Reload the page (and click "next page" / "load more" if there is one).
3. Find the request whose **Response** has `"devices": [...]` and `"total": 65`.
4. Note the URL, the method (GET/POST), the query string or request body, and
   which parameter changes between pages. Right-click → **Copy as cURL** shows
   it all.

### 2. Fill in `telekom_api.json`

```json
{
  "url": "https://<telekom host>/api/<endpoint>",
  "method": "GET",
  "category": "MKT00006",
  "params": {"categoryId": "{category}", "page": "{page}", "pageSize": "{size}"},
  "headers": {},
  "body": null,
  "paging": {"mode": "page", "start": 1, "size": 20},
  "devices_path": "devices",
  "total_path": "total",
  "id_field": "Id"
}
```

Placeholders are filled in for every page:

| Placeholder | Value |
|---|---|
| `{category}` | `category` from the config, or `--category` |
| `{page}` | page number, starting at `paging.start` and going up by 1 |
| `{offset}` | number of devices received so far (0, 20, 40, ...), for skip/offset APIs |
| `{size}` | `paging.size` |

- **Page-number API** (`?page=2&pageSize=20`): `"mode": "page"` and use `{page}`.
- **Offset API** (`skip=40&take=20`): `"mode": "offset"` and use `{offset}`.
- **POST with a JSON body**: put the body under `"body"`, e.g.
  `{"filter": {"categoryId": "{category}"}, "skip": "{offset}", "take": "{size}"}`.
  A value that is just a placeholder is sent as a number, not a string.
- **Everything in one response** (no paging): `"mode": "none"`.
- If the device list or total sit deeper in the response, set
  `devices_path` / `total_path` with dots, e.g. `"result.items"`.
- Copy any header the request needs (e.g. `Authorization`, `X-Requested-With`) into `"headers"`.

Paging stops when `total` devices have been collected, or a page comes back
empty or with nothing new. If the number of unique devices does not equal the
API's `total`, or a request fails after retries (`--retries`, default 3, for
network errors, 429 and 5xx), the script exits with an error and leaves the
existing `telekom_devices.json` untouched. Other options: `--category`,
`--output`, `--delay` (seconds between pages), `--timeout`, `--max-pages`.
