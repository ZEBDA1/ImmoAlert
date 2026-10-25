"""Quarantine historical inconsistencies and import captured live observations.

Default is a read-only summary. --apply changes listing metadata only: it never
creates an alert, changes an account/filter/favorite or calls Telegram.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import fields
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from database import repo, models as m
from database.session import init_db
from scraper.base import ScrapedListing
from scraper.quality import coherence_issues, normalize_listing
from utils.images import decode_images


def as_scraped(row):
    return ScrapedListing(source=row.source, external_id=row.external_id, url=row.url, title=row.title,
        price=row.price, surface_m2=row.surface_m2, price_per_m2=row.price_per_m2,
        location=row.location, rooms=row.rooms, description=row.description,
        images=decode_images(row.images), published_at=row.published_at)


def reconcile(paths: list[Path], *, apply: bool = False, record_health: bool = False):
    observed = []
    runs = defaultdict(list)
    allowed = {field.name for field in fields(ScrapedListing)}
    for directory in paths:
        summary_file = directory / "summary.json"
        if summary_file.exists():
            for run in json.loads(summary_file.read_text()):
                runs[run["source"]].append(run)
        for file in directory.glob("*/result.json"):
            if datetime.utcfromtimestamp(file.stat().st_mtime) < datetime.utcnow()-timedelta(hours=24):
                raise ValueError("Captured observations must be less than 24 hours old")
            result = json.loads(file.read_text())
            if result.get("error"):
                continue
            for data in result.get("normalized", []):
                item = ScrapedListing(**{key: value for key, value in data.items() if key in allowed})
                if isinstance(item.published_at, str):
                    item.published_at = datetime.fromisoformat(item.published_at.replace("Z", "+00:00")).replace(tzinfo=None)
                if item.source != result["source"]:
                    raise ValueError("Observation source mismatch")
                if normalized := normalize_listing(item):
                    observed.append(normalized)
    counts, reasons = Counter(), Counter()
    with repo.session_scope() as session:
        for row in session.scalars(select(m.Listing)):
            item = as_scraped(row)
            valid = normalize_listing(item) is not None
            status = "invalid" if not valid else row.inventory_status or "unverified"
            counts[status] += 1
            if not valid:
                reasons.update(coherence_issues(item) or ["insufficient_signal"])
            if apply:
                row.inventory_status = status
                if not valid:
                    row.quality_flags = ",".join(coherence_issues(item) or ["insufficient_signal"])
    imported, sources = Counter(), Counter()
    if apply:
        from services.scheduler import _persist_listing_candidate
        for item in observed:
            saved = _persist_listing_candidate(item)
            imported["new" if saved.is_new else "refreshed"] += 1
            sources[item.source] += 1
        if record_health:
            for source, items in runs.items():
                count = sources[source]
                failed = any(item.get("error") or not item.get("normalized_count") for item in items)
                errors = []
                if any(item.get("firecrawl_rate_limited") for item in items):
                    errors.append("firecrawl_quota_exhausted")
                if not count:
                    errors.append("blocked_or_unavailable: no rental extracted during live audit")
                if any(item.get("error") == "deadline" for item in items):
                    errors.append("timeout")
                finished = datetime.utcnow()
                elapsed = sum(item.get("elapsed", 0) for item in items)
                repo.save_source_run(cycle_id="live-audit-"+finished.strftime("%Y%m%dT%H%M%S"), source=source,
                    status=("partial" if failed else "success") if count else "failed",
                    started_at=finished-timedelta(seconds=elapsed), finished_at=finished,
                    duration_seconds=elapsed, locations_count=len(items), raw_count=sum(item.get("raw_count", 0) for item in items),
                    dropped_count=max(0, sum(item.get("raw_count", 0) for item in items)-count),
                    normalized_count=count, unique_count=count, error_count=1 if failed else 0,
                    timeout_count=sum(item.get("error")=="deadline" for item in items), last_error="; ".join(errors) or None)
    return {"applied": apply, "historical_classification": dict(counts), "rejection_reasons": dict(reasons),
            "captured_observations": len(observed), "imports": dict(imported), "sources": dict(sources)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="append", type=Path, default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--record-health", action="store_true", help="Store the captured live audit as source-health evidence")
    args = parser.parse_args()
    # Schema migration is opt-in, along with the data repair.
    if args.apply:
        init_db()
    print(json.dumps(reconcile(args.audit, apply=args.apply, record_health=args.record_health), ensure_ascii=False, indent=2))
