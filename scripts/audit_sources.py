"""Read-only live source audit, with captured evidence and a hard process budget.

Run from the application directory. No listing/alert is persisted or delivered.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

sys.path.insert(0, os.getcwd())


def worker(args):
    from bs4 import BeautifulSoup
    from utils import http
    from config import settings

    directory = Path(args.output) / args.worker
    directory.mkdir(parents=True, exist_ok=True)
    evidence = []
    sequence = 0

    def record(kind, url, html=None, **extra):
        nonlocal sequence
        sequence += 1
        item = {"kind": kind, "url": url, **extra}
        if html:
            soup = BeautifulSoup(html, "html.parser")
            filename = f"page-{sequence:03d}.html.gz"
            with gzip.open(directory / filename, "wt", encoding="utf-8") as handle:
                handle.write(html)
            item.update({"file": filename, "bytes": len(html), "sha256": hashlib.sha256(html.encode()).hexdigest(),
                         "title": soup.title.get_text(" ", strip=True) if soup.title else None,
                         "h1": [node.get_text(" ", strip=True)[:180] for node in soup.select("h1")[:2]],
                         "blocked_hint": http.is_probably_blocked(html)})
        evidence.append(item)
        (directory / "trace.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))

    original_get = http.HttpClient.get
    def traced_get(self, url, **kwargs):
        started = time.monotonic()
        try:
            response = original_get(self, url, **kwargs)
            record("http", response.url or url, response.text, status=response.status_code,
                   elapsed=round(time.monotonic()-started, 2))
            return response
        except Exception as exc:
            record("http_error", url, error=type(exc).__name__, elapsed=round(time.monotonic()-started, 2))
            raise
    http.HttpClient.get = traced_get

    original_html = http.fetch_html
    def traced_html(url, **kwargs):
        started = time.monotonic()
        html = original_html(url, **kwargs)
        record("html", url, html, elapsed=round(time.monotonic()-started, 2))
        return html
    http.fetch_html = traced_html

    from services.scheduler import get_scraper
    from scraper.quality import normalize_listing, listing_quality_flags
    from utils.firecrawl import firecrawl_rate_limited

    scraper = get_scraper(args.worker)
    started = time.monotonic()
    raw = []
    error = None
    try:
        for listing in scraper.fetch_city(args.city, args.postal):
            raw.append(listing)
    except Exception as exc:
        error = type(exc).__name__
    normalized = [item for listing in raw if (item := normalize_listing(listing)) is not None]
    result = {"source": args.worker, "city": args.city, "postal": args.postal,
              "elapsed": round(time.monotonic()-started, 2), "error": error,
              "raw_count": len(raw), "normalized_count": len(normalized),
              "firecrawl_rate_limited": firecrawl_rate_limited(),
              "firecrawl_key_configured": bool(settings.firecrawl_api_key),
              "proxy_configured": bool(settings.proxy_url),
              "raw": [asdict(listing) for listing in raw],
              "normalized": [{**asdict(listing), "quality_flags": listing_quality_flags(listing)} for listing in normalized],
              "evidence": evidence}
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def run_one(source, args):
    directory = Path(args.output)/source
    directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", source,
               "--city", args.city, "--postal", args.postal, "--output", args.output]
    started = time.monotonic()
    timed_out = False
    with (directory/"worker.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
        try:
            process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    path = directory/"result.json"
    if path.exists():
        result = json.loads(path.read_text())
        return {key: result[key] for key in ["source", "elapsed", "raw_count", "normalized_count", "error", "firecrawl_rate_limited"]}
    return {"source": source, "elapsed": round(time.monotonic()-started,2), "error": "deadline" if timed_out else f"exit_{process.returncode}", "trace": (directory/"trace.json").exists()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="Rouen")
    parser.add_argument("--postal", default="76000")
    parser.add_argument("--output", required=True)
    parser.add_argument("--sources", default="paruvendu,entreparticuliers,leboncoin,seloger,pap,logicimmo,rentola,ouestfranceimmo")
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--worker")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    Path(args.output).mkdir(parents=True, exist_ok=True)
    results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_one, source, args) for source in args.sources.split(",")]
        for future in as_completed(futures):
            result=future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False),flush=True)
    (Path(args.output)/"summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__=="__main__":
    main()
