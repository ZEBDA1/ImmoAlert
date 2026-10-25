"""Extract the current advertisement, never neighbouring/recommended properties."""
from __future__ import annotations

import json
import math
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scraper.html_helpers import canonical_url
from utils.images import decode_images


def coordinates(lat, lon):
    try:
        if isinstance(lat,bool) or isinstance(lon,bool):
            return None
        lat, lon = float(lat), float(lon)
        return (lat,lon) if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 else None
    except (TypeError,ValueError):
        return None


def extract_details(html: str, url: str, external_id: str) -> dict:
    soup=BeautifulSoup(html,"html.parser")
    result={"images":[],"features":{}}

    def text(value):
        return BeautifulSoup(str(value),"html.parser").get_text("\n",strip=True) if value is not None else ""

    def images(value):
        if isinstance(value,str):
            candidate=urljoin(url,value)
            if candidate.startswith(("https://","http://")) and not re.search(r"logo|placeholder|no-photo|novisu|transparent",candidate,re.I):
                result["images"].append(candidate)
        elif isinstance(value,list):
            for item in value:
                images(item)
        elif isinstance(value,dict):
            for key in ("url","contentUrl","urls_large","large","src","original","images","photos"):
                if key in value:
                    images(value[key])

    def consume(node):
        for key in ("description","body","ad_description","descriptionText"):
            value=node.get(key)
            if isinstance(value,str) and len(text(value))>len(result.get("description","")):
                result["description"]=text(value)[:60000]
        for key in ("image","images","photos","pictures"):
            if key in node:
                images(node[key])
        address=node.get("address")
        if isinstance(address,dict):
            value=" ".join(str(address.get(k) or "") for k in ("streetAddress","postalCode","addressLocality")).strip()
            if value:
                result["features"]["Adresse publiée"]=value
        geo=node.get("geo") or node.get("geoLocation") or node.get("location") or node
        if isinstance(geo,dict):
            point=coordinates(geo.get("latitude",geo.get("lat")),geo.get("longitude",geo.get("lng",geo.get("lon"))))
            if point:
                result.update(latitude=point[0],longitude=point[1],geo_precision="portal")
        labels={"numberOfRooms":"Pièces","numberOfBedrooms":"Chambres","floorLevel":"Étage",
            "numberOfBathroomsTotal":"Salles de bain","petsAllowed":"Animaux acceptés",
            "availableFrom":"Disponibilité","datePosted":"Date de publication",
            "energyClass":"DPE","energy_class":"DPE","ges":"GES","furnished":"Meublé"}
        for key,label in labels.items():
            value=node.get(key)
            if isinstance(value,(str,int,float,bool)):
                result["features"][label]="Oui" if value is True else "Non" if value is False else str(value)
        for attr in node.get("attributes",[]) if isinstance(node.get("attributes"),list) else []:
            if isinstance(attr,dict):
                label=attr.get("key_label") or attr.get("label") or attr.get("key")
                value=attr.get("value_label",attr.get("value"))
                if label and isinstance(value,(str,int,float,bool)):
                    result["features"][str(label)[:100]]=str(value)[:1000]
        for attr in node.get("amenityFeature",[]) if isinstance(node.get("amenityFeature"),list) else []:
            if isinstance(attr,dict) and attr.get("name"):
                result["features"][str(attr["name"])[:100]]="Non" if attr.get("value") is False else "Oui"
        for attr in node.get("additionalProperty",[]) if isinstance(node.get("additionalProperty"),list) else []:
            if isinstance(attr,dict) and attr.get("name") and isinstance(attr.get("value"),(str,int,float,bool)):
                result["features"][str(attr["name"])[:100]]=str(attr["value"])[:1000]

    def walk(node,ld=False,depth=0):
        if depth>25:
            return
        if isinstance(node,list):
            for value in node:
                walk(value,ld,depth+1)
        elif isinstance(node,dict):
            kind=node.get("@type","")
            kinds=kind if isinstance(kind,list) else [kind]
            node_url=node.get("url") or node.get("@id")
            matches_url=isinstance(node_url,str) and canonical_url(node_url,url)==canonical_url(url)
            matches_id=any(str(node.get(k,""))==external_id for k in ("id","list_id","classifiedId","adId"))
            listing_kind=bool(set(kinds)&{"RealEstateListing","Apartment","House","Residence","Accommodation","Product"})
            if matches_id or (ld and listing_kind and (matches_url or not node_url)):
                consume(node)
                # Structured property and offer are part of this same ad.
                for key in ("about","mainEntity","itemOffered","offers"):
                    value=node.get(key)
                    if isinstance(value,dict):
                        consume(value)
                        if isinstance(value.get("itemOffered"),dict):
                            consume(value["itemOffered"])
                return
            for key,value in node.items():
                if key.lower() not in {"recommendations","similar","similarlistings","related","itemlistelement","searchresults"}:
                    walk(value,ld,depth+1)

    for script in soup.select('script[type="application/ld+json"],script[type="application/json"],script#__NEXT_DATA__'):
        try:
            walk(json.loads(script.string or script.get_text()),script.get("type")=="application/ld+json")
        except (ValueError,TypeError):
            continue
    # OuestFrance publishes a Nuxt JavaScript object rather than JSON. Read
    # literals from the balanced current-ad object without evaluating scripts.
    for script in soup.find_all("script"):
        raw=script.get_text()
        match=re.search(r'\bannonce:\{id:'+re.escape(external_id)+r'\s*[,}]',raw)
        if not match:
            continue
        start=raw.index("{",match.start())
        depth=0; quote=None; escaped=False; end=start
        for end in range(start,len(raw)):
            char=raw[end]
            if quote:
                if escaped:
                    escaped=False
                elif char=="\\":
                    escaped=True
                elif char==quote:
                    quote=None
            elif char in ('"',"'"):
                quote=char
            elif char=="{":
                depth+=1
            elif char=="}":
                depth-=1
                if depth==0:
                    break
        scoped=raw[start:end+1]
        literal=r'"(?:[^"\\]|\\.)*"'
        match=re.search(r'\btexte:('+literal+r')',scoped)
        if match:
            try:
                result["description"]=text(json.loads(match.group(1)))[:60000]
            except ValueError:
                pass
        match=re.search(r'\bphotos:\[([^\]]*)\]',scoped)
        if match:
            for value in re.findall(literal,match.group(1)):
                try:
                    images(json.loads(value))
                except ValueError:
                    continue
        lat=re.search(r'\blatitude:(-?\d+(?:\.\d+)?)',scoped)
        lon=re.search(r'\blongitude:(-?\d+(?:\.\d+)?)',scoped)
        point=coordinates(lat.group(1),lon.group(1)) if lat and lon else None
        if point:
            result.update(latitude=point[0],longitude=point[1],geo_precision="portal")
    for selector in ('[data-qa-id="adview_description_container"]','[data-testid="description"]','[data-testid="description-text"]','[itemprop="description"]','.annonce-description','.description-annonce','.detail-description','#description'):
        node=soup.select_one(selector)
        if node:
            value=node.get_text("\n",strip=True)
            if len(value)>len(result.get("description","")):
                result["description"]=value[:60000]
    # Galleries are explicitly scoped. Page-wide img would include other homes.
    for gallery in soup.select('[data-testid="gallery"],[data-qa-id="adview_spotlight_container"],[itemprop="photo"],.gallery,.photo-gallery,.annonce-photos,.detail-page__gallery'):
        for img in gallery.select("img"):
            images(img.get("data-src") or img.get("src"))
        for link in gallery.select("a[href]"):
            if re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)",link["href"],re.I):
                images(link["href"])
    for meta in soup.select('meta[property="og:image"]'):
        images(meta.get("content"))
    for node in soup.select("main dl,article dl,[data-testid='characteristics'] dl"):
        for label in node.select("dt"):
            value=label.find_next_sibling("dd")
            if value:
                result["features"][label.get_text(" ",strip=True)[:100]]=value.get_text(" ",strip=True)[:1000]
    for node in soup.select(".detail-caracteristiques .detail-info"):
        label=node.select_one(".detail-info__label")
        value=node.select_one(".detail-info__value")
        if label and value:
            key=label.get_text(" ",strip=True).rstrip(" :")
            extra=node.select_one(".detail-info__text-additional")
            result["features"][key[:100]]=" ".join([value.get_text(" ",strip=True),extra.get_text(" ",strip=True) if extra else ""]).strip()[:1000]
    diagnostic=soup.select_one(".detail-diagnostics__summary .diagnostic-etiquette")
    if diagnostic:
        result["features"]["Diagnostic énergétique"]=diagnostic.get_text(" ",strip=True)
    identities=set();unique=[]
    for image in decode_images(result["images"]):
        identity=re.sub(r'_rcrop_\d+-\d+_',"",image) if "ouestfrance-immo.com/" in image else image
        if identity not in identities:
            identities.add(identity);unique.append(image)
    result["images"]=unique
    return result
