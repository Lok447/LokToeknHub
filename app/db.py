from collections.abc import Generator
from datetime import datetime, timezone
import json

from sqlalchemy import create_engine, delete, inspect, select, text
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
    account_columns = {column["name"] for column in inspector.get_columns("billing_accounts")}
    reset_challenge_columns = {column["name"] for column in inspector.get_columns("password_reset_challenges")}
    api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}
    payment_order_columns = {column["name"] for column in inspector.get_columns("payment_orders")}
    usage_columns = {column["name"] for column in inspector.get_columns("usage_records")}
    transaction_columns = {column["name"] for column in inspector.get_columns("account_balance_transactions")}
    channel_columns = {column["name"] for column in inspector.get_columns("model_channels")}
    model_columns = {column["name"] for column in inspector.get_columns("model_configs")}
    provider_connection_columns = {column["name"] for column in inspector.get_columns("provider_connections")}
    with engine.begin() as connection:
        if "login_id" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN login_id VARCHAR(160)"))
        if "password_hash" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN password_hash VARCHAR(256)"))
        if "security_contact" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN security_contact VARCHAR(160)"))
        if "security_contact_verified_at" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN security_contact_verified_at DATETIME"))
        if "session_version" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"))
        if "account_source" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN account_source VARCHAR(24) NOT NULL DEFAULT 'admin'"))
        if "access_mode" not in account_columns:
            connection.execute(text("ALTER TABLE billing_accounts ADD COLUMN access_mode VARCHAR(24) NOT NULL DEFAULT 'api'"))
        # Recover the source of legacy accounts from stable identifiers where it is unambiguous.
        connection.execute(text("UPDATE billing_accounts SET account_source = 'loksystem' WHERE external_user_id LIKE 'loksystem-%' AND account_source = 'admin'"))
        connection.execute(text("UPDATE billing_accounts SET account_source = 'oidc' WHERE external_user_id LIKE 'oidc-%' AND account_source = 'admin'"))
        connection.execute(text("UPDATE billing_accounts SET account_source = 'self_registered' WHERE login_id IS NOT NULL AND login_id <> '' AND account_source = 'admin'"))
        connection.execute(text("UPDATE billing_accounts SET access_mode = 'portal' WHERE login_id IS NOT NULL AND login_id <> ''"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_billing_accounts_account_source ON billing_accounts (account_source)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_billing_accounts_access_mode ON billing_accounts (access_mode)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_billing_accounts_login_id ON billing_accounts (login_id)"))
        if "purpose" not in reset_challenge_columns:
            connection.execute(text("ALTER TABLE password_reset_challenges ADD COLUMN purpose VARCHAR(24) NOT NULL DEFAULT 'password_reset'"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_password_reset_challenges_purpose ON password_reset_challenges (purpose)"))
        if "account_id" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN account_id INTEGER"))
        if "billing_account_id" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN billing_account_id INTEGER"))
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
        if "trial_expires_at" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN trial_expires_at DATETIME"))
        if "trial_token_hash" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN trial_token_hash VARCHAR(64)"))
        if "rotated_from_key_id" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN rotated_from_key_id INTEGER"))
        if "revoked_at" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN revoked_at DATETIME"))
        if "revoke_reason" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN revoke_reason VARCHAR(255)"))
        if "idempotency_key" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN idempotency_key VARCHAR(120)"))
        if "rate_limit_requests" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN rate_limit_requests INTEGER"))
        if "rate_limit_window_seconds" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN rate_limit_window_seconds INTEGER"))
        if "project_id" not in api_key_columns:
            connection.execute(text("ALTER TABLE api_keys ADD COLUMN project_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_expires_at ON api_keys (expires_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_trial_expires_at ON api_keys (trial_expires_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_trial_token_hash ON api_keys (trial_token_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_rotated_from_key_id ON api_keys (rotated_from_key_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_rotated_from_key_id ON api_keys (rotated_from_key_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_revoked_at ON api_keys (revoked_at)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_idempotency_key ON api_keys (idempotency_key) WHERE idempotency_key IS NOT NULL"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_project_id ON api_keys (project_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_api_keys_billing_account_id ON api_keys (billing_account_id)"))
        if "reviewed_by_admin_id" not in payment_order_columns:
            connection.execute(text("ALTER TABLE payment_orders ADD COLUMN reviewed_by_admin_id INTEGER"))
        if "reviewed_at" not in payment_order_columns:
            connection.execute(text("ALTER TABLE payment_orders ADD COLUMN reviewed_at DATETIME"))
        if "review_note" not in payment_order_columns:
            connection.execute(text("ALTER TABLE payment_orders ADD COLUMN review_note TEXT"))
        if "workspace_id" not in payment_order_columns:
            connection.execute(text("ALTER TABLE payment_orders ADD COLUMN workspace_id INTEGER"))
        if "project_id" not in payment_order_columns:
            connection.execute(text("ALTER TABLE payment_orders ADD COLUMN project_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payment_orders_reviewed_by_admin_id ON payment_orders (reviewed_by_admin_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payment_orders_workspace_id ON payment_orders (workspace_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payment_orders_project_id ON payment_orders (project_id)"))
        if "account_id" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_records ADD COLUMN account_id INTEGER"))
        if "workspace_id" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_records ADD COLUMN workspace_id INTEGER"))
        if "project_id" not in usage_columns:
            connection.execute(text("ALTER TABLE usage_records ADD COLUMN project_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_records_workspace_id ON usage_records (workspace_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_records_project_id ON usage_records (project_id)"))
        if "workspace_id" not in transaction_columns:
            connection.execute(text("ALTER TABLE account_balance_transactions ADD COLUMN workspace_id INTEGER"))
        if "project_id" not in transaction_columns:
            connection.execute(text("ALTER TABLE account_balance_transactions ADD COLUMN project_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_account_balance_transactions_workspace_id ON account_balance_transactions (workspace_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_account_balance_transactions_project_id ON account_balance_transactions (project_id)"))
        if "health_source" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN health_source VARCHAR(24) NOT NULL DEFAULT 'unknown'"))
        if "encrypted_api_key" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN encrypted_api_key TEXT"))
        if "credential_source" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN credential_source VARCHAR(24) NOT NULL DEFAULT 'environment'"))
        if "provider_connection_id" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN provider_connection_id INTEGER"))
        if "provider_input_cost_micros_per_1k" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN provider_input_cost_micros_per_1k INTEGER"))
        if "provider_output_cost_micros_per_1k" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN provider_output_cost_micros_per_1k INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_model_channels_health_source ON model_channels (health_source)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_model_channels_provider_connection_id ON model_channels (provider_connection_id)"))
        if "catalog_metadata_json" not in model_columns:
            connection.execute(text("ALTER TABLE model_configs ADD COLUMN catalog_metadata_json TEXT"))
        if "official_pricing_json" not in model_columns:
            connection.execute(text("ALTER TABLE model_configs ADD COLUMN official_pricing_json TEXT"))
        if "pricing_margin_bps" not in model_columns:
            connection.execute(text("ALTER TABLE model_configs ADD COLUMN pricing_margin_bps INTEGER NOT NULL DEFAULT 0"))
        if "task_price_micros" not in model_columns:
            connection.execute(text("ALTER TABLE model_configs ADD COLUMN task_price_micros BIGINT NOT NULL DEFAULT 0"))
        if "provider_task_cost_micros" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN provider_task_cost_micros BIGINT"))
        if "last_latency_ms" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN last_latency_ms INTEGER NOT NULL DEFAULT 0"))
        if "last_status_code" not in channel_columns:
            connection.execute(text("ALTER TABLE model_channels ADD COLUMN last_status_code INTEGER"))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS model_change_records ("
            "id INTEGER PRIMARY KEY, model_config_id INTEGER NOT NULL, actor_type VARCHAR(24) NOT NULL DEFAULT 'admin', "
            "actor_id VARCHAR(120), change_type VARCHAR(32) NOT NULL, changed_fields_json TEXT, before_json TEXT, "
            "after_json TEXT, created_at DATETIME NOT NULL)"
        ))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_model_change_records_model_config_id ON model_change_records (model_config_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_model_change_records_change_type ON model_change_records (change_type)"))
        for name, definition in {
            "balance_micros": "BIGINT",
            "balance_currency": "VARCHAR(12)",
            "balance_status": "VARCHAR(24) NOT NULL DEFAULT 'unknown'",
            "balance_source": "VARCHAR(32)",
            "balance_checked_at": "DATETIME",
            "balance_error": "TEXT",
            "balance_alert_threshold_micros": "BIGINT NOT NULL DEFAULT 0",
            "credential_source": "VARCHAR(24) NOT NULL DEFAULT 'environment'",
        }.items():
            if name not in provider_connection_columns:
                connection.execute(text(f"ALTER TABLE provider_connections ADD COLUMN {name} {definition}"))
        connection.execute(text("UPDATE provider_connections SET credential_source = 'console' WHERE encrypted_api_key IS NOT NULL AND credential_source = 'environment'"))
        connection.execute(text("UPDATE model_channels SET credential_source = 'console' WHERE encrypted_api_key IS NOT NULL AND credential_source = 'environment'"))
        usage_additions = {
            "provider_cost_micros": "INTEGER NOT NULL DEFAULT 0",
            "provider_channel_id": "INTEGER",
            "provider_request_id": "VARCHAR(160)",
            "input_cache_hit_tokens": "INTEGER NOT NULL DEFAULT 0",
            "input_cache_miss_tokens": "INTEGER NOT NULL DEFAULT 0",
            "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
            "price_version": "VARCHAR(64)",
            "route_attempts_json": "TEXT",
            "raw_usage_json": "TEXT",
        }
        for column, definition in usage_additions.items():
            if column not in usage_columns:
                connection.execute(text(f"ALTER TABLE usage_records ADD COLUMN {column} {definition}"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_records_provider_channel_id ON usage_records (provider_channel_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_records_provider_request_id ON usage_records (provider_request_id)"))
        if not settings.mock_mode:
            connection.execute(text(
                "DELETE FROM model_channels WHERE model_config_id IN ("
                "SELECT id FROM model_configs WHERE public_name IN ("
                "'lok-chat', 'lok-reason', 'lok-vision', 'smoke-model', "
                "'deepseek/deepseek-chat', 'deepseek/deepseek-reasoner'))"
            ))
            connection.execute(text(
                "DELETE FROM model_configs WHERE public_name IN ("
                "'lok-chat', 'lok-reason', 'lok-vision', 'smoke-model', "
                "'deepseek/deepseek-chat', 'deepseek/deepseek-reasoner')"
            ))
            # Older console versions labeled prices as /1K while operators entered
            # the market-standard /1M values. Normalize the known DeepSeek trial
            # route once; all new UI writes convert /1M back to the ledger's /1K unit.
            connection.execute(text(
                "UPDATE model_configs SET input_price_micros_per_1k = 3000, output_price_micros_per_1k = 9000 "
                "WHERE public_name = 'deepseek-v4-flash' "
                "AND input_price_micros_per_1k = 3000000 AND output_price_micros_per_1k = 9000000"
            ))

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

        # Every legacy account gets a personal workspace and default project before
        # project attribution is enabled for new API keys and operational records.
        connection.execute(text(
            "INSERT INTO workspaces (name, workspace_type, owner_account_id, active, created_at) "
            "SELECT billing_accounts.name || ' 的个人空间', 'personal', billing_accounts.id, 1, :created_at "
            "FROM billing_accounts WHERE NOT EXISTS ("
            "SELECT 1 FROM workspaces WHERE workspaces.owner_account_id = billing_accounts.id "
            "AND workspaces.workspace_type = 'personal')"
        ), {"created_at": now})
        connection.execute(text(
            "INSERT INTO projects (workspace_id, name, slug, active, created_at) "
            "SELECT workspaces.id, '默认项目', 'default', 1, :created_at FROM workspaces "
            "WHERE workspaces.workspace_type = 'personal' AND NOT EXISTS ("
            "SELECT 1 FROM projects WHERE projects.workspace_id = workspaces.id AND projects.slug = 'default')"
        ), {"created_at": now})
        connection.execute(text(
            "UPDATE api_keys SET project_id = (SELECT projects.id FROM projects JOIN workspaces "
            "ON projects.workspace_id = workspaces.id WHERE workspaces.owner_account_id = api_keys.account_id "
            "AND workspaces.workspace_type = 'personal' AND projects.slug = 'default') WHERE project_id IS NULL"
        ))
        for table in ("usage_records", "account_balance_transactions", "payment_orders"):
            connection.execute(text(
                f"UPDATE {table} SET project_id = (SELECT projects.id FROM projects JOIN workspaces "
                f"ON projects.workspace_id = workspaces.id WHERE workspaces.owner_account_id = {table}.account_id "
                "AND workspaces.workspace_type = 'personal' AND projects.slug = 'default') WHERE project_id IS NULL"
            ))
            connection.execute(text(
                f"UPDATE {table} SET workspace_id = (SELECT projects.workspace_id FROM projects WHERE projects.id = {table}.project_id) "
                "WHERE workspace_id IS NULL"
            ))

        connection.execute(text(
            "INSERT INTO model_channels "
            "(model_config_id, name, provider_base_url, upstream_model, provider_api_key_env, priority, weight, "
            "active, status, health_source, credential_source, consecutive_failures, last_latency_ms, created_at) "
            "SELECT model_configs.id, 'Primary', model_configs.provider_base_url, model_configs.upstream_model, "
            "model_configs.provider_api_key_env, 100, 100, model_configs.active, 'unknown', 'unknown', 'environment', 0, 0, :created_at "
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

    remove_deprecated_provider_models()
    seed_builtin_models()
    seed_provider_catalogue()


def remove_deprecated_provider_models() -> None:
    """Remove retired catalogue candidates while preserving auditable history."""
    from .models import ModelChannel, ModelConfig, UsageRecord
    from .provider_presets import DEPRECATED_PROVIDER_MODEL_PUBLIC_NAMES

    with SessionLocal() as db:
        models = db.scalars(
            select(ModelConfig).where(ModelConfig.public_name.in_(DEPRECATED_PROVIDER_MODEL_PUBLIC_NAMES))
        ).all()
        for model in models:
            has_usage = db.scalar(select(UsageRecord.id).where(UsageRecord.model == model.public_name).limit(1))
            if has_usage:
                model.active = False
                continue
            db.execute(delete(ModelChannel).where(ModelChannel.model_config_id == model.id))
            db.delete(model)
        db.commit()


def seed_builtin_models() -> None:
    """Create a useful local trial catalogue without inventing production supply."""
    if not settings.mock_mode or not settings.seed_builtin_models:
        return
    from .builtin_models import BUILTIN_MODELS
    from .models import ModelChannel, ModelConfig, utcnow

    with SessionLocal() as db:
        existing_names = set(db.scalars(select(ModelConfig.public_name)).all())
        for builtin in BUILTIN_MODELS:
            if builtin.public_name in existing_names:
                continue
            model = ModelConfig(
                public_name=builtin.public_name,
                upstream_model=builtin.public_name,
                provider_base_url=settings.default_provider_base_url,
                input_price_micros_per_1k=builtin.input_price_micros_per_1k,
                output_price_micros_per_1k=builtin.output_price_micros_per_1k,
            )
            db.add(model)
            db.flush()
            db.add(ModelChannel(
                model_config_id=model.id,
                name="LokSystem built-in",
                provider_base_url=model.provider_base_url,
                upstream_model=model.upstream_model,
                priority=100,
                weight=100,
            ))
        if settings.mock_mode:
            for channel in db.scalars(select(ModelChannel).where(ModelChannel.status == "unknown")).all():
                channel.status = "healthy"
                channel.last_checked_at = utcnow()
        db.commit()


def seed_provider_catalogue() -> None:
    """Preload curated provider candidates so operators do not create models one by one."""
    if not settings.seed_provider_catalogue:
        return
    from .models import ModelChannel, ModelConfig
    from .provider_presets import PROVIDER_PRESETS

    with SessionLocal() as db:
        existing_models = {
            model.public_name: model
            for model in db.scalars(select(ModelConfig)).all()
        }
        for preset in PROVIDER_PRESETS:
            for preset_model in preset.models:
                existing = existing_models.get(preset_model.public_name)
                if existing:
                    current_metadata = {}
                    if existing.catalog_metadata_json:
                        try:
                            decoded_metadata = json.loads(existing.catalog_metadata_json)
                            current_metadata = decoded_metadata if isinstance(decoded_metadata, dict) else {}
                        except (TypeError, ValueError, json.JSONDecodeError):
                            current_metadata = {}
                    previous_pricing = {}
                    if existing.official_pricing_json:
                        try:
                            decoded_pricing = json.loads(existing.official_pricing_json)
                            previous_pricing = decoded_pricing if isinstance(decoded_pricing, dict) else {}
                        except (TypeError, ValueError, json.JSONDecodeError):
                            previous_pricing = {}
                    # Migrate the original DeepSeek USD-converted seed once. Do
                    # not overwrite prices that an operator has subsequently
                    # edited in the pricing workflow.
                    legacy_deepseek_pricing = (
                        current_metadata.get("provider") == "DeepSeek"
                        and previous_pricing.get("source_currency") == "USD"
                    )
                    if preset_model.catalog_metadata:
                        current_metadata.update(preset_model.catalog_metadata)
                        existing.catalog_metadata_json = json.dumps(current_metadata, ensure_ascii=False)
                    if preset_model.official_pricing:
                        existing.official_pricing_json = json.dumps(preset_model.official_pricing, ensure_ascii=False)
                    if (legacy_deepseek_pricing or existing.input_price_micros_per_1k <= 0) and preset_model.platform_input_price_micros_per_1k > 0:
                        existing.input_price_micros_per_1k = preset_model.platform_input_price_micros_per_1k
                    if (legacy_deepseek_pricing or existing.output_price_micros_per_1k <= 0) and preset_model.platform_output_price_micros_per_1k > 0:
                        existing.output_price_micros_per_1k = preset_model.platform_output_price_micros_per_1k
                    if existing.task_price_micros <= 0 and preset_model.platform_task_price_micros > 0:
                        existing.task_price_micros = preset_model.platform_task_price_micros
                    for channel in db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == existing.id)).all():
                        if legacy_deepseek_pricing or not channel.provider_input_cost_micros_per_1k:
                            channel.provider_input_cost_micros_per_1k = preset_model.platform_input_price_micros_per_1k
                        if legacy_deepseek_pricing or not channel.provider_output_cost_micros_per_1k:
                            channel.provider_output_cost_micros_per_1k = preset_model.platform_output_price_micros_per_1k
                        if not channel.provider_task_cost_micros and preset_model.provider_task_cost_micros > 0:
                            channel.provider_task_cost_micros = preset_model.provider_task_cost_micros
                    continue
                record = ModelConfig(
                    public_name=preset_model.public_name,
                    upstream_model=preset_model.model_id,
                    provider_base_url=preset.base_url,
                    provider_api_key_env=preset.api_key_env,
                    input_price_micros_per_1k=preset_model.platform_input_price_micros_per_1k,
                    output_price_micros_per_1k=preset_model.platform_output_price_micros_per_1k,
                    task_price_micros=preset_model.platform_task_price_micros,
                    catalog_metadata_json=json.dumps(preset_model.catalog_metadata, ensure_ascii=False),
                    official_pricing_json=json.dumps(preset_model.official_pricing, ensure_ascii=False) if preset_model.official_pricing else None,
                    active=False,
                )
                db.add(record)
                db.flush()
                db.add(ModelChannel(
                    model_config_id=record.id,
                    name=f"{preset.name} 主渠道",
                    provider_base_url=preset.base_url,
                    upstream_model=preset_model.model_id,
                    provider_api_key_env=preset.api_key_env,
                    provider_task_cost_micros=preset_model.provider_task_cost_micros or None,
                    active=False,
                ))
                existing_models[preset_model.public_name] = record
        db.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
