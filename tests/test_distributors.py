"""Mouser and Digi-Key adapters: field mapping and clean-skip-without-a-key, offline.

Same spirit as test_provider.py -- these prove the adapters parse the payload shapes
declared in Mouser's and Digi-Key's published API docs, and that both skip cleanly
(no crash, no adapter registered as "working" when it isn't) when unconfigured. They
do NOT prove either vendor's live API still matches those docs; that needs
`selftest --site mouser` / `selftest --site digikey` against real credentials.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="buildhub_dist_"))
os.environ["BUILDHUB_DB_URL"] = f"sqlite:///{(_TMP / 't.db').as_posix()}"
os.environ["BUILDHUB_MEDIA_DIR"] = str(_TMP / "media")

from buildhub.config import get_settings  # noqa: E402
from buildhub.scrapers.components import DigiKeyAdapter, MouserAdapter  # noqa: E402

FAILS: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        FAILS.append(label)


def check_true(label, got):
    check(label, bool(got), True)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.last = {}

    def get(self, url, params=None, headers=None, referer=None, expect_json=False):
        self.last = {"url": url, "params": params or {}, "headers": headers or {}}
        return _Resp(self.payload)

    def post(self, url, data=None, json_body=None, headers=None, referer=None):
        self.last = {"url": url, "data": data, "body": json_body, "headers": headers or {}}
        return _Resp(self.payload)

    def close(self):
        pass


# ------------------------------------------------------------------------- mouser

MOUSER_PAYLOAD = {
    "Errors": [],
    "SearchResults": {
        "NumberOfResult": 1,
        "Parts": [
            {
                "MouserPartNumber": "356-ESP32WROOM32E",
                "ManufacturerPartNumber": "ESP32-WROOM-32E",
                "Manufacturer": "Espressif",
                "Description": "RF TXRX MODULE WIFI/BT ESP32",
                "ImagePath": "https://www.mouser.com/images/espressif/esp32.jpg",
                "ProductDetailUrl": "https://www.mouser.com/ProductDetail/espressif/esp32-wroom-32e",
                "Availability": "In Stock",
                "Min": "1",
                "PriceBreaks": [
                    {"Quantity": 1, "Price": "$2.9500", "Currency": "USD"},
                    {"Quantity": 10, "Price": "$2.5000", "Currency": "USD"},
                ],
            }
        ],
    },
}


def test_mouser_skips_without_key():
    print("\nmouser: skips cleanly without a key")
    a = MouserAdapter()
    results = list(a.search("esp32", max_pages=1))
    check("no results without MOUSER_API_KEY", results, [])


def test_mouser_parses_payload():
    print("\nmouser: field mapping")
    os.environ["MOUSER_API_KEY"] = "test-key"
    get_settings.cache_clear()
    a = MouserAdapter()
    a._fetcher = FakeFetcher(MOUSER_PAYLOAD)
    results = list(a.search("esp32", max_pages=1))
    check("one offer parsed", len(results), 1)
    if results:
        o = results[0]
        check("mpn extracted", o.mpn, "ESP32-WROOM-32E")
        check("brand extracted", o.brand, "Espressif")
        check("site product id is the Mouser part number", o.site_product_id, "356-ESP32WROOM32E")
        check("in stock parsed", o.in_stock, True)
        check("tiers parsed", len(o.tiers), 2)
        check("cheapest tier wins price_min", o.price_min, 2.50)
        check_true("image url captured", o.image_urls)
        check_true("api key sent as query param", "apiKey=test-key" in a._fetcher.last["url"])
    del os.environ["MOUSER_API_KEY"]
    get_settings.cache_clear()


def test_mouser_reports_api_errors():
    print("\nmouser: API-level errors don't crash the crawl")
    os.environ["MOUSER_API_KEY"] = "test-key"
    get_settings.cache_clear()
    a = MouserAdapter()
    a._fetcher = FakeFetcher({"Errors": [{"Message": "Invalid API Key"}], "SearchResults": {}})
    results = list(a.search("esp32", max_pages=1))
    check("no results when the API rejects the key", results, [])
    del os.environ["MOUSER_API_KEY"]
    get_settings.cache_clear()


# ------------------------------------------------------------------------ digikey

DIGIKEY_TOKEN_PAYLOAD = {"access_token": "fake-token", "expires_in": 600}

DIGIKEY_SEARCH_PAYLOAD = {
    "Products": [
        {
            "ManufacturerProductNumber": "ESP32-WROOM-32E",
            "Manufacturer": {"Name": "Espressif Systems"},
            "ProductDescription": {"ProductDescription": "RF TXRX MODULE WIFI ESP32"},
            "ProductUrl": "https://www.digikey.com/en/products/detail/espressif/esp32-wroom-32e",
            "PhotoUrl": "https://media.digikey.com/esp32.jpg",
            "ProductVariations": [
                {
                    "MinimumOrderQuantity": 1,
                    "StandardPricing": [
                        {"BreakQuantity": 1, "UnitPrice": 3.10},
                        {"BreakQuantity": 10, "UnitPrice": 2.70},
                    ],
                }
            ],
        }
    ]
}


def test_digikey_skips_without_credentials():
    print("\ndigikey: skips cleanly without credentials")
    a = DigiKeyAdapter()
    results = list(a.search("esp32", max_pages=1))
    check("no results without DIGIKEY_CLIENT_ID/SECRET", results, [])


class _TwoStageFetcher:
    """First call (token) returns one payload, every call after returns the other."""

    def __init__(self, token_payload, search_payload):
        self.token_payload = token_payload
        self.search_payload = search_payload
        self.calls: list[str] = []

    def post(self, url, data=None, json_body=None, headers=None, referer=None):
        self.calls.append(url)
        if "oauth2/token" in url:
            return _Resp(self.token_payload)
        return _Resp(self.search_payload)

    def get(self, url, params=None, headers=None, referer=None, expect_json=False):
        return _Resp({})

    def close(self):
        pass


def test_digikey_parses_payload():
    print("\ndigikey: token exchange + field mapping")
    os.environ["DIGIKEY_CLIENT_ID"] = "test-id"
    os.environ["DIGIKEY_CLIENT_SECRET"] = "test-secret"
    get_settings.cache_clear()
    a = DigiKeyAdapter()
    a._fetcher = _TwoStageFetcher(DIGIKEY_TOKEN_PAYLOAD, DIGIKEY_SEARCH_PAYLOAD)
    results = list(a.search("esp32", max_pages=1))
    check("token endpoint called once", a._fetcher.calls.count(
        "https://api.digikey.com/v1/oauth2/token"), 1)
    check("one offer parsed", len(results), 1)
    if results:
        o = results[0]
        check("mpn extracted", o.mpn, "ESP32-WROOM-32E")
        check("brand extracted", o.brand, "Espressif Systems")
        check("tiers parsed", len(o.tiers), 2)
        check("cheapest tier wins price_min", o.price_min, 2.70)
        check_true("image url captured", o.image_urls)
    del os.environ["DIGIKEY_CLIENT_ID"]
    del os.environ["DIGIKEY_CLIENT_SECRET"]
    get_settings.cache_clear()


def run() -> int:
    test_mouser_skips_without_key()
    test_mouser_parses_payload()
    test_mouser_reports_api_errors()
    test_digikey_skips_without_credentials()
    test_digikey_parses_payload()

    print()
    print("=" * 62)
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("distributor adapters OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
