"""Measure isolated API/collector processes; never mutate LXD resources.

Run with backend/.venv/bin/python scripts/measure_runtime.py --seconds 60 --output docs/runtime-verified.json.
CPU is elapsed process-tree CPU / wall time, where 100% is one logical CPU.
Runtime databases and credentials stay in a temporary directory.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

from hsm.db import connect, migrate
from hsm.lxd.capacity import CapacityService
from hsm.lxd.discovery import LxdDiscovery
from pylxd import Client
from hsm.collector import Collector
from hsm.auth.sessions import SessionService
from hsm.app import create_app
from hsm.config import load_settings
import falcon.testing

ROOT = Path(__file__).resolve().parents[1]


def process_tree(pid):
    ids = [pid]
    for current in ids:
        try:
            ids.extend(map(int, Path(f'/proc/{current}/task/{current}/children').read_text().split()))
        except FileNotFoundError:
            pass
    ticks = rss = 0
    for current in ids:
        try:
            fields = Path(f'/proc/{current}/stat').read_text().split(') ', 1)[1].split()
            ticks += int(fields[11]) + int(fields[12])
            rss += int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
        except FileNotFoundError:
            pass
    return ticks / os.sysconf('SC_CLK_TCK'), rss / 1024**2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = {'machine': platform.uname()._asdict(), 'logical_cpus': os.cpu_count(),
              'interval_seconds': args.seconds, 'method': 'procfs process-tree summed RSS and CPU tick deltas; no browser load; one Gunicorn worker'}
    with tempfile.TemporaryDirectory(prefix='hsm-measure-') as directory:
        db = Path(directory) / 'hsm.sqlite'
        migrate(db)
        discovery = LxdDiscovery(Client, 10)
        inventory = discovery.inventory()
        CapacityService(db, discovery)._reconcile(inventory['instances'])
        result['workload'] = [{'project': i['project'], 'name': i['name'], 'state': i['status']} for i in inventory['instances']]
        result['lxd_version'] = Client().host_info['environment']['server_version']
        result['pools'] = [p.__dict__ for p in discovery.pools()]
        result['memory_total_kib'] = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))
        env = dict(os.environ, DATABASE_PATH=str(db), TINYFLUX_DIR=directory+'/metrics',
                   PUBLIC_BASE_URL='http://localhost:18761', COOKIE_SECURE='false',
                   GOOGLE_CLIENT_ID='measurement-placeholder', GOOGLE_CLIENT_SECRET='measurement-placeholder',
                   BOOTSTRAP_ADMIN_EMAIL='measurement@example.invalid')
        commands = {'api': [sys.executable, '-m', 'gunicorn', '--workers', '1', '--bind', '127.0.0.1:18761', 'hsm.wsgi:app'],
                    'collector': [sys.executable, '-m', 'hsm.collector']}
        processes = {}
        logs = {}
        try:
            for name, command in commands.items():
                logs[name] = open(Path(directory)/f'{name}.log', 'w+')
                processes[name] = subprocess.Popen(command, env=env, cwd=ROOT/'backend', stdout=logs[name], stderr=logs[name])
            time.sleep(5)
            with urllib.request.urlopen('http://127.0.0.1:18761/healthz', timeout=5) as response:
                result['health'] = response.status
            start = {name: process_tree(p.pid) for name, p in processes.items()}
            rss = {name: [] for name in processes}
            samples = []
            began = time.monotonic()
            while time.monotonic()-began < args.seconds:
                for name, p in processes.items():
                    rss[name].append(process_tree(p.pid)[1])
                c = connect(db)
                samples.append({'elapsed': round(time.monotonic()-began, 3), 'latest': c.execute('SELECT container_id,sampled_at FROM metrics_latest').fetchall()})
                c.close()
                time.sleep(1)
            elapsed = time.monotonic()-began
            result['measurement'] = {name: {'rss_mean_mib': statistics.mean(rss[name]), 'rss_peak_mib': max(rss[name]),
                 'cpu_percent_one_core': (process_tree(p.pid)[0]-start[name][0])*100/elapsed, 'alive': p.poll() is None}
                 for name,p in processes.items()}
            result['elapsed_seconds'] = elapsed
            result['samples'] = samples
            processes['api'].terminate(); processes['api'].wait(timeout=10)
            c = connect(db)
            before = c.execute('SELECT COUNT(*) FROM metrics_history').fetchone()[0]; c.close()
            time.sleep(22)
            c = connect(db)
            result['api_stopped_history_counts'] = [before,c.execute('SELECT COUNT(*) FROM metrics_history').fetchone()[0]]
            result['collector_status'] = c.execute('SELECT key,value_json FROM collector_status').fetchall()
            result['snapshots'] = c.execute('SELECT container_id,state,cpu_pct,ram_used_bytes,disk_used_bytes,sampled_at FROM metrics_latest').fetchall()
            c.close()
            result['tinyflux_bytes'] = sum(p.stat().st_size for p in (Path(directory)/'metrics').glob('*'))
            processes['collector'].terminate(); processes['collector'].wait(timeout=15)
            c = connect(db)
            before_restart = c.execute('SELECT COUNT(*) FROM metrics_history').fetchone()[0]
            prior_snapshot = c.execute('SELECT container_id,sampled_at FROM metrics_latest ORDER BY container_id').fetchall()
            c.close()
            collector = Collector(db, Path(directory)/'metrics')
            def unavailable(**kwargs):
                raise ConnectionError('controlled unavailable transport; real LXD stays running')
            collector.poll_lxd(unavailable, 10)
            c = connect(db)
            result['injected_outage'] = {'heartbeat': json.loads(c.execute("SELECT value_json FROM collector_status WHERE key='heartbeat'").fetchone()[0]),
                'snapshots_preserved': prior_snapshot == c.execute('SELECT container_id,sampled_at FROM metrics_latest ORDER BY container_id').fetchall()}
            c.close()
            collector.poll_lxd(Client, 10)
            c = connect(db)
            result['restart_history_counts'] = [before_restart,c.execute('SELECT COUNT(*) FROM metrics_history').fetchone()[0]]
            result['recovered_heartbeat'] = json.loads(c.execute("SELECT value_json FROM collector_status WHERE key='heartbeat'").fetchone()[0])
            with c:
                c.execute("INSERT INTO users(id,email,role,status,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes,created_at) VALUES(1,'measurement@example.invalid','admin','active',0,0,0,datetime('now'))")
            c.close()
            token = SessionService(db, idle_seconds=1800, absolute_seconds=28800).create(1)
            client = falcon.testing.TestClient(create_app(load_settings(env)))
            result['api_checks'] = {}
            for route in ['/api/containers', '/api/accounting', f"/api/containers/{prior_snapshot[0][0]}/history?range=1h"]:
                response = client.simulate_get(route, cookies={'hsm_session': token})
                result['api_checks'][route] = {'status': response.status_code, 'bytes': len(response.content), 'body': response.json}
            times = sorted({row[1] for sample in samples for row in sample['latest']})
            from datetime import datetime
            deltas = [(datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds() for a,b in zip(times,times[1:])]
            result['cadence_seconds'] = {'intervals': deltas, 'mean': statistics.mean(deltas) if deltas else None}
            result['tinyflux_bytes_after_restart'] = sum(p.stat().st_size for p in (Path(directory)/'metrics').glob('*'))
        finally:
            for p in processes.values():
                if p.poll() is None:
                    p.terminate()
                    try: p.wait(timeout=10)
                    except subprocess.TimeoutExpired: p.kill(); p.wait()
            for name, log in logs.items():
                log.seek(0); result[name+'_log'] = log.read()[-4000:]; log.close()
            Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('samples','api_log','collector_log')}, indent=2))


if __name__ == '__main__':
    main()
