"""Read historical photo values without splitting commas inside CDN URLs."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def decode_images(value: str | list | None) -> list[str]:
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, str) and value.lstrip().startswith("["):
        try:
            candidates = json.loads(value)
        except ValueError:
            candidates = []
    elif isinstance(value, str):
        candidates = re.split(r",(?=https?://)", value)
    else:
        candidates = []
    if not isinstance(candidates, list):
        return []
    return list(dict.fromkeys(url for url in candidates if isinstance(url, str) and url.startswith(("https://", "http://"))))


def encode_images(value: list[str] | None) -> str | None:
    images = decode_images(value)
    return json.dumps(images, ensure_ascii=False) if images else None


def image_identity(url: str) -> str:
    """Ignore rendering sizes, preserving query parameters that identify a photo."""
    parts=urlsplit(url)
    path=re.sub(r"_rcrop_\d+-\d+_", "", parts.path) if (parts.hostname or "").endswith("ouestfrance-immo.com") else parts.path
    rendering={"w","h","width","height","quality","q","fit","crop","auto"}
    query=urlencode(sorted((key,value) for key,value in parse_qsl(parts.query,keep_blank_values=True) if key.lower() not in rendering))
    return urlunsplit((parts.scheme,parts.netloc,path,query,""))


def merge_images(*values) -> list[str]:
    result=[];seen=set()
    for value in values:
        for url in decode_images(value):
            key=image_identity(url)
            if key not in seen:
                seen.add(key);result.append(url)
    return result
