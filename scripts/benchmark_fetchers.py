"""Read-only, reproducible Scrapling/Firecrawl benchmark on identical portal pages.

No listing, filter, source setting or alert is written. Run in a diagnostic
container with the production storage mounted read-only and a separate output.
Firecrawl quota failures are NOT treated as measurements of scraping ability.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, os.getcwd())


def corpus():
    pages = []
    for city, postal, slug, insee, pap, region, aviv in [
        ("Rouen", "76000", "rouen", "76540", 43640, "normandie", "ad08fr31632"),
        ("Mantes-la-Jolie", "78200", "mantes-la-jolie", "78361", 39120, "ile-de-france", "ad08fr32499"),
    ]:
        urls = {
            "entreparticuliers":f"https://www.entreparticuliers.com/annonces-immobilieres/location/{slug}-{postal}",
            "paruvendu":f"https://www.paruvendu.fr/immobilier/annonceimmofo/liste/listeAnnonces?tt=5&pa=FR&codeINSEE={insee}&lo={postal}&ray=15",
            "leboncoin":f"https://www.leboncoin.fr/recherche?category=10&sort=time&locations={postal}&page=1",
            "seloger":f"https://www.seloger.com/recherche/location/immobilier/{region}/{slug}-{postal}/{aviv}",
            "pap":f"https://www.pap.fr/annonce/locations-appartement-g{pap}",
            "logicimmo":f"https://www.logic-immo.com/recherche-immo/location/immobilier/{region}/{slug}-{postal}/{aviv}",
            "rentola":f"https://rentola.fr/location/{slug}",
            "ouestfranceimmo":f"https://www.ouestfrance-immo.com/immobilier/location/appartement/{slug}-{postal[:2]}-{insee}/",
        }
        for source, url in urls.items():
            pages.append({"id":source+"-"+postal,"source":source,"city":city,"postal":postal,"url":url})
    return pages


def clean_error(value):
    from config import settings
    text = str(value or "")
    if settings.firecrawl_api_key:
        text = text.replace(settings.firecrawl_api_key, "[REDACTED]")
    return text[:500]


def account_usage():
    from config import settings
    import requests
    if not settings.firecrawl_api_key:
        return {"status":"key_missing"}
    try:
        r = requests.get("https://api.firecrawl.dev/v2/team/credit-usage",headers={"Authorization":"Bearer "+settings.firecrawl_api_key},timeout=15)
        data = r.json()
        return {"http_status":r.status_code,"success":data.get("success"),
                "usage":{k:v for k,v in (data.get("data") or {}).items() if k in {"remainingCredits","planCredits"}},
                "error":clean_error(data.get("error"))}
    except Exception as exc:
        return {"error_type":type(exc).__name__}


def firecrawl_api_probe(url):
    from config import settings
    import requests
    started = time.monotonic()
    try:
        r = requests.post("https://api.firecrawl.dev/v2/scrape",headers={"Authorization":"Bearer "+settings.firecrawl_api_key},
            json={"url":url,"formats":["rawHtml"],"onlyMainContent":False,"maxAge":0,"timeout":30000},timeout=40)
        data = r.json()
        return {"url":url,"provider_http_status":r.status_code,"elapsed_seconds":round(time.monotonic()-started,3),
                "success":data.get("success"),"error":clean_error(data.get("error"))}
    except Exception as exc:
        return {"url":url,"elapsed_seconds":round(time.monotonic()-started,3),"error_type":type(exc).__name__}


def fetch_firecrawl(url, directory, *, extraction=False):
    from config import settings
    from utils.firecrawl import firecrawl_cli_path
    binary = firecrawl_cli_path()
    if not binary:
        return None, {"status":"not_installed"}
    output = directory/"firecrawl-response.json"
    command = [binary,"scrape",url,"--format","json" if extraction else "rawHtml","--json","--max-age","0","--wait-for","1000","--country","FR","--proxy","auto","-o",str(output)]
    if extraction:
        from scraper.firecrawl_helpers import LISTING_SCHEMA
        schema = directory/"extraction-schema.json"
        schema.write_text(json.dumps(LISTING_SCHEMA))
        command.extend(["--schema-file",str(schema)])
    env = dict(os.environ)
    if settings.firecrawl_api_key:
        env["FIRECRAWL_API_KEY"] = settings.firecrawl_api_key
    env["NO_COLOR"] = "1"
    started = time.monotonic()
    try:
        process = subprocess.run(command,capture_output=True,text=True,timeout=55,env=env)
    except subprocess.TimeoutExpired:
        return None, {"status":"timeout","elapsed_seconds":round(time.monotonic()-started,3)}
    elapsed = round(time.monotonic()-started,3)
    text_output = output.read_text() if output.exists() else process.stdout
    try:
        data = json.loads(text_output)
    except ValueError:
        data = {}
    error = clean_error(data.get("error") or process.stderr)
    if process.returncode or data.get("success") is False:
        quota = bool(re.search(r"insufficient credits|not enough credits|payment required|free tier|rate.?limit",error,re.I))
        return None, {"status":"quota_exhausted" if quota else "provider_error","exit_code":process.returncode,"elapsed_seconds":elapsed,"error":error}
    inner = data.get("data",data)
    html = inner.get("rawHtml") or inner.get("html")
    meta = inner.get("metadata") or {}
    return html, {"status":"returned","elapsed_seconds":elapsed,"http_status":meta.get("statusCode"),
                  "credits_used":meta.get("creditsUsed"),"phase":"firecrawl_cloud",
                  "final_url":meta.get("sourceURL") or url}


def fetch_scrapling(url):
    from config import settings
    from utils.http import is_probably_blocked
    from utils.scrapling import _browser_path
    from scrapling.fetchers import Fetcher, StealthyFetcher
    phases = []
    started = time.monotonic()
    for name in ("http","browser"):
        phase_started = time.monotonic()
        try:
            if name == "http":
                result = Fetcher.get(url,impersonate="chrome",timeout=25,retries=1,proxy=settings.proxy_url)
            else:
                result = StealthyFetcher.fetch(url,headless=True,executable_path=_browser_path(),extra_flags=["--no-sandbox","--disable-dev-shm-usage"],
                    timeout=25000,retries=1,wait=1000,locale="fr-FR",timezone_id="Europe/Paris",google_search=False,proxy=settings.proxy_url)
            html = result.html_content or ""
            blocked = is_probably_blocked(html) if html else False
            phases.append({"phase":name,"http_status":result.status,"blocked":blocked,"elapsed_seconds":round(time.monotonic()-phase_started,3)})
            if result.status == 200 and html and not blocked:
                return html, {"status":"returned","phase":name,"http_status":200,"final_url":str(result.url),"elapsed_seconds":round(time.monotonic()-started,3),"phases":phases}
            if result.status == 404:
                return html, {"status":"not_found","http_status":404,"elapsed_seconds":round(time.monotonic()-started,3),"phases":phases}
        except Exception as exc:
            phases.append({"phase":name,"error_type":type(exc).__name__,"elapsed_seconds":round(time.monotonic()-phase_started,3)})
    return None, {"status":"blocked_or_unavailable","elapsed_seconds":round(time.monotonic()-started,3),"phases":phases}


def parse_page(source, html, url, city, postal):
    """Use the current bot parsers unchanged, with no further network calls."""
    from bs4 import BeautifulSoup
    from unittest.mock import patch
    from scraper.quality import normalize_listing, listing_quality_flags
    from scraper.base import ScrapedListing
    from scraper.html_helpers import jsonld_rentals
    soup = BeautifulSoup(html,"html.parser")
    rows = []
    support = "direct_search_parser"
    if source in {"pap","paruvendu","entreparticuliers","ouestfranceimmo"}:
        module = __import__("scraper."+source,fromlist=["_"])
        classes = {"pap":"PAPScraper","paruvendu":"ParuVenduScraper","entreparticuliers":"EntreParticuliersScraper","ouestfranceimmo":"OuestFranceImmoScraper"}
        rows = getattr(module,classes[source])()._parse_cards(html)
    elif source == "rentola":
        rows = jsonld_rentals(html,source,"https://rentola.fr")
    elif source == "leboncoin":
        from scraper.leboncoin import LeboncoinScraper
        rows = LeboncoinScraper()._listings_from_next_data(soup)
    elif source == "seloger":
        from scraper.seloger import SeLogerScraper
        parser = SeLogerScraper()
        with patch.object(parser,"_discover_result_url",return_value=url),patch("utils.http.fetch_html",return_value=html):
            rows = parser._legacy_html(city,postal)
        support = "legacy_search_parser_missing_fields_possible"
    elif source == "logicimmo":
        # This connector normally hydrates detail pages. The benchmark is one
        # search-page request, so extraction completeness is not comparable here.
        support = "detail_hydration_required"
    valid = [item for row in rows if (item:=normalize_listing(row)) is not None]
    links = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if re.search(r"/(?:ad/locations|annonces?|listings|ref-\d|expose)/",href):
            links.add(href.split("?")[0])
    coverage = {field:sum(getattr(item,field) is not None and getattr(item,field)!=[] for item in valid) for field in ["price","surface_m2","rooms","location","images"]}
    return {"parser_support":support,"raw_count":len(rows),"valid_count":len(valid),"dropped_count":len(rows)-len(valid),"property_links":len(links),
            "field_counts":coverage,"listings":[{**asdict(item),"quality_flags":listing_quality_flags(item)} for item in valid]}


def worker(args):
    from utils.http import is_probably_blocked
    task = json.loads(Path(args.task).read_text())
    directory = Path(args.output)
    directory.mkdir(parents=True,exist_ok=True)
    html, metadata = fetch_scrapling(task["url"]) if args.engine == "scrapling" else fetch_firecrawl(task["url"],directory)
    result = {**task,"engine":args.engine,"round":args.round,**metadata,"retrieved_at":datetime.now(timezone.utc).isoformat()}
    result["html_ok"] = bool(html and metadata.get("http_status")==200 and not is_probably_blocked(html))
    result["raw_count"], result["valid_count"] = 0, 0
    if html:
        encoded = html.encode()
        result["html_sha256"],result["html_bytes"] = hashlib.sha256(encoded).hexdigest(),len(encoded)
        with gzip.open(directory/"page.html.gz","wt",encoding="utf-8") as f:
            f.write(html)
    if result["html_ok"]:
        started = time.monotonic()
        try:
            result.update(parse_page(task["source"],html,task["url"],task["city"],task["postal"]))
        except Exception as exc:
            result["parse_error_type"] = type(exc).__name__
        result["parse_seconds"] = round(time.monotonic()-started,3)
    (directory/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))


def trial(task, engine, round_number, root, locks):
    directory = root/engine/f"round-{round_number}"/task["id"]
    directory.mkdir(parents=True,exist_ok=True)
    task_file = directory/"task.json"
    task_file.write_text(json.dumps(task))
    command = [sys.executable,str(Path(__file__).resolve()),"--worker","--task",str(task_file),"--engine",engine,"--round",str(round_number),"--output",str(directory)]
    with locks[urlsplit(task["url"]).hostname]:
        with (directory/"worker.log").open("w") as log:
            process = subprocess.Popen(command,stdout=log,stderr=log,start_new_session=True)
            try:
                process.wait(timeout=70)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait()
                (directory/"result.json").write_text(json.dumps({**task,"engine":engine,"round":round_number,"status":"hard_timeout","elapsed_seconds":70,"html_ok":False,"raw_count":0,"valid_count":0}))
    file = directory/"result.json"
    if not file.exists():
        result={**task,"engine":engine,"round":round_number,"status":"worker_error","exit_code":process.returncode,"html_ok":False,"raw_count":0,"valid_count":0}
        file.write_text(json.dumps(result))
    return json.loads(file.read_text())


def summarize(root):
    results=[json.loads(p.read_text()) for p in root.glob("*/round-*/*/result.json")]
    rows=[]
    for engine in sorted({r["engine"] for r in results}):
        for source in sorted({r["source"] for r in results if r["engine"]==engine}):
            subset=[r for r in results if r["engine"]==engine and r["source"]==source]
            timings=[r["elapsed_seconds"] for r in subset if "elapsed_seconds" in r]
            successes=[r for r in subset if r.get("valid_count",0)>0]
            valid_timings=[r["elapsed_seconds"] for r in successes]
            urls={item["url"] for r in subset for item in r.get("listings",[])}
            rows.append({"engine":engine,"source":source,"attempts":len(subset),"html_successes":sum(bool(r.get("html_ok")) for r in subset),
                         "extraction_successes":len(successes),"median_seconds_all":round(statistics.median(timings),3) if timings else None,
                         "median_seconds_successful":round(statistics.median(valid_timings),3) if valid_timings else None,
                         "valid_count_range":[min((r.get("valid_count",0) for r in successes),default=0),max((r.get("valid_count",0) for r in successes),default=0)],
                         "unique_listing_urls":len(urls),"browser_uses":sum(r.get("phase")=="browser" or any(p.get("phase")=="browser" for p in r.get("phases",[])) for r in subset)})
    summary={"generated_at":datetime.now(ZoneInfo("Europe/Paris")).isoformat(),"attempts":len(results),"rows":rows,
             "all_unique_listing_urls":len({item["url"] for r in results for item in r.get("listings",[])})}
    (root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


def main(args):
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError("Use a new, empty output directory to avoid mixing benchmark runs")
    from config import settings
    from utils.firecrawl import firecrawl_status
    cli_version=subprocess.run(["firecrawl","--version"],capture_output=True,text=True).stdout.strip()
    before=account_usage()
    environment={"started_at":datetime.now(ZoneInfo("Europe/Paris")).isoformat(),"scrapling_version":importlib.metadata.version("scrapling"),"firecrawl_cli_version":cli_version,
        "firecrawl_status":asdict(firecrawl_status()),"credit_usage_before":before,"proxy_configured":bool(settings.proxy_url),"api_key_configured":bool(settings.firecrawl_api_key),
        "rounds":args.rounds,"concurrency":2,"per_domain_concurrency":1,"local_cache":False,"firecrawl_max_age":0,
        "scrapling_http_timeout":25,"scrapling_browser_timeout":25,"browser_wait_ms":1000,"hard_trial_budget_seconds":70,
        "parsers_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path("scraper").glob("*.py")}}
    (root/"environment.json").write_text(json.dumps(environment,ensure_ascii=False,indent=2))
    pages=corpus();(root/"corpus.json").write_text(json.dumps(pages,ensure_ascii=False,indent=2))
    # Diagnose account failure on a trivial control and on an actual portal.
    probes=[]
    for url in ["https://example.com",next(p["url"] for p in pages if p["source"]=="pap")]:
        probe=firecrawl_api_probe(url);probes.append(probe)
    directory=root/"firecrawl-cli-json-probe";directory.mkdir(exist_ok=True)
    _, cli_probe=fetch_firecrawl(next(p["url"] for p in pages if p["source"]=="pap"),directory,extraction=True)
    (root/"firecrawl_preflight.json").write_text(json.dumps({"api_probes":probes,"configured_cli_json_probe":cli_probe},ensure_ascii=False,indent=2))
    quota=before.get("usage",{}).get("remainingCredits")==0 or any(p.get("provider_http_status")==402 for p in probes)
    engines=["scrapling"] if quota else ["scrapling","firecrawl"]
    environment["firecrawl_corpus_status"]="not_run_quota_exhausted" if quota else "scheduled"
    (root/"environment.json").write_text(json.dumps(environment,ensure_ascii=False,indent=2))
    print(json.dumps({"preflight":before,"firecrawl_corpus_status":environment["firecrawl_corpus_status"],"trials":len(pages)*args.rounds*len(engines)}),flush=True)
    locks={urlsplit(p["url"]).hostname:threading.Lock() for p in pages}
    with ThreadPoolExecutor(max_workers=2) as executor:
        for round_number in range(1,args.rounds+1):
            jobs=[(page,engine) for page in pages for engine in engines]
            random.Random(20261024+round_number).shuffle(jobs)
            futures=[executor.submit(trial,page,engine,round_number,root,locks) for page,engine in jobs]
            for future in as_completed(futures):
                r=future.result()
                print(json.dumps({k:r.get(k) for k in ["engine","source","postal","round","status","phase","elapsed_seconds","html_ok","valid_count"]}),flush=True)
    environment["credit_usage_after"]=account_usage();environment["finished_at"]=datetime.now(ZoneInfo("Europe/Paris")).isoformat()
    (root/"environment.json").write_text(json.dumps(environment,ensure_ascii=False,indent=2))
    print(json.dumps(summarize(root),ensure_ascii=False),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",required=True);p.add_argument("--rounds",type=int,default=3);p.add_argument("--worker",action="store_true");p.add_argument("--task");p.add_argument("--engine",choices=["scrapling","firecrawl"]);p.add_argument("--round",type=int);p.add_argument("--summarize-only",action="store_true")
    args=p.parse_args()
    if args.worker:worker(args)
    elif args.summarize_only:print(json.dumps(summarize(Path(args.output)),ensure_ascii=False))
    else:main(args)
