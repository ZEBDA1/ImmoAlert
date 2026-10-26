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
    _allow_users_without_telegram()


def _allow_users_without_telegram():
    """Rebuild only the legacy SQLite users table, retaining IDs and indexes."""
    from sqlalchemy import inspect, MetaData
    from sqlalchemy.schema import CreateTable, CreateIndex
    from .models import User
    columns=inspect(engine).get_columns("users")
    if not next(column for column in columns if column["name"]=="telegram_id")["nullable"]:
        if engine.dialect.name!="sqlite":
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL"))
            return
        table=User.__table__.to_metadata(MetaData(),name="users_login_migration")
        names=", ".join('"'+column.name+'"' for column in User.__table__.columns)
        with engine.connect() as conn:
            foreign_keys=conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
            conn.commit()
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
            try:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                conn.execute(CreateTable(table))
                conn.exec_driver_sql(f"INSERT INTO users_login_migration ({names}) SELECT {names} FROM users")
                conn.exec_driver_sql("DROP TABLE users")
                conn.exec_driver_sql("ALTER TABLE users_login_migration RENAME TO users")
                for index in User.__table__.indexes:
                    conn.execute(CreateIndex(index))
                if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("User migration failed referential integrity check")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
                conn.commit()


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
        _add_column_if_missing("provider_configurations", "enabled", "BOOLEAN")
        _add_column_if_missing("provider_configurations", "keyless_blocked", "BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing("users", "premium_until", datetime_type)
        _add_column_if_missing("source_runs", "diagnostics_json", "TEXT")
        _add_column_if_missing("web_sessions", "user_id", "INTEGER")
        _add_column_if_missing("web_sessions", "web_account_id", "INTEGER")
        _add_column_if_missing("web_commands", "profile_user_id", "INTEGER")
        for column,definition in {
            "price":"INTEGER", "availability_status":"VARCHAR(24)",
            "availability_checked_at":datetime_type, "availability_detail":"VARCHAR(200)",
            "absence_count":"INTEGER NOT NULL DEFAULT 0", "absence_first_at":datetime_type,
        }.items():
            _add_column_if_missing("listing_publications",column,definition)
        for column,definition in {
            "search_text":"TEXT",
            "commute_minutes":"INTEGER", "commute_destination":"VARCHAR(160)",
            "commute_mode":"VARCHAR(12)", "parking":"VARCHAR(12)", "accessibility":"VARCHAR(12)",
        }.items():
            _add_column_if_missing("member_listing_notes",column,definition)
        _create_index_if_missing("ix_web_sessions_user_id", "web_sessions", "user_id")
        _create_index_if_missing("ix_web_commands_profile_user_id", "web_commands", "profile_user_id")
        _create_index_if_missing("ix_publication_availability_checked", "listing_publications", "availability_checked_at")

        listing_columns = {
            "search_text":"TEXT",
            "last_seen_at": datetime_type,
            "inventory_status": "VARCHAR(24)",
            "canonical_signature": "VARCHAR(64)",
            "quality_flags": "TEXT",
            "normalized_city": "VARCHAR(120)",
            "normalized_postal_code": "VARCHAR(16)",
            "charges_included": "BOOLEAN",
            "duplicate_of_id": "INTEGER",
            "details": "TEXT",
            "details_checked_at": datetime_type,
            "details_status": "VARCHAR(24)",
            "latitude": "FLOAT",
            "longitude": "FLOAT",
            "geo_precision": "VARCHAR(24)",
            "removed_at": datetime_type,
            "removed_by": "INTEGER",
        }
        for column, definition in listing_columns.items():
            _add_column_if_missing("listings", column, definition)

        from services.deduplication import normalized
        with engine.begin() as conn:
            missing=conn.execute(text("SELECT id, title, location FROM listings WHERE search_text IS NULL ORDER BY id"))
            while batch:=missing.fetchmany(500):
                conn.execute(text("UPDATE listings SET search_text=:value WHERE id=:id"),[{"id":row[0],"value":normalized((row[1] or "")+" "+(row[2] or ""))} for row in batch])

        with engine.begin() as conn:
            missing=conn.execute(text("SELECT user_id, listing_id, note, commute_destination FROM member_listing_notes WHERE search_text IS NULL"))
            while batch:=missing.fetchmany(500):
                conn.execute(text("UPDATE member_listing_notes SET search_text=:value WHERE user_id=:user_id AND listing_id=:listing_id"),[{"user_id":row[0],"listing_id":row[1],"value":normalized((row[2] or "")+" "+(row[3] or ""))} for row in batch])

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
        _create_index_if_missing("ix_listings_duplicate_of_id", "listings", "duplicate_of_id")
        _create_index_if_missing("ix_listings_inventory_status", "listings", "inventory_status")
        _create_index_if_missing("ix_listings_last_seen_at", "listings", "last_seen_at")
        _create_index_if_missing("ix_listings_removed_at", "listings", "removed_at")
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
