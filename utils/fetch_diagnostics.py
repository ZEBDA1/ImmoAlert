"""Bounded per-worker diagnostics. Never retain headers, cookies or response bodies."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import traceback
from threading import Lock

_problems: ContextVar[list[str] | None] = ContextVar("fetch_problems", default=None)
_events: ContextVar[list[dict] | None] = ContextVar("fetch_events", default=None)
_source: ContextVar[str | None] = ContextVar("fetch_source", default=None)
_log_lock=Lock()
_diagnostic_logger=None


def safe_message(value) -> str:
    text=str(value or "")
    # URLs in exception strings can contain signed queries or embedded credentials.
    text=re.sub(r'https?://[^\s<>\"\']+',lambda match:safe_url(match.group()),text)
    text=re.sub(r'(?i)\bBearer\s+[^\s,;]+','Bearer [masqué]',text)
    text=re.sub(r'\b(?:fpk_live_[A-Za-z0-9_-]+|fc-[A-Za-z0-9_-]{12,})\b','[masqué]',text)
    text=re.sub(r'(?i)([\"\']?(?:authorization|cookie|token|api[_-]?key|password|secret)[\"\']?\s*[=:]\s*)[\"\']?[^\s,;]+',r'\1[masqué]',text)
    return text[:800]


def safe_url(value) -> str:
    try:
        parsed=urlsplit(value or '')
        return f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"[:500] if parsed.hostname else ''
    except ValueError:
        return ''


def note_event(code: str, url: str = "", *, stage="fetch", engine=None, status=None, elapsed=None, message="", exception=None, **values):
    events=_events.get()
    if events is None or len(events)>=80:
        return
    event={"at":datetime.utcnow().isoformat()+"Z","code":str(code)[:80],"stage":str(stage)[:40]}
    if url:event["url"]=safe_url(url)
    if engine:event["engine"]=str(engine)[:40]
    if type(status) is int:event["http_status"]=status
    if elapsed is not None:event["duration_seconds"]=round(max(0,float(elapsed)),3)
    if message:event["message"]=safe_message(message)
    if exception:
        event["exception_type"]=type(exception).__name__
        event["message"]=safe_message(exception)
        event["trace"]=[{"file":Path(frame.filename).name,"line":frame.lineno,"function":frame.name} for frame in traceback.extract_tb(exception.__traceback__)[-5:]]
    for key in ("raw_count","valid_count","rejected_count"):
        if type(values.get(key)) is int:event[key]=values[key]
    events.append(event)
    if code!="http_ok":
        logging.getLogger('source.fetch').warning('source=%s %s',_source.get() or 'unknown',json.dumps(event,ensure_ascii=False))


def note_problem(kind: str, url: str = "", *, message: str = "", **context) -> None:
    problems = _problems.get()
    if problems is not None:
        summary = f"{kind}: {urlsplit(url).hostname}" if url else kind
        if message:
            summary += " (" + safe_message(message)[:300] + ")"
        if summary not in problems:
            problems.append(summary)
    note_event(kind,url,message=message,**context)


@contextmanager
def capture_problems(problems: list[str], events: list[dict] | None = None, *, source: str | None = None):
    token = _problems.set(problems)
    event_token=_events.set(events if events is not None else _events.get())
    source_token=_source.set(source if source is not None else _source.get())
    try:
        yield
    finally:
        _problems.reset(token)
        _events.reset(event_token)
        _source.reset(source_token)


def decode_events(value):
    try:
        data=json.loads(value or '[]')
        return data[:80] if isinstance(data,list) else []
    except (ValueError,TypeError):
        return []


def write_source_diagnostics(*, source, cycle_id, status, events):
    """One structured record per run with anomalies; max 20 MB on disk."""
    if not events:
        return
    global _diagnostic_logger
    with _log_lock:
        if _diagnostic_logger is None:
            path=Path('storage/logs/source-diagnostics.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
            handler=RotatingFileHandler(path,maxBytes=5_000_000,backupCount=3,encoding='utf-8')
            logger=logging.getLogger('source.diagnostics.file');logger.setLevel(logging.INFO);logger.propagate=False
            logger.addHandler(handler);_diagnostic_logger=logger
        _diagnostic_logger.info(json.dumps({'at':datetime.utcnow().isoformat()+'Z','source':source,'cycle_id':cycle_id,'status':status,'events':events[:80]},ensure_ascii=False))
