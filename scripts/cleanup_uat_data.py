"""Remove explicitly identified local UAT/Smoke data without touching model setup or audit history."""

from __future__ import annotations

import argparse

from sqlalchemy import or_, select

from app.db import SessionLocal, init_db
from app.models import (
    AccountBalanceTransaction,
    ApiKey,
    BillingAccount,
    ExternalIdentity,
    Organization,
    OrganizationMember,
    PaymentOrder,
    PasswordResetChallenge,
    Project,
    RedemptionClaim,
    SecurityContactChallenge,
    SecurityNotification,
    UsageRecord,
    Workspace,
)


def synthetic_accounts(db):
    return db.scalars(
        select(BillingAccount).where(
            or_(
                BillingAccount.external_user_id.like("legacy-key-%"),
                BillingAccount.external_user_id == "lok-smoke-account",
                BillingAccount.external_user_id.like("loksystem-system_default%"),
                BillingAccount.external_user_id.like("real-gate-%"),
                BillingAccount.external_user_id.like("user-e0803f9fc7c54acba3f7%"),
                BillingAccount.external_user_id.like("user-4522f250ac2842ab9d1b%"),
                BillingAccount.name.in_(["Real DeepSeek Gate", "浏览器 UAT 用户", "test"]),
                BillingAccount.name.ilike("%smoke%"),
                BillingAccount.login_id.in_(["test"]),
                BillingAccount.login_id.like("uat-%"),
            )
        )
    ).all()


def remove_account_graph(db, account_ids: set[int]) -> dict[str, int]:
    if not account_ids:
        return {}
    counts: dict[str, int] = {}

    def delete(model, criterion):
        query = db.query(model).filter(criterion)
        count = query.delete(synchronize_session=False)
        counts[model.__tablename__] = count

    delete(UsageRecord, UsageRecord.account_id.in_(account_ids))
    delete(AccountBalanceTransaction, AccountBalanceTransaction.account_id.in_(account_ids))
    delete(PaymentOrder, PaymentOrder.account_id.in_(account_ids))
    delete(RedemptionClaim, RedemptionClaim.account_id.in_(account_ids))
    delete(SecurityNotification, SecurityNotification.account_id.in_(account_ids))
    delete(SecurityContactChallenge, SecurityContactChallenge.account_id.in_(account_ids))
    delete(PasswordResetChallenge, PasswordResetChallenge.account_id.in_(account_ids))
    delete(ExternalIdentity, ExternalIdentity.account_id.in_(account_ids))
    delete(ApiKey, ApiKey.account_id.in_(account_ids))
    delete(OrganizationMember, OrganizationMember.account_id.in_(account_ids))
    delete(Project, Project.workspace_id.in_(select(Workspace.id).where(Workspace.owner_account_id.in_(account_ids))))
    delete(Workspace, Workspace.owner_account_id)
    delete(Organization, Organization.owner_account_id)
    count = db.query(BillingAccount).filter(BillingAccount.id.in_(account_ids)).delete(synchronize_session=False)
    counts[BillingAccount.__tablename__] = count
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform deletion; without it only preview matches")
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        accounts = synthetic_accounts(db)
        print("Matched accounts:")
        for account in accounts:
            print(f"- {account.id}: {account.name} ({account.external_user_id})")
        if not args.execute:
            print("Preview only. Re-run with --execute to remove these accounts and dependent UAT data.")
            return
        counts = remove_account_graph(db, {account.id for account in accounts})
        db.commit()
        print("Deleted rows:")
        for table, count in counts.items():
            print(f"- {table}: {count}")


if __name__ == "__main__":
    main()
