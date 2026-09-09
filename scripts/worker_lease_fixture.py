"""Internal-only fixture for run_worker_lease_drill.py; never mount in production."""
import json
import sys
import threading
import time
from pathlib import Path


def serve():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    events = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == '/events':
                with lock:
                    body = list(events)
            elif self.path == '/healthz':
                body = {'ok': True}
            elif self.path.startswith('/v1/videos/generations/'):
                with lock:
                    events.append({'time': time.time(), 'client': self.client_address[0], 'path': self.path})
                    first = len(events) == 1
                    Path('/tmp/events.json').write_text(json.dumps(events))
                if first:
                    # The caller must kill the Worker while this request is pending.
                    threading.Event().wait(120)
                body = {'status': 'completed', 'id': 'lease-provider-task', 'data': [{'url': 'https://fixture.invalid/result.mp4'}]}
            else:
                self.send_error(404)
                return
            encoded = json.dumps(body).encode()
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    ThreadingHTTPServer(('0.0.0.0', 4011), Handler).serve_forever()


def database_action(action, tag, provider):
    import uuid
    from sqlalchemy import select
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import AccountBalanceTransaction, ApiKey, BillingAccount, GenerationTask, ModelChannel, ModelConfig, UsageRecord
    from app.services import reserve_balance

    settings = get_settings()
    assert settings.environment == 'staging', 'fixture requires staging'
    assert not settings.mock_mode, 'fixture requires real HTTP calls'
    name = 'sigkill-drill-' + tag
    with SessionLocal() as db:
        if action == 'seed':
            account = BillingAccount(external_user_id=name, name=name, balance_micros=1_000_000)
            db.add(account)
            db.flush()
            key = ApiKey(account_id=account.id, billing_account_id=account.id, name=name,
                         key_prefix='drill-unusable', key_hash=uuid.uuid4().hex + uuid.uuid4().hex)
            model = ModelConfig(public_name=name, upstream_model=name, provider_base_url=provider,
                                task_price_micros=100_000, catalog_metadata_json=json.dumps({'api_type': 'video_generations'}))
            db.add_all([key, model])
            db.flush()
            channel = ModelChannel(model_config_id=model.id, name=name, provider_base_url=provider,
                                   upstream_model=name, provider_api_key_env='DRILL_UNUSED_KEY', status='healthy')
            db.add(channel)
            db.flush()
            db.add(AccountBalanceTransaction(account_id=account.id, api_key_id=key.id, amount_micros=1_000_000,
                                            transaction_type='credit', reference_id=name + '-credit', description='isolated drill funding'))
            task = GenerationTask(task_id=name, request_id=name, trace_id=name, account_id=account.id,
                                  api_key_id=key.id, model_config_id=model.id, provider_channel_id=channel.id,
                                  provider_task_id='lease-provider-task', task_type='video_generations',
                                  status='queued', reserved_micros=100_000)
            db.add(task)
            db.commit()
            reserve_balance(db, account, key, 100_000, name)
            task.status = 'processing'
            db.commit()
        task = db.scalar(select(GenerationTask).where(GenerationTask.task_id == name))
        if not task:
            print(json.dumps({'absent': True}))
            return
        account = db.get(BillingAccount, task.account_id)
        key = db.get(ApiKey, task.api_key_id)
        if action == 'cleanup':
            # Retain billing evidence but prevent abandoned fixtures being scheduled.
            task.status = task.status if task.status in {'completed', 'failed'} else 'cancelled'
            key.active = False
            account.active = False
            db.get(ModelConfig, task.model_config_id).active = False
            db.get(ModelChannel, task.provider_channel_id).active = False
            db.commit()
        usage = db.scalars(select(UsageRecord).where(UsageRecord.request_id == name)).all()
        ledger = db.scalars(select(AccountBalanceTransaction).where(AccountBalanceTransaction.account_id == account.id).order_by(AccountBalanceTransaction.id)).all()
        print(json.dumps({'task_id': name, 'account_id': account.id, 'status': task.status,
                          'attempt_count': task.attempt_count, 'claimed_at': task.worker_claimed_at.isoformat() if task.worker_claimed_at else None,
                          'claim_token': task.worker_claim_token, 'settled_at': task.settled_at.isoformat() if task.settled_at else None,
                          'balance': account.balance_micros, 'spent': key.spent_micros,
                          'usage': [{'status': u.status, 'amount': u.amount_micros} for u in usage],
                          'ledger': [{'type': row.transaction_type, 'amount': row.amount_micros, 'reference': row.reference_id} for row in ledger],
                          'lease_seconds': max(30, settings.task_worker_interval_seconds * 3),
                          'interval_seconds': settings.task_worker_interval_seconds}))


if __name__ == '__main__':
    if sys.argv[1] == 'serve':
        serve()
    else:
        database_action(*sys.argv[1:4])
