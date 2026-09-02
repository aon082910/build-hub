"""Import a user-supplied CSV/JSON parts catalog.

Runs uploaded rows through the same normalize -> translate(no-op, already English)
-> match -> publish pipeline as a crawled listing (``pipeline.ingest.ingest_offer``),
so custom parts show up in search and BOM costing right alongside scraped offers,
merge into canonical products via the same matching engine, and get CSV export for
free. All rows attach to the single reserved ``custom`` Site (seeded in db/seed.py).

Expected columns/fields (case-insensitive, extra columns ignored):
    name (required), brand, mpn, url, price, currency, moq, category

Re-uploading the same file updates rows in place rather than duplicating: each row's
identity is its MPN if present, else a hash of (name, url) -- the same idempotency
rule crawling relies on.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..scrapers.base import RawOffer
from .ingest import IngestContext, ingest_offer

log = logging.getLogger(__name__)

REQUIRED_FIELDS = ("name",)
KNOWN_FIELDS = ("name", "brand", "mpn", "url", "price", "currency", "moq", "category")


@dataclass
class ImportResult:
    imported: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.imported > 0 and not self.errors


def parse_catalog_file(raw_bytes: bytes, fmt: str) -> list[dict]:
    """Parse uploaded bytes into a list of row dicts with lowercased keys."""
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    fmt = (fmt or "").lower()

    if fmt == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("items", "parts", "rows"):
                if key in data:
                    data = data[key]
                    break
            else:
                raise ValueError(
                    "JSON object has none of the expected 'items'/'parts'/'rows' keys -- "
                    "expected a list of objects, or {\"items\": [...]}"
                )
        if not isinstance(data, list):
            raise ValueError("JSON catalog must be a list of objects (or {\"items\": [...]})")
        rows = [{str(k).strip().lower(): v for k, v in (row or {}).items()} for row in data]
    elif fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        rows = [
            {(k or "").strip().lower(): v for k, v in row.items() if k}
            for row in reader
        ]
    else:
        raise ValueError(f"Unsupported catalog format: {fmt!r} (expected csv or json)")

    return rows


def import_catalog(session: Session, rows: list[dict]) -> ImportResult:
    """Ingest parsed rows into the ``custom`` site's offers."""
    result = ImportResult()
    ctx = IngestContext(session)
    try:
        for i, row in enumerate(rows, start=1):
            try:
                raw = _row_to_offer(row)
            except ValueError as e:
                result.errors.append(f"row {i}: {e}")
                continue
            try:
                ingest_offer(ctx, raw, fetch_images=False)
                result.imported += 1
            except Exception as e:
                log.warning("custom import: row %s failed to ingest: %s", i, e)
                result.errors.append(f"row {i} ({raw.title[:60]!r}): {e}")
        session.commit()
    finally:
        ctx.close()
    return result


def _row_to_offer(row: dict) -> RawOffer:
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError("missing required 'name' column")

    mpn = str(row.get("mpn") or "").strip() or None
    url = str(row.get("url") or "").strip()
    site_product_id = mpn or hashlib.sha256(f"{name}|{url}".encode()).hexdigest()[:24]

    price = _as_float(row.get("price"))
    currency = (str(row.get("currency") or "USD").strip() or "USD").upper()
    moq = _as_int(row.get("moq")) or 1

    return RawOffer(
        site_key="custom",
        site_product_id=site_product_id,
        url=url or "#",
        title=name[:512],
        currency=currency,
        price_min=price,
        mpn=mpn,
        model=mpn,
        brand=(str(row.get("brand") or "").strip() or None),
        moq=max(1, moq),
        category_path=(str(row.get("category") or "").strip() or None),
        seller_name="Custom Catalog",
        detail_fetched=True,
        raw={"source": "custom_import"},
    )


def _as_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
