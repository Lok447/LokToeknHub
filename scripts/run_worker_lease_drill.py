"""SIGKILL the sole lease holder in the local loktoken-preprod Compose stack."""
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'scripts' / 'worker_lease_fixture.py'
TOKEN1 = 'loktoken-preprod-token-1'
TOKEN2 = 'loktoken-preprod-token2-1'


def docker(*args, stdin=None):
    result = subprocess.run(['docker', *args], input=stdin, capture_output=True, text=True, timeout=40, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f'docker {args[0]} failed: {result.stderr.strip()[:400]}')
    return result.stdout.strip()


def inspect(name):
    # Never persist environment variables or provider credentials from inspect.
    return json.loads(docker('inspect', name))[0]


def wait_until(fn, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError('drill condition timed out')


def ready(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/readyz', timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def main():
    tag = uuid.uuid4().hex[:12]
    provider = 'loktoken-sigkill-' + tag
    base = f'http://{provider}:4011/v1'
    report = {'run_id': tag, 'started_at': datetime.now(timezone.utc).isoformat(), 'checks': [], 'cleanup_errors': []}
    originals = {name: inspect(name) for name in (TOKEN1, TOKEN2)}
    network = 'loktoken-preprod_default'
    for state in originals.values():
        assert state['Config']['Labels']['com.docker.compose.project'] == 'loktoken-preprod'
        assert network in state['NetworkSettings']['Networks']
        assert state['State']['Running'], 'start both preprod instances before drilling'
    fixture = FIXTURE.read_text(encoding='utf-8')
    created = False

    def db(action, container):
        return json.loads(docker('exec', '-i', container, 'python', '-', action, tag, base, stdin=fixture))

    def events():
        code = "from pathlib import Path; p=Path('/tmp/events.json'); print(p.read_text() if p.exists() else '[]')"
        return json.loads(docker('exec', provider, 'python', '-c', code))

    def check(name, passed, detail=None):
        report['checks'].append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(f'{name}: {bool(passed)}', flush=True)
        if not passed:
            raise AssertionError(name)

    try:
        docker('stop', '-t', '10', TOKEN2)
        docker('update', '--restart=no', TOKEN1)
        docker('create', '--name', provider, '--network', network, '--restart=no',
               originals[TOKEN1]['Image'], 'python', '/tmp/fixture.py', 'serve')
        created = True
        docker('cp', str(FIXTURE), provider + ':/tmp/fixture.py')
        docker('start', provider)
        docker('exec', provider, 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4011/healthz', timeout=2)")
        report['seed'] = db('seed', TOKEN1)
        first = wait_until(events, 15)
        claimed = db('snapshot', TOKEN1)
        token1_ip = originals[TOKEN1]['NetworkSettings']['Networks'][network]['IPAddress']
        check('sole_holder_request_blocked', len(first) == 1 and first[0]['client'] == token1_ip
              and claimed['status'] == 'processing' and bool(claimed['claim_token']), claimed)
        report['claimed'] = claimed
        docker('kill', '--signal=KILL', TOKEN1)
        killed = inspect(TOKEN1)['State']
        check('holder_killed_exit_137', killed['ExitCode'] == 137 and not killed['Running'])
        report['killed_at'] = killed['FinishedAt']
        docker('start', TOKEN2)
        token2_ip = inspect(TOKEN2)['NetworkSettings']['Networks'][network]['IPAddress']
        expiry = datetime.fromisoformat(claimed['claimed_at']).timestamp() + claimed['lease_seconds']
        before = db('snapshot', TOKEN2)
        check('lease_retained_after_crash', before['claim_token'] == claimed['claim_token'] and before['status'] == 'processing')
        wait_until(lambda: ready(18001), 15)
        check('standby_ready_before_expiry', time.time() < expiry)
        while time.time() < expiry - 1:
            check_events = events()
            if len(check_events) != 1:
                check('no_takeover_before_expiry', False, check_events)
            time.sleep(0.5)
        check('no_takeover_before_expiry', len(events()) == 1)
        completed = wait_until(lambda: (s if (s := db('snapshot', TOKEN2))['settled_at'] and not s['claim_token'] else None), 25)
        received = events()
        report['events'] = received
        report['final'] = completed
        check('standby_takes_over_after_expiry', len(received) == 2 and received[1]['client'] == token2_ip
              and received[1]['time'] >= expiry, {'seconds_after_expiry': received[-1]['time'] - expiry})
        check('completed_once', completed['status'] == 'completed' and completed['usage'] == [{'status': 'success', 'amount': 100_000}])
        check('balanced_single_charge', completed['balance'] == 900_000 and completed['spent'] == 100_000
              and [row['type'] for row in completed['ledger']] == ['credit', 'reservation'])
        # Persisted attempts must include the request abandoned by SIGKILL.
        check('crashed_attempt_persisted', completed['attempt_count'] == 2, completed['attempt_count'])
        docker('start', TOKEN1)
        wait_until(lambda: ready(18000), 30)
        time.sleep(report['seed']['interval_seconds'] * 2 + 1)
        restored = db('snapshot', TOKEN2)
        check('restart_does_not_double_settle', restored == completed and len(events()) == 2)
    except Exception as exc:
        report['error'] = str(exc)
        print('drill failed: ' + str(exc), flush=True)
    finally:
        for name in (TOKEN1, TOKEN2):
            try:
                docker('start', name)
                policy = originals[name]['HostConfig']['RestartPolicy']
                value = policy['Name']
                if value == 'on-failure' and policy['MaximumRetryCount']:
                    value += ':' + str(policy['MaximumRetryCount'])
                docker('update', '--restart=' + value, name)
            except Exception as exc:
                report['cleanup_errors'].append(str(exc))
        try:
            report['fixture_cleanup'] = db('cleanup', TOKEN2)
        except Exception as exc:
            report['cleanup_errors'].append(str(exc))
        if created:
            try:
                docker('rm', '-f', provider)
            except Exception as exc:
                report['cleanup_errors'].append(str(exc))
        for port in (18000, 18001):
            try:
                wait_until(lambda: ready(port), 30)
            except Exception as exc:
                report['cleanup_errors'].append(f'{port}: {exc}')
        report['passed'] = not report.get('error') and not report['cleanup_errors']
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        path = ROOT / 'runtime-logs' / f'worker-sigkill-{tag}.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(str(path), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
