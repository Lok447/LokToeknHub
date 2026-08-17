import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_audit_event
from .builtin_models import model_metadata
from .config import get_settings
from .db import get_db
from .guardrails import rate_limiter
from .models import AccountBalanceTransaction, ApiKey, BillingAccount, ModelConfig, PaymentOrder, RedemptionClaim, RedemptionCode, UsageRecord, utcnow
from .payment_providers import payment_providers, require_available_provider
from .schemas import ActiveUpdate, PaymentOrderCreate, PortalApiKeyCreate, RedemptionCodeRedeem, TrialLinkCreate
from .security import create_key, create_trial_token, hash_key, require_admin, require_trial_account


router = APIRouter()


def portal_account(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> BillingAccount:
    return require_trial_account(authorization, db)


def order_data(order: PaymentOrder) -> dict[str, object]:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "amount_micros": order.amount_micros,
        "provider": order.provider,
        "provider_order_id": order.provider_order_id,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
    }


def scoped_usage_query(
    account_id: int,
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    status: str | None = None,
    request_id: str | None = None,
):
    query = select(UsageRecord).where(UsageRecord.account_id == account_id)
    if model:
        query = query.where(UsageRecord.model == model)
    if api_key_id:
        query = query.where(UsageRecord.api_key_id == api_key_id)
    if from_at:
        query = query.where(UsageRecord.created_at >= from_at)
    if to_at:
        query = query.where(UsageRecord.created_at <= to_at)
    if status:
        query = query.where(UsageRecord.status == status)
    if request_id:
        query = query.where(UsageRecord.request_id.contains(request_id))
    return query


def usage_record_data(record: UsageRecord, key_name: str) -> dict[str, object]:
    return {
        "id": record.id,
        "request_id": record.request_id,
        "trace_id": record.trace_id,
        "api_key_id": record.api_key_id,
        "api_key_name": key_name,
        "model": record.model,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "total_tokens": record.total_tokens,
        "amount_micros": record.amount_micros,
        "status": record.status,
        "latency_ms": record.latency_ms,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat(),
    }


def csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@router.post("/admin/trial-links", dependencies=[Depends(require_admin)])
def create_trial_link(payload: TrialLinkCreate, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    account = db.get(BillingAccount, payload.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=404, detail="active account not found")
    token, expires_at = create_trial_token(account, payload.expires_in_seconds)
    configured_base = get_settings().public_base_url.rstrip("/")
    base_url = configured_base or str(request.base_url).rstrip("/")
    return {
        "account_id": account.id,
        "access_token": token,
        "portal_url": f"{base_url}/portal#access_token={quote(token, safe='')}",
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


@router.get("/portal/profile")
def profile(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    return {
        "id": account.id,
        "external_user_id": account.external_user_id,
        "name": account.name,
        "balance_micros": account.balance_micros,
        "api_key_count": db.scalar(select(func.count(ApiKey.id)).where(ApiKey.account_id == account.id, ApiKey.active.is_(True))) or 0,
        "request_count": db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.account_id == account.id)) or 0,
        "created_at": account.created_at.isoformat(),
    }


@router.get("/portal/api-keys")
def list_api_keys(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    keys = db.scalars(select(ApiKey).where(ApiKey.account_id == account.id).order_by(ApiKey.id.desc())).all()
    return {"data": [{
        "id": item.id,
        "name": item.name,
        "key_prefix": item.key_prefix,
        "active": item.active,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "spending_limit_micros": item.spending_limit_micros,
        "spent_micros": item.spent_micros,
        "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
        "created_at": item.created_at.isoformat(),
    } for item in keys]}


@router.post("/portal/api-keys")
def create_api_key(payload: PortalApiKeyCreate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("portal-key-create", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    exact_expiry = payload.expires_at
    if exact_expiry and exact_expiry.tzinfo is None:
        exact_expiry = exact_expiry.replace(tzinfo=timezone.utc)
    if exact_expiry and exact_expiry <= utcnow():
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    raw_key = create_key()
    record = ApiKey(
        account_id=account.id,
        name=payload.name,
        key_prefix=raw_key[:12],
        key_hash=hash_key(raw_key),
        expires_at=exact_expiry or (utcnow() + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None),
        spending_limit_micros=payload.spending_limit_micros,
    )
    db.add(record)
    db.flush()
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="api_key.created", target_type="api_key", target_id=record.id, details={"name": record.name})
    db.commit()
    db.refresh(record)
    return {"id": record.id, "name": record.name, "key": raw_key, "key_prefix": record.key_prefix}


@router.patch("/portal/api-keys/{api_key_id}")
def update_api_key(api_key_id: int, payload: ActiveUpdate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    api_key = db.scalar(select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.account_id == account.id))
    if not api_key:
        raise HTTPException(status_code=404, detail="api key not found")
    api_key.active = payload.active
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="api_key.status_updated", target_type="api_key", target_id=api_key.id, details={"active": api_key.active})
    db.commit()
    return {"id": api_key.id, "active": api_key.active}


@router.get("/portal/models")
def list_models(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    del account
    models = db.scalars(select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.public_name)).all()
    return {"data": [{
        "id": item.id,
        "public_name": item.public_name,
        "input_price_micros_per_1k": item.input_price_micros_per_1k,
        "output_price_micros_per_1k": item.output_price_micros_per_1k,
        **model_metadata(item.public_name),
    } for item in models]}


@router.get("/portal/usage")
def usage_summary(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
) -> dict[str, object]:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    count, inputs, outputs, total, amount, average_latency, success_count = db.execute(select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        func.coalesce(func.avg(UsageRecord.latency_ms), 0),
        func.count(UsageRecord.id).filter(UsageRecord.status == "success"),
    ).where(UsageRecord.id.in_(record_ids))).one()
    failed_count = count - success_count
    return {
        "request_count": count,
        "input_tokens": inputs,
        "output_tokens": outputs,
        "total_tokens": total,
        "amount_micros": amount,
        "average_latency_ms": round(float(average_latency), 1),
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": round(success_count / count * 100, 1) if count else 0,
    }


@router.get("/portal/usage/analytics")
def usage_analytics(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
    granularity: Annotated[str, Query(pattern="^(hour|day)$")] = "day",
) -> dict[str, object]:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    bucket_format = "%Y-%m-%dT%H:00:00" if granularity == "hour" else "%Y-%m-%d"
    bucket = func.strftime(bucket_format, UsageRecord.created_at).label("bucket")
    trend_rows = db.execute(
        select(
            bucket,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
            func.coalesce(func.avg(UsageRecord.latency_ms), 0),
        )
        .where(UsageRecord.id.in_(record_ids))
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    model_rows = db.execute(
        select(
            UsageRecord.model,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .where(UsageRecord.id.in_(record_ids))
        .group_by(UsageRecord.model)
        .order_by(func.sum(UsageRecord.total_tokens).desc(), func.count(UsageRecord.id).desc())
    ).all()
    key_rows = db.execute(
        select(
            ApiKey.id,
            ApiKey.name,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .join(UsageRecord, UsageRecord.api_key_id == ApiKey.id)
        .where(UsageRecord.id.in_(record_ids))
        .group_by(ApiKey.id, ApiKey.name)
        .order_by(func.sum(UsageRecord.total_tokens).desc(), func.count(UsageRecord.id).desc())
    ).all()
    return {
        "granularity": granularity,
        "trend": [
            {
                "bucket": item_bucket,
                "request_count": count,
                "input_tokens": inputs,
                "output_tokens": outputs,
                "total_tokens": total,
                "amount_micros": amount,
                "average_latency_ms": round(float(latency), 1),
            }
            for item_bucket, count, inputs, outputs, total, amount, latency in trend_rows
        ],
        "model_distribution": [
            {"name": item_model, "request_count": count, "total_tokens": total, "amount_micros": amount}
            for item_model, count, total, amount in model_rows
        ],
        "key_distribution": [
            {"id": key_id, "name": key_name, "request_count": count, "total_tokens": total, "amount_micros": amount}
            for key_id, key_name, count, total, amount in key_rows
        ],
    }


@router.get("/portal/dashboard")
def dashboard(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    days: Annotated[int, Query(ge=7, le=90)] = 7,
    model: str | None = None,
    api_key_id: int | None = None,
) -> dict[str, object]:
    if days not in {7, 30, 90}:
        raise HTTPException(status_code=422, detail="days must be one of 7, 30 or 90")
    today = utcnow().date()
    period_start = today - timedelta(days=days - 1)

    base_filters = [UsageRecord.account_id == account.id]
    if model:
        base_filters.append(UsageRecord.model == model)
    if api_key_id:
        base_filters.append(UsageRecord.api_key_id == api_key_id)
    period_filters = [*base_filters, UsageRecord.created_at >= datetime.combine(period_start, datetime.min.time(), timezone.utc)]

    day_column = func.date(UsageRecord.created_at).label("day")
    daily_rows = db.execute(
        select(
            day_column,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .where(*period_filters)
        .group_by(day_column)
        .order_by(day_column)
    ).all()
    daily_map = {
        str(day): {"request_count": count, "total_tokens": tokens, "amount_micros": amount}
        for day, count, tokens, amount in daily_rows
    }
    daily = []
    for offset in range(days):
        day = period_start + timedelta(days=offset)
        values = daily_map.get(day.isoformat(), {"request_count": 0, "total_tokens": 0, "amount_micros": 0})
        daily.append({"date": day.isoformat(), **values})

    ranking_rows = db.execute(
        select(
            UsageRecord.model,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.sum(UsageRecord.amount_micros), 0),
        )
        .where(*period_filters)
        .group_by(UsageRecord.model)
        .order_by(func.sum(UsageRecord.amount_micros).desc(), func.count(UsageRecord.id).desc())
        .limit(5)
    ).all()

    activity_start = today - timedelta(days=364)
    activity_filters = [
        *base_filters,
        UsageRecord.created_at >= datetime.combine(activity_start, datetime.min.time(), timezone.utc),
    ]
    activity_rows = db.execute(
        select(day_column, func.count(UsageRecord.id), func.coalesce(func.sum(UsageRecord.amount_micros), 0))
        .where(*activity_filters)
        .group_by(day_column)
        .order_by(day_column)
    ).all()
    activity = [{"date": str(day), "request_count": count, "amount_micros": amount} for day, count, amount in activity_rows]
    activity_dates = sorted(datetime.fromisoformat(item["date"]).date() for item in activity)
    longest_streak = 0
    current_streak = 0
    previous_day = None
    for activity_day in activity_dates:
        current_streak = current_streak + 1 if previous_day and activity_day == previous_day + timedelta(days=1) else 1
        longest_streak = max(longest_streak, current_streak)
        previous_day = activity_day

    total_amount = db.scalar(select(func.coalesce(func.sum(UsageRecord.amount_micros), 0)).where(*base_filters)) or 0
    today_amount = sum(item["amount_micros"] for item in activity if item["date"] == today.isoformat())
    week_start = today - timedelta(days=6)
    week_amount = sum(
        item["amount_micros"]
        for item in activity
        if datetime.fromisoformat(item["date"]).date() >= week_start
    )
    period_request_count = sum(item["request_count"] for item in daily)
    period_total_tokens = sum(item["total_tokens"] for item in daily)
    period_amount = sum(item["amount_micros"] for item in daily)

    return {
        "period": {
            "days": days,
            "from": period_start.isoformat(),
            "to": today.isoformat(),
            "request_count": period_request_count,
            "total_tokens": period_total_tokens,
            "amount_micros": period_amount,
        },
        "daily": daily,
        "model_ranking": [
            {"model": item_model, "request_count": count, "total_tokens": tokens, "amount_micros": amount}
            for item_model, count, tokens, amount in ranking_rows
        ],
        "activity": activity,
        "activity_summary": {
            "longest_streak_days": longest_streak,
            "today_amount_micros": today_amount,
            "week_amount_micros": week_amount,
            "total_amount_micros": total_amount,
        },
    }


@router.get("/portal/usage/records")
def usage_records(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=100)] = 20,
) -> dict[str, object]:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    total = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.id.in_(record_ids))) or 0
    query = select(UsageRecord, ApiKey.name).join(ApiKey, ApiKey.id == UsageRecord.api_key_id).where(UsageRecord.id.in_(record_ids))
    rows = db.execute(
        query.order_by(UsageRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "data": [usage_record_data(record, key_name) for record, key_name in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/portal/usage/records/{request_id}")
def usage_record_detail(
    request_id: str,
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    row = db.execute(
        select(UsageRecord, ApiKey.name)
        .join(ApiKey, ApiKey.id == UsageRecord.api_key_id)
        .where(UsageRecord.account_id == account.id, UsageRecord.request_id == request_id)
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="request record not found")
    return usage_record_data(row[0], row[1])


@router.get("/portal/usage/export")
def export_usage(
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
    model: str | None = None,
    api_key_id: int | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    status: str | None = None,
    request_id: str | None = None,
) -> Response:
    record_ids = scoped_usage_query(
        account.id, model, api_key_id, from_at, to_at, status, request_id
    ).with_only_columns(UsageRecord.id)
    rows = db.execute(
        select(UsageRecord, ApiKey.name)
        .join(ApiKey, ApiKey.id == UsageRecord.api_key_id)
        .where(UsageRecord.id.in_(record_ids))
        .order_by(UsageRecord.id.desc())
        .limit(5000)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["created_at", "request_id", "trace_id", "api_key", "model", "input_tokens", "output_tokens", "total_tokens", "latency_ms", "amount_micros", "status", "error_message"])
    for record, key_name in rows:
        writer.writerow([
            record.created_at.isoformat(), csv_safe(record.request_id), csv_safe(record.trace_id), csv_safe(key_name), csv_safe(record.model),
            record.input_tokens, record.output_tokens, record.total_tokens, record.latency_ms,
            record.amount_micros, csv_safe(record.status), csv_safe(record.error_message or ""),
        ])
    filename = f"token-usage-{datetime.now(timezone.utc).date().isoformat()}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/portal/transactions")
def transactions(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    items = db.scalars(select(AccountBalanceTransaction).where(
        AccountBalanceTransaction.account_id == account.id
    ).order_by(AccountBalanceTransaction.id.desc()).limit(100)).all()
    return {"data": [{
        "id": item.id,
        "amount_micros": item.amount_micros,
        "type": item.transaction_type,
        "reference_id": item.reference_id,
        "description": item.description,
        "created_at": item.created_at.isoformat(),
    } for item in items]}


@router.get("/portal/balance-summary")
def balance_summary(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    total_credit = db.scalar(select(func.coalesce(func.sum(AccountBalanceTransaction.amount_micros), 0)).where(
        AccountBalanceTransaction.account_id == account.id,
        AccountBalanceTransaction.transaction_type.in_(("topup", "payment", "redemption")),
    )) or 0
    total_consumed = db.scalar(select(func.coalesce(func.sum(UsageRecord.amount_micros), 0)).where(
        UsageRecord.account_id == account.id,
        UsageRecord.status == "success",
    )) or 0
    transaction_count = db.scalar(select(func.count(AccountBalanceTransaction.id)).where(
        AccountBalanceTransaction.account_id == account.id
    )) or 0
    return {
        "balance_micros": account.balance_micros,
        "total_credit_micros": total_credit,
        "total_consumed_micros": total_consumed,
        "transaction_count": transaction_count,
    }


@router.get("/portal/payment-providers")
def list_payment_providers(account: BillingAccount = Depends(portal_account)) -> dict[str, object]:
    del account
    return {"data": [provider.to_dict() for provider in payment_providers()]}


@router.get("/portal/payment-orders")
def list_payment_orders(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    orders = db.scalars(select(PaymentOrder).where(PaymentOrder.account_id == account.id).order_by(PaymentOrder.id.desc()).limit(100)).all()
    return {"data": [order_data(item) for item in orders]}


@router.post("/portal/payment-orders")
def create_payment_order(payload: PaymentOrderCreate, account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("portal-payment-create", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    if payload.account_id != account.id:
        raise HTTPException(status_code=403, detail="payment order account mismatch")
    try:
        require_available_provider(payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    order = PaymentOrder(
        order_no="pay_" + uuid.uuid4().hex,
        account_id=account.id,
        amount_micros=payload.amount_micros,
        provider=payload.provider,
    )
    db.add(order)
    db.flush()
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="payment_order.created", target_type="payment_order", target_id=order.id, details={"order_no": order.order_no, "amount_micros": order.amount_micros, "provider": order.provider})
    db.commit()
    db.refresh(order)
    return order_data(order)


def redemption_code_is_expired(code: RedemptionCode) -> bool:
    if not code.expires_at:
        return False
    now = utcnow()
    if code.expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return code.expires_at <= now


@router.post("/portal/redemption-codes/redeem")
def redeem_code(
    payload: RedemptionCodeRedeem,
    account: BillingAccount = Depends(portal_account),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings = get_settings()
    rate_limiter.check("portal-redemption", str(account.id), settings.portal_rate_limit_requests, settings.portal_rate_limit_window_seconds)
    raw_code = payload.code.strip()
    code = db.scalar(select(RedemptionCode).where(RedemptionCode.code_hash == hash_key(raw_code)).with_for_update())
    if not code:
        raise HTTPException(status_code=404, detail="redemption code not found")
    existing_claim = db.scalar(select(RedemptionClaim).where(
        RedemptionClaim.redemption_code_id == code.id,
        RedemptionClaim.account_id == account.id,
    ))
    if existing_claim:
        raise HTTPException(status_code=409, detail="redemption code already used by this account")
    if not code.active or redemption_code_is_expired(code) or code.redeemed_count >= code.max_redemptions:
        raise HTTPException(status_code=422, detail="redemption code is unavailable")
    locked_account = db.scalar(select(BillingAccount).where(BillingAccount.id == account.id).with_for_update())
    if not locked_account or not locked_account.active:
        raise HTTPException(status_code=403, detail="billing account is inactive")
    reference_id = f"redemption:{code.id}:{account.id}"
    claim = RedemptionClaim(
        redemption_code_id=code.id,
        account_id=account.id,
        amount_micros=code.amount_micros,
        reference_id=reference_id,
    )
    locked_account.balance_micros += code.amount_micros
    code.redeemed_count += 1
    db.add(claim)
    db.add(AccountBalanceTransaction(
        account_id=account.id,
        api_key_id=None,
        amount_micros=code.amount_micros,
        transaction_type="redemption",
        reference_id=reference_id,
        description=f"redeemed benefit: {code.label}",
    ))
    record_audit_event(db, actor_type="portal", actor_id=account.external_user_id, action="redemption_code.claimed", target_type="redemption_code", target_id=code.id, details={"amount_micros": code.amount_micros})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="redemption code could not be claimed") from exc
    db.refresh(claim)
    return {
        "id": claim.id,
        "label": code.label,
        "amount_micros": claim.amount_micros,
        "balance_micros": locked_account.balance_micros,
        "redeemed_at": claim.redeemed_at.isoformat(),
    }


@router.get("/portal/redemptions")
def list_redemptions(account: BillingAccount = Depends(portal_account), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.execute(
        select(RedemptionClaim, RedemptionCode.label)
        .join(RedemptionCode, RedemptionCode.id == RedemptionClaim.redemption_code_id)
        .where(RedemptionClaim.account_id == account.id)
        .order_by(RedemptionClaim.id.desc())
        .limit(100)
    ).all()
    return {"data": [{
        "id": claim.id,
        "label": label,
        "amount_micros": claim.amount_micros,
        "redeemed_at": claim.redeemed_at.isoformat(),
    } for claim, label in rows]}
