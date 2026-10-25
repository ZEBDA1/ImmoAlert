"""Per-fetch diagnostics, isolated between concurrent scraper workers."""
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

_problems: ContextVar[list[str] | None] = ContextVar("fetch_problems", default=None)


def note_problem(kind: str, url: str = "") -> None:
    problems = _problems.get()
    if problems is not None:
        message = f"{kind}: {urlsplit(url).hostname}" if url else kind
        if message not in problems:
            problems.append(message)


@contextmanager
def capture_problems(problems: list[str]):
    token = _problems.set(problems)
    try:
        yield
    finally:
        _problems.reset(token)
