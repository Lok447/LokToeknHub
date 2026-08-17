from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401

    if not settings.auto_create_schema:
        return
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}
    usage_columns = {column["name"] for column in inspector.get_columns("usage_records")}
    with engine.begin() as connection:
        if "account_id" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN account_id INTEGER"))
        if "expires_at" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN expires_at DATETIME"))
        if "spending_limit_micros" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN spending_limit_micros INTEGER"))
        if "spent_micros" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN spent_micros INTEGER NOT NULL DEFAULT 0"))
            connection.execute(text(
                "UPDATE api_keys SET spent_micros = COALESCE((SELECT SUM(amount_micros) FROM usage_records "
                "WHERE usage_records.api_key_id = api_keys.id AND usage_records.status = 'success'), 0)"
            ))
        if "last_used_at" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN last_used_at DATETIME"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_expires_at ON api_keys (expires_at)"))
        if "account_id" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_records ADD COLUMN account_id INTEGER"))

    inspector = inspect(engine)
    api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}
    legacy_balance_column = "balance_micros" in api_key_columns
    balance_expression = "balance_micros" if legacy_balance_column else "0 AS balance_micros"
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        legacy_keys = connection.execute(text(
            f"SELECT id, name, {balance_expression} FROM api_keys WHERE account_id IS NULL"
        )).mappings().all()
        for key in legacy_keys:
            external_user_id = f"legacy-key-{key['id']}"
            account_id = connection.execute(
                text("SELECT id FROM billing_accounts WHERE external_user_id = :external_user_id"),
                {"external_user_id": external_user_id},
            ).scalar_one_or_none()
            if account_id is None:
                connection.execute(text(
                    "INSERT INTO billing_accounts (external_user_id, name, balance_micros, active, created_at) "
                    "VALUES (:external_user_id, :name, :balance_micros, :active, :created_at)"
                ), {
                    "external_user_id": external_user_id,
                    "name": f"Migrated {key['name']}",
                    "balance_micros": key["balance_micros"],
                    "active": True,
                    "created_at": now,
                })
                account_id = connection.execute(
                    text("SELECT id FROM billing_accounts WHERE external_user_id = :external_user_id"),
                    {"external_user_id": external_user_id},
                ).scalar_one()
            connection.execute(
                text("UPDATE api_keys SET account_id = :account_id WHERE id = :api_key_id"),
                {"account_id": account_id, "api_key_id": key["id"]},
            )

        connection.execute(text(
            "UPDATE usage_records SET account_id = "
            "(SELECT api_keys.account_id FROM api_keys WHERE api_keys.id = usage_records.api_key_id) "
            "WHERE account_id IS NULL"
        ))

        connection.execute(text(
            "INSERT INTO model_channels "
            "(model_config_id, name, provider_base_url, upstream_model, provider_api_key_env, priority, weight, "
            "active, status, consecutive_failures, created_at) "
            "SELECT model_configs.id, 'Primary', model_configs.provider_base_url, model_configs.upstream_model, "
            "model_configs.provider_api_key_env, 100, 100, model_configs.active, 'unknown', 0, :created_at "
            "FROM model_configs WHERE NOT EXISTS ("
            "SELECT 1 FROM model_channels WHERE model_channels.model_config_id = model_configs.id)"
        ), {"created_at": now})

        if "balance_transactions" in inspector.get_table_names():
            legacy_transactions = connection.execute(text(
                "SELECT id, api_key_id, amount_micros, transaction_type, reference_id, description, created_at "
                "FROM balance_transactions ORDER BY id"
            )).mappings().all()
            for item in legacy_transactions:
                if connection.execute(
                    text("SELECT id FROM account_balance_transactions WHERE reference_id = :reference_id"),
                    {"reference_id": item["reference_id"]},
                ).scalar_one_or_none():
                    continue
                account_id = connection.execute(
                    text("SELECT account_id FROM api_keys WHERE id = :api_key_id"),
                    {"api_key_id": item["api_key_id"]},
                ).scalar_one_or_none()
                if account_id is None:
                    continue
                connection.execute(text(
                    "INSERT INTO account_balance_transactions "
                    "(account_id, api_key_id, amount_micros, transaction_type, reference_id, description, created_at) "
                    "VALUES (:account_id, :api_key_id, :amount_micros, :transaction_type, :reference_id, :description, :created_at)"
                ), {
                    "account_id": account_id,
                    "api_key_id": item["api_key_id"],
                    "amount_micros": item["amount_micros"],
                    "transaction_type": item["transaction_type"],
                    "reference_id": item["reference_id"],
                    "description": item["description"],
                    "created_at": item["created_at"],
                })


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
