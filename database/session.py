from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool
from config import settings


Base = declarative_base()


def _make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if url == "sqlite://" else None,
            future=True,
        )
    else:
        engine = create_engine(url, pool_pre_ping=True, future=True)
    return engine


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
    expire_on_commit=False,
)


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _run_light_migrations()


def _column_exists(table: str, column: str) -> bool:
    try:
        with engine.connect() as conn:
            if settings.database_url.startswith("sqlite"):
                rows = conn.execute(text(f"PRAGMA table_info('{table}')")).fetchall()
                for r in rows:
                    if len(r) >= 2 and (r[1] == column or getattr(r, "name", None) == column):
                        return True
                return False
            else:
                # Generic information_schema check for non-sqlite
                q = text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = :t AND column_name = :c
                    LIMIT 1
                    """
                )
                res = conn.execute(q, {"t": table, "c": column}).first()
                return bool(res)
    except Exception:
            return False


def _add_column_if_missing(table: str, column: str, definition: str) -> None:
    if _column_exists(table, column):
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def _create_index_if_missing(index_name: str, table: str, column: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"))


def _run_light_migrations() -> None:
    # Add nullable columns only. This keeps SQLite migrations safe and rollback-friendly.
    try:
        datetime_type = "DATETIME" if settings.database_url.startswith("sqlite") else "TIMESTAMP NULL"
        _add_column_if_missing("users", "premium_until", datetime_type)

        listing_columns = {
            "canonical_signature": "VARCHAR(64)",
            "quality_flags": "TEXT",
            "normalized_city": "VARCHAR(120)",
            "normalized_postal_code": "VARCHAR(16)",
            "charges_included": "BOOLEAN",
        }
        for column, definition in listing_columns.items():
            _add_column_if_missing("listings", column, definition)

        alert_columns = {
            "filter_id": "INTEGER",
            "filter_name": "VARCHAR(64)",
            "score_value": "FLOAT",
            "score_label": "VARCHAR(64)",
            "match_reasons": "TEXT",
        }
        for table in ("sent_alerts", "pending_alerts"):
            for column, definition in alert_columns.items():
                _add_column_if_missing(table, column, definition)

        _create_index_if_missing("ix_listings_canonical_signature", "listings", "canonical_signature")
        _create_index_if_missing("ix_listings_normalized_city", "listings", "normalized_city")
        _create_index_if_missing("ix_listings_normalized_postal_code", "listings", "normalized_postal_code")
        _create_index_if_missing("ix_sent_alerts_filter_id", "sent_alerts", "filter_id")
        _create_index_if_missing("ix_pending_alerts_filter_id", "pending_alerts", "filter_id")

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE listings
                    SET canonical_signature = lower(source || '|' || external_id)
                    WHERE canonical_signature IS NULL
                    """
                )
            )
    except Exception:
        # Best-effort migration; ignore if not applicable
        pass
