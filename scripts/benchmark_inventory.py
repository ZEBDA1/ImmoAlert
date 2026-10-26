"""Read-only timing of the real inventory; no portal fetch or user mutation."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import math
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


def measurements(call,count):
    durations=[];result=None
    for _ in range(count):
        started=perf_counter();result=call();durations.append((perf_counter()-started)*1000)
    durations.sort()
    return result,{'samples':count,'median_ms':round(median(durations),2),'p95_ms':round(durations[math.ceil(.95*count)-1],2)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runs',type=int,default=5);parser.add_argument('--base-url');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.runs<=20:parser.error('--runs must be between 1 and 20')
    from database import repo
    from services.inventory import listing_page
    from services.performance import summary
    from services.favorite_freshness import freshness_plan
    page,timings=measurements(lambda:listing_page(-1,{'limit':'18','page':'1','sort':'recent'}),args.runs)
    identifiers=[item['id'] for item in page['items'][:5]]
    _,reads=measurements(lambda:[repo.get_listing_by_id(identifier) for identifier in identifiers],args.runs)
    report={'measured_at':datetime.now(timezone.utc).isoformat(),'inventory_count':page['total'],'inventory_page':timings,
            'local_listing_reads':{**reads,'listings_per_sample':len(identifiers)},'production_metrics':summary(),'freshness':freshness_plan(),
            'method':'Bounded reads of the current inventory, no collection; cold first sample retained. No listing content or user identifier exported.'}
    if args.base_url:
        if not args.base_url.startswith(('http://127.0.0.1:','http://localhost:')):parser.error('--base-url must be loopback')
        report['anonymous_api']={}
        for path in ('/api/health','/api/bootstrap'):
            def read():
                with urllib.request.urlopen(args.base_url.rstrip('/')+path,timeout=10) as response:return response.status
            code,values=measurements(read,args.runs);report['anonymous_api'][path]={'status':code,**values}
    text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    else:print(text,end='')


if __name__=='__main__':main()
