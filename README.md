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
