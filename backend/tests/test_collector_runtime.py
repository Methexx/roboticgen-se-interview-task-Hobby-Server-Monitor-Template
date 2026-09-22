from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import json
import tempfile

from pylxd.models.instance import InstanceState
from tinyflux import TinyFlux
from hsm.collector.service import Collector, LatestSnapshot
from hsm.db import migrate, connect


class CollectorRuntimeTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'db.sqlite'
        migrate(self.path)
        c = connect(self.path)
        with c:
            c.execute("INSERT INTO containers(id,project,lxd_uuid,current_name,managed,isolation_status,lifecycle,first_seen_at,last_seen_at) VALUES('id','p','uuid','c',0,'safe','present','now','now')")
        c.close()
        self.collector = Collector(self.path)

    def factory(self, **kwargs):
        state = InstanceState({'status': 'Running', 'memory': {'usage': 12}, 'cpu': {'usage': 100},
            'network': {'eth0': {'counters': {'bytes_received': 100, 'bytes_sent': 200},
                'addresses': [{'family': 'inet', 'scope': 'global', 'address': '10.0.0.2'}]}},
            'processes': 5, 'disk': {'root': {'usage': 1000}}})
        instance = SimpleNamespace(config={'volatile.uuid': 'uuid'}, state=lambda: state)
        return SimpleNamespace(projects=SimpleNamespace(all=lambda: [SimpleNamespace(name='p')]),
                               instances=SimpleNamespace(all=lambda: [instance]))

    def test_real_pylxd_state_type_persists_and_recovers(self):
        self.collector.poll_lxd(self.factory, 10)
        c = connect(self.path)
        before = c.execute('SELECT sampled_at FROM metrics_latest').fetchone()[0]
        c.close()
        def down(**kwargs): raise ConnectionError('injected')
        self.collector.poll_lxd(down, 10)
        c = connect(self.path)
        self.assertEqual(c.execute('SELECT sampled_at FROM metrics_latest').fetchone()[0], before)
        status = json.loads(c.execute("SELECT value_json FROM collector_status WHERE key='heartbeat'").fetchone()[0])
        self.assertEqual(status['state'], 'lxd_down')
        self.assertIn('last_successful_collection', status)
        c.close()
        Collector(self.path).poll_lxd(self.factory, 10)
        for path in (self.path.parent / 'metrics').glob('*.tinyflux'):
            with TinyFlux(str(path)) as database:
                points = database.all()
                self.assertEqual(len(points), 2)
                self.assertEqual(points[0].tags['ipv4_json'], '["10.0.0.2"]')
                self.assertNotIn('cpu_pct', points[0].fields)

    def test_counter_rates_missing_and_reset_are_not_zero(self):
        instance = SimpleNamespace(config={})
        now = datetime.now(timezone.utc).isoformat()
        def state(cpu, rx): return {'cpu': {'usage': cpu}, 'network': {'eth0': {'counters': {'bytes_received': rx, 'bytes_sent': rx}}}}
        with patch('hsm.collector.service.time.monotonic', side_effect=[1, 11, 21]):
            first = self.collector._snapshot('id', instance, state(0, 10), now)
            second = self.collector._snapshot('id', instance, state(5_000_000_000, 110), now)
            reset = self.collector._snapshot('id', instance, state(0, 0), now)
        self.assertIsNone(first.cpu_pct)
        self.assertIsNone(first.disk_used_bytes)
        self.assertEqual(second.cpu_pct, 50)
        self.assertEqual(second.net_rx_bps, 10)
        self.assertIsNone(reset.net_rx_bps)

    def test_cache_and_partition_limits_and_write_failure_status(self):
        collector = Collector(self.path, cache_rows=2, max_bytes=1)
        now = datetime.now(timezone.utc)
        for i in range(3):
            collector.record_latest(LatestSnapshot('id', 'Running', (), (now+timedelta(seconds=i)).isoformat()))
        c = connect(self.path)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM metrics_history').fetchone()[0], 2)
        c.close()
        self.assertFalse(list((self.path.parent/'metrics').glob('*.tinyflux')))
        with patch('hsm.collector.service.TinyFlux', side_effect=OSError('injected')):
            collector.poll_lxd(self.factory, 10)
        c = connect(self.path)
        self.assertEqual(json.loads(c.execute("SELECT value_json FROM collector_status WHERE key='history_store'").fetchone()[0])['state'], 'tinyflux_error')
        c.close()
