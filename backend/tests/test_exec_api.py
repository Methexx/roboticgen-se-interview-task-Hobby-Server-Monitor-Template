import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_containers_api as container_tests
from hsm.db import connect
from hsm.lxd.creation import LxdCreator
from hsm.lxd.discovery import DiscoveryError


class ExecApiTests(unittest.TestCase):
    setUp = container_tests.ContainersApiTests.setUp
    tearDown = container_tests.ContainersApiTests.tearDown

    def call(self, token=None, command='secret-test-command', target='container-a'):
        return self.client.simulate_post(f'/api/containers/{target}/exec', json={'command': command},
                                        cookies={'hsm_session': token or self.admin_token})

    def db(self):
        return connect(Path(self.directory.name) / 'hsm.sqlite')

    def test_roles_metadata_and_slots(self):
        result = dict(stdout='secret-test-output', stderr='', exit_code=0, duration_ms=1, truncated=False, timed_out=False)
        with patch.object(LxdCreator, 'execute', return_value=result) as execute:
            self.assertEqual(self.call().status_code, 200)
            self.assertEqual(self.call(self.user_token).status_code, 200)
            self.assertEqual(self.call(self.user_token, target='container-b').status_code, 403)
            self.assertEqual(execute.call_count, 2)
        c = self.db()
        try:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM execution_slots').fetchone()[0], 0)
            audit = c.execute('SELECT detail_json,outcome FROM audit_log').fetchall()
            self.assertEqual(len(audit), 4)
            self.assertNotIn('secret-test', json.dumps(audit))
        finally: c.close()

    def test_input_and_unavailable_release(self):
        for command in ['x'*2001, 'x\x00', '']:
            self.assertEqual(self.call(command=command).status_code, 400)
        with patch.object(LxdCreator, 'execute', side_effect=DiscoveryError('unavailable', 'Unavailable')):
            self.assertEqual(self.call().status_code, 503)
        c = self.db()
        try:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM execution_slots').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT status FROM operations').fetchone()[0], 'unknown')
        finally: c.close()

    def test_concurrent_user_rejected_before_exec(self):
        def running(*args, **kwargs):
            self.assertEqual(self.call().status_code, 429)
            return dict(stdout='', stderr='', exit_code=124, duration_ms=15000, truncated=False, timed_out=True)
        with patch.object(LxdCreator, 'execute', side_effect=running):
            self.assertTrue(self.call().json['timed_out'])
        c = self.db()
        try: self.assertEqual(c.execute('SELECT COUNT(*) FROM execution_slots').fetchone()[0], 0)
        finally: c.close()

    def test_streaming_cap_and_stopped_state(self):
        def execute(argv, **kwargs):
            self.assertEqual(argv[:4], ['/usr/bin/timeout', '-k', '2s', '15s'])
            for _ in range(100):
                kwargs['stdout_handler'](b'x'*4096)
                kwargs['stderr_handler'](b'y'*4096)
            return SimpleNamespace(exit_code=124)
        instance = SimpleNamespace(config={'volatile.uuid': 'uuid'}, status='Running', execute=execute)
        factory = lambda **kwargs: SimpleNamespace(instances=SimpleNamespace(get=lambda name: instance))
        creator = LxdCreator(factory, 10)
        result = creator.execute('hsm', 'test', 'anything', expected_uuid='uuid')
        self.assertEqual(len(result['stdout'].encode())+len(result['stderr'].encode()), 65536)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['timed_out'])
        instance.status = 'Stopped'
        with self.assertRaises(DiscoveryError) as error:
            creator.execute('hsm', 'test', 'anything')
        self.assertEqual(error.exception.kind, 'not_running')
