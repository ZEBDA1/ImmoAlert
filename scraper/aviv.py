"""Parse the native search payload shared by SeLoger and Logic-Immo.

Only the main result IDs are used: suggested and enlarged results are excluded.
The JSON.parse string is decoded as data, never executed as JavaScript.
"""
import json
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from scraper.base import ScrapedListing
from utils.text import clean_text,parse_price_eur,parse_rooms,parse_surface_m2
from utils.fetch_diagnostics import note_event


def search_rows(html: str, source: str) -> list[ScrapedListing]:
    soup=BeautifulSoup(html,'lxml')
    script=soup.select_one('script#__UFRN_FETCHER__')
    if not script:return []
    try:
        text=script.string or script.get_text()
        encoded=json.JSONDecoder().raw_decode(text.split('JSON.parse(',1)[1])[0]
        state=json.loads(encoded)
        payload=state['data']['classified-serp-data']
        items=payload['classifiedsData']
        identifiers=payload['classifieds']
        if not isinstance(items,dict) or not isinstance(identifiers,list):raise ValueError('Invalid result payload')
    except (ValueError,TypeError,KeyError,IndexError) as error:
        note_event('payload_changed',stage='extraction',engine=source,message='Le format du JSON de recherche a changé.',exception=error)
        return []
    rows=[];seen=set()
    domains={'seloger':{'www.seloger.com','seloger.com'},'logicimmo':{'www.logic-immo.com','logic-immo.com'}}
    for identifier in identifiers:
        item=items.get(str(identifier))
        if not isinstance(item,dict):continue
        raw=item.get('rawData') or {}
        if str(raw.get('distributionType','')).upper() not in {'RENT','RENTAL'}:continue
        facts=item.get('hardFacts') or {};price=facts.get('price') or {}
        address=(item.get('location') or {}).get('address') or {}
        values={fact.get('type'):str(fact.get('value') or '') for fact in facts.get('facts',[]) if isinstance(fact,dict)}
        description=item.get('mainDescription') or {}
        url=item.get('url') or ''
        if urlsplit(url).scheme not in {'http','https'} or urlsplit(url).hostname not in domains[source] or identifier in seen:continue
        title=clean_text(description.get('headline') or facts.get('title') or facts.get('shortTitle'))
        place=' '.join(str(value) for value in [address.get('city'),address.get('zipCode')] if value)
        rows.append(ScrapedListing(source=source,external_id=str(item.get('id') or identifier),url=url,
            title=title,price=parse_price_eur(str(price.get('formatted') or price.get('value') or ''),monthly_only=True),
            surface_m2=parse_surface_m2(values.get('livingSpace','')),rooms=parse_rooms(values.get('numberOfRooms','')),
            location=place or None,description=clean_text(description.get('description')),
            images=[image['url'] for image in (item.get('gallery') or {}).get('images',[]) if isinstance(image,dict) and isinstance(image.get('url'),str) and urlsplit(image['url']).scheme in {'https','http'}] or None))
        seen.add(identifier)
    if identifiers and not rows:
        note_event('payload_no_rentals',stage='extraction',engine=source,raw_count=len(identifiers),valid_count=0,message='Résultats présents sans location exploitable dans le payload.')
    return rows
