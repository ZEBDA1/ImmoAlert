"""Conservative property matching across portals; basic criteria alone never suffice."""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata

from scraper.quality import extract_postal_code, normalize_city_name
from utils.images import decode_images, image_identity


def normalized(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c))))


def same_location(a, b) -> bool:
    postal_a, postal_b = extract_postal_code(a.location), extract_postal_code(b.location)
    if postal_a and postal_b and postal_a != postal_b:
        return False
    city_a, city_b = normalized(normalize_city_name(a.location)), normalized(normalize_city_name(b.location))
    if not city_a or not city_b:
        return False
    full_a=" "+normalized(re.sub(r"\b\d{5}\b","",a.location or ""))+" "
    full_b=" "+normalized(re.sub(r"\b\d{5}\b","",b.location or ""))+" "
    return city_a==city_b or bool(postal_a and postal_a==postal_b and (" "+city_a+" " in full_b or " "+city_b+" " in full_a))


def same_property(a, b) -> bool:
    if a.source == b.source or not same_location(a,b):
        return False
    if not a.price or not b.price or abs(a.price-b.price) > max(30, min(a.price,b.price)*.05):
        return False
    if not a.surface_m2 or not b.surface_m2 or abs(a.surface_m2-b.surface_m2) > max(.6, min(a.surface_m2,b.surface_m2)*.02):
        return False
    if a.rooms and b.rooms and a.rooms != b.rooms:
        return False
    # Same CDN photo with a different query size is strong evidence, alongside
    # the independently checked location, rent, surface and room count.
    def photos(row):
        return {image_identity(url) for url in decode_images(row.images)}
    if photos(a) & photos(b):
        return True
    left, right = normalized(a.description), normalized(b.description)
    if min(len(left),len(right)) < 120 or min(len(set(left.split())),len(set(right.split()))) < 18:
        return False
    # Exclude generic agency boilerplate at the end; compare actual wording
    # using ordered phrases rather than only a bag of common property words.
    left, right = left[:5000], right[:5000]
    def shingles(text):
        words = text.split()
        return {tuple(words[i:i+4]) for i in range(len(words)-3)}
    sa, sb = shingles(left), shingles(right)
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    sequence=SequenceMatcher(None,left,right,autojunk=False)
    if overlap>=.78 and sequence.ratio()>=.72:
        return True
    # A search card can contain only the beginning of the same description.
    # Require a long ordered passage near the beginning of both texts.
    block=sequence.find_longest_match(0,min(len(left),1400),0,min(len(right),1400))
    return overlap>=.9 and block.size>=180 and block.size/min(len(left),len(right))>=.8 and max(block.a,block.b)<250
