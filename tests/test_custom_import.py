"""Custom catalog import: parsing, validation, and idempotent re-upload.

Rows run through the real ingest pipeline (pipeline.ingest.ingest_offer), same as
crawled listings -- so this mostly exercises the CSV/JSON -> RawOffer translation
and the re-upload-updates-in-place guarantee, not the pipeline itself (already
covered by test_pipeline.py and test_export_bom.py).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="buildhub_import_"))
os.environ["BUILDHUB_DB_URL"] = f"sqlite:///{(_TMP / 't.db').as_posix()}"
os.environ["BUILDHUB_MEDIA_DIR"] = str(_TMP / "media")
os.environ["TRANSLATE_PROVIDER"] = "none"

from sqlalchemy import select  # noqa: E402

from buildhub.db.models import CanonicalProduct, Offer, Site  # noqa: E402
from buildhub.db.session import init_db, session_scope  # noqa: E402
from buildhub.pipeline.custom_import import (  # noqa: E402
    import_catalog,
    parse_catalog_file,
)

FAILS: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        FAILS.append(label)


def check_true(label, got):
    check(label, bool(got), True)


CSV_TEXT = """Name,Brand,MPN,URL,Price,Currency,MOQ,Category
NEMA17 Stepper Motor,Generic,17HS4401,https://example.com/nema17,8.50,USD,1,printer_motion_electronics/stepper-motors-nema
PLA Filament 1kg Black,GenericFil,PLA-BLK-1KG,https://example.com/pla,18.99,USD,1,filament/pla
,NoName,MISSING-NAME,https://example.com/bad,1.00,USD,1,
"""

JSON_TEXT = """
[
  {"name": "BLTouch Sensor", "brand": "Antclabs", "mpn": "BLT-V3.1",
   "url": "https://example.com/bltouch", "price": 14.99, "moq": 1},
  {"name": "GT2 Belt 2m", "url": "https://example.com/gt2belt", "price": 3.99}
]
"""


def test_parse_csv():
    print("\nparsing CSV")
    rows = parse_catalog_file(CSV_TEXT.encode("utf-8"), "csv")
    check("three rows read (including the bad one)", len(rows), 3)
    check("headers lowercased", rows[0]["name"], "NEMA17 Stepper Motor")
    check("mpn column present", rows[0]["mpn"], "17HS4401")


def test_parse_json():
    print("\nparsing JSON")
    rows = parse_catalog_file(JSON_TEXT.encode("utf-8"), "json")
    check("two rows read", len(rows), 2)
    check("missing optional fields default to absent, not crash", rows[1].get("brand"), None)


def test_parse_bad_json_raises():
    print("\nbad JSON is reported, not silently swallowed")
    try:
        parse_catalog_file(b"not valid json {{{", "json")
        check("raises on malformed JSON", False, True)
    except Exception:
        check("raises on malformed JSON", True, True)

    try:
        parse_catalog_file(b'{"just": "an object, not a list"}', "json")
        check("raises when JSON isn't a list of rows", False, True)
    except ValueError:
        check("raises when JSON isn't a list of rows", True, True)


def test_import_and_reimport():
    print("\nimport ingests rows through the real pipeline")
    init_db()
    with session_scope() as session:
        rows = parse_catalog_file(CSV_TEXT.encode("utf-8"), "csv")
        result = import_catalog(session, rows)
        check("2 of 3 rows imported", result.imported, 2)
        check("1 error for the missing name", len(result.errors), 1)

        custom_site = session.scalar(select(Site).where(Site.key == "custom"))
        check_true("custom site exists (seeded)", custom_site)

        offers = session.scalars(
            select(Offer).where(Offer.site_id == custom_site.id)
        ).all()
        check("2 offers attached to the custom site", len(offers), 2)

        stepper = session.scalar(
            select(CanonicalProduct).where(CanonicalProduct.mpn == "17HS4401")
        )
        check_true("stepper motor product created", stepper)
        if stepper:
            check("price carried through", stepper.best_price_usd, 8.50)

    print("\nre-importing the same file updates in place")
    with session_scope() as session:
        rows = parse_catalog_file(CSV_TEXT.encode("utf-8"), "csv")
        import_catalog(session, rows)

        custom_site = session.scalar(select(Site).where(Site.key == "custom"))
        offers = session.scalars(
            select(Offer).where(Offer.site_id == custom_site.id)
        ).all()
        check("still 2 offers, not 4 (re-upload updates in place)", len(offers), 2)


def test_row_without_mpn_keys_on_name_and_url():
    print("\nrows without an MPN still dedupe on (name, url)")
    with session_scope() as session:
        rows = parse_catalog_file(JSON_TEXT.encode("utf-8"), "json")
        r1 = import_catalog(session, rows)
        check("2 rows imported first time", r1.imported, 2)
        r2 = import_catalog(session, rows)
        check("2 rows imported again (idempotent, not duplicated)", r2.imported, 2)

        custom_site = session.scalar(select(Site).where(Site.key == "custom"))
        offers = session.scalars(
            select(Offer).where(Offer.site_id == custom_site.id)
        ).all()
        # 2 from the CSV test above + 2 from this JSON import, still no duplicates.
        check("4 total offers on the custom site", len(offers), 4)


def run() -> int:
    test_parse_csv()
    test_parse_json()
    test_parse_bad_json_raises()
    test_import_and_reimport()
    test_row_without_mpn_keys_on_name_and_url()

    print()
    print("=" * 62)
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("custom catalog import OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
