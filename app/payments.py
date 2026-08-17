from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_audit_event
from .models import AccountBalanceTransaction, BillingAccount, PaymentOrder


def mark_order_paid(
    db: Session,
    order: PaymentOrder,
    provider_order_id: str | None,
    audit_event: dict[str, object] | None = None,
) -> PaymentOrder:
    locked_order = db.scalar(select(PaymentOrder).where(PaymentOrder.id == order.id).with_for_update())
    if not locked_order:
        raise ValueError("payment order not found")
    if provider_order_id:
        provider_match = db.scalar(select(PaymentOrder).where(PaymentOrder.provider_order_id == provider_order_id))
        if provider_match and provider_match.id != locked_order.id:
            raise ValueError("provider order id already belongs to another payment order")
    if locked_order.status == "paid":
        return locked_order
    if locked_order.status != "pending":
        raise ValueError(f"cannot pay an order in {locked_order.status} status")

    account = db.scalar(select(BillingAccount).where(BillingAccount.id == locked_order.account_id).with_for_update())
    if not account or not account.active:
        raise ValueError("billing account is inactive")

    reference_id = f"payment:{locked_order.order_no}"
    existing = db.scalar(select(AccountBalanceTransaction).where(AccountBalanceTransaction.reference_id == reference_id))
    if not existing:
        account.balance_micros += locked_order.amount_micros
        db.add(AccountBalanceTransaction(
            account_id=account.id,
            api_key_id=None,
            amount_micros=locked_order.amount_micros,
            transaction_type="payment",
            reference_id=reference_id,
            description=f"payment order {locked_order.order_no}",
        ))

    locked_order.status = "paid"
    locked_order.provider_order_id = provider_order_id or locked_order.provider_order_id
    locked_order.paid_at = locked_order.paid_at or datetime.now(timezone.utc)
    if audit_event:
        record_audit_event(db, **audit_event)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        completed = db.get(PaymentOrder, order.id)
        if completed and completed.status == "paid":
            return completed
        raise ValueError("payment confirmation conflicts with an existing transaction") from exc
    db.refresh(locked_order)
    return locked_order


def refund_order(db: Session, order: PaymentOrder, audit_event: dict[str, object] | None = None) -> PaymentOrder:
    locked_order = db.scalar(select(PaymentOrder).where(PaymentOrder.id == order.id).with_for_update())
    if not locked_order:
        raise ValueError("payment order not found")
    if locked_order.status == "refunded":
        return locked_order
    if locked_order.status != "paid":
        raise ValueError("only paid orders can be refunded")

    account = db.scalar(select(BillingAccount).where(BillingAccount.id == locked_order.account_id).with_for_update())
    if not account or account.balance_micros < locked_order.amount_micros:
        raise ValueError("account balance is insufficient for a full refund")

    reference_id = f"refund:{locked_order.order_no}"
    existing = db.scalar(select(AccountBalanceTransaction).where(AccountBalanceTransaction.reference_id == reference_id))
    if not existing:
        account.balance_micros -= locked_order.amount_micros
        db.add(AccountBalanceTransaction(
            account_id=account.id,
            api_key_id=None,
            amount_micros=-locked_order.amount_micros,
            transaction_type="refund",
            reference_id=reference_id,
            description=f"refund payment order {locked_order.order_no}",
        ))

    locked_order.status = "refunded"
    locked_order.refunded_at = locked_order.refunded_at or datetime.now(timezone.utc)
    if audit_event:
        record_audit_event(db, **audit_event)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        completed = db.get(PaymentOrder, order.id)
        if completed and completed.status == "refunded":
            return completed
        raise ValueError("refund conflicts with an existing transaction") from exc
    db.refresh(locked_order)
    return locked_order
