from pathlib import Path
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

import io
import json
import sqlite3
from workbench.desktop import (launch, inspect_service, main, wait_for_product_release,
                               stop_workbench, stop_service, terminate_listener, _posix_listener)
import hashlib
import os
import signal
import subprocess


class DesktopLaunchTests(unittest.TestCase):
    def test_entry_restarts_workbench_by_default_and_reuse_opts_out(self):
        for options, expected in [([], True), (['--reuse'], False)]:
            with patch('workbench.desktop.launch', side_effect=lambda *a, **k: {'state':'started','url':'http://localhost'}) as run, \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(0, main(options))
                self.assertEqual(expected, run.call_args_list[0].kwargs['restart'])
                self.assertNotIn('restart', run.call_args_list[1].kwargs)

    def test_restart_waits_for_old_listener_then_starts_new_service(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('workbench.desktop.inspect_service', side_effect=['same','free','same']), \
             patch('workbench.desktop.stop_workbench') as stop, \
             patch('workbench.desktop.subprocess.Popen') as process:
            process.return_value.poll.return_value = None
            process.return_value.pid = 123
            result = launch(Path(directory), restart=True)
            self.assertEqual('restarted', result['state'])
            stop.assert_called_once()
            process.assert_called_once()

    def test_stop_only_targets_verified_command_and_keeps_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            evidence = runtime / 'evidence.txt'
            evidence.write_text('keep')
            args = ['python.exe','-m','workbench.cli','serve-workbench','--port','8001',
                    '--runtime-dir',str(runtime)]
            for command, allowed in [(args, True), (['python.exe','other.py'], False),
                                     (args[:-1]+[str(runtime / 'other')], False)]:
                with patch('workbench.desktop.inspect_service', side_effect=['same','free']), \
                     patch('workbench.desktop.urllib.request.build_opener') as opener, \
                     patch('workbench.desktop.listener_process', return_value=(123456,command)), \
                     patch('workbench.desktop.terminate_listener') as kill, \
                     patch('sys.stdout', new_callable=io.StringIO):
                    opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"items":[]}')
                    if allowed:
                        stop_workbench(runtime,8001)
                        self.assertEqual((123456,), kill.call_args.args)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'不是指定目录'):
                            stop_workbench(runtime,8001)
                        kill.assert_not_called()
                    self.assertEqual('keep',evidence.read_text())

    def test_posix_stop_sends_sigterm_to_the_verified_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            command = ['python','-m','workbench.cli','serve-workbench','--port','8001',
                       '--runtime-dir',str(runtime)]
            with patch('workbench.desktop.os.name','posix'), \
                 patch('workbench.desktop.os.kill') as kill, \
                 patch('workbench.desktop.inspect_service', side_effect=['same','free']), \
                 patch('workbench.desktop.urllib.request.build_opener') as opener, \
                 patch('workbench.desktop.listener_process', return_value=(4321,command)), \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"items":[]}')
                result = stop_service(runtime, 8001, 'workbench')
                self.assertEqual('stopped', result['state'])
                self.assertEqual(4321, result['pid'])
                self.assertEqual((4321, signal.SIGTERM), kill.call_args.args)
                self.assertIn('任务与证据保留', output.getvalue())

    def test_foreign_listener_is_never_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            for surface, command in [('workbench', ['python','-m','workbench.cli','serve-workbench']),
                                     ('flowerp', ['python','-m','workbench.cli','serve-workbench',
                                                  '--port','8002','--runtime-dir',str(Path(directory))])]:
                with patch('workbench.desktop.inspect_service', return_value='same'), \
                     patch('workbench.desktop.urllib.request.build_opener') as opener, \
                     patch('workbench.desktop.listener_process', return_value=(999,command)), \
                     patch('workbench.desktop.terminate_listener') as kill:
                    opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"items":[]}')
                    with self.assertRaisesRegex(RuntimeError,'不是指定目录'):
                        stop_service(Path(directory), 8002, surface)
                    kill.assert_not_called()

    def test_free_port_is_reported_as_not_running(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('workbench.desktop.inspect_service', return_value='free'), \
             patch('workbench.desktop.listener_process') as listener:
            self.assertEqual('not_running', stop_service(Path(directory), 8001)['state'])
            listener.assert_not_called()

    def test_posix_listener_needs_one_owner_and_a_readable_command(self):
        lsof = subprocess.CompletedProcess([], 0, stdout='4859\n')
        ps = subprocess.CompletedProcess([], 0, stdout='python3 -X utf8 -m workbench.cli serve-workbench --port 8001\n')
        with patch('workbench.desktop.os.name','posix'), \
             patch('workbench.desktop.subprocess.run', side_effect=[lsof, ps]) as run:
            pid, arguments = _posix_listener(8001)
            self.assertEqual(4859, pid)
            self.assertEqual(['workbench.cli','serve-workbench'], arguments[arguments.index('-m')+1:arguments.index('-m')+3])
            self.assertEqual(['lsof','-ti','tcp:8001','-sTCP:LISTEN'], run.call_args_list[0].args[0])
        empty = subprocess.CompletedProcess([], 1, stdout='')
        with patch('workbench.desktop.os.name','posix'), \
             patch('workbench.desktop.subprocess.run', return_value=empty):
            with self.assertRaisesRegex(RuntimeError,'没有可停止的监听进程'):
                _posix_listener(8001)
        shared = subprocess.CompletedProcess([], 0, stdout='11\n22\n')
        with patch('workbench.desktop.os.name','posix'), \
             patch('workbench.desktop.subprocess.run', return_value=shared):
            with self.assertRaisesRegex(RuntimeError,'不唯一'):
                _posix_listener(8001)

    def test_windows_stop_still_uses_taskkill(self):
        with patch('workbench.desktop.os.name','nt'), patch('workbench.desktop.subprocess.run') as run:
            run.return_value.returncode = 0
            terminate_listener(123456)
            self.assertEqual(['taskkill.exe','/PID','123456','/F'], run.call_args.args[0])
        with patch('workbench.desktop.os.name','nt'), patch('workbench.desktop.subprocess.run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError,'未能停止旧服务'):
                terminate_listener(123456)

    def test_stop_flag_stops_services_without_launching(self):
        with patch('workbench.desktop.launch') as start, \
             patch('workbench.desktop.stop_service', side_effect=[
                 {'state':'stopped','surface':'workbench'}, {'state':'not_running','surface':'flowerp'}]) as stop, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(0, main(['--stop','--erp-port','8002']))
            start.assert_not_called()
            self.assertEqual([(8001,'workbench'), (8002,'flowerp')],
                             [(call.args[1], call.args[2]) for call in stop.call_args_list])
            self.assertIn('"stopped"', output.getvalue())

    def test_stop_flag_can_target_one_service_and_reports_failure(self):
        with patch('workbench.desktop.launch') as start, \
             patch('workbench.desktop.stop_service', return_value={'state':'stopped'}) as stop, \
             patch('sys.stdout', new_callable=io.StringIO):
            main(['--stop','workbench'])
            self.assertEqual(1, stop.call_count)
            self.assertEqual('workbench', stop.call_args.args[2])
            start.assert_not_called()
        with patch('workbench.desktop.stop_service', side_effect=RuntimeError('端口 8000 上的服务不是本运行目录的FlowERP')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(1, main(['--stop','flowerp']))
            self.assertIn('不是本运行目录', output.getvalue())

    def test_active_work_prevents_termination(self):
        with patch('workbench.desktop.inspect_service', return_value='same'), \
             patch('workbench.desktop.urllib.request.build_opener') as opener, \
             patch('workbench.desktop.listener_process') as listener:
            opener.return_value.open.return_value.__enter__.side_effect = [
                io.StringIO('{"items":[{"id":"I1"}]}'), io.StringIO('{"stage":"executing"}')]
            with self.assertRaisesRegex(RuntimeError,'运行中的事项'):
                stop_workbench(Path('.'),8001)
            listener.assert_not_called()

    def test_expiring_writer_lease_is_waited_for_without_rewriting_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flowerp.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('CREATE TABLE instance_leases (lease_name TEXT, owner_id TEXT, expires_at TEXT)')
                conn.execute("INSERT INTO instance_leases VALUES ('sqlite-primary-writer','previous-instance',datetime('now','+60 seconds'))")
            def expire_lease(_seconds):
                with closing(sqlite3.connect(path)) as conn, conn:
                    conn.execute("UPDATE instance_leases SET expires_at=datetime('now','-1 second')")
            with patch('sys.stdout', new_callable=io.StringIO) as output, \
                 patch('workbench.desktop.time.sleep', side_effect=expire_lease) as sleep:
                wait_for_product_release(Path(directory), timeout=3)
                sleep.assert_called_once_with(.5)
                self.assertIn('无需重复点击', output.getvalue())
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual('previous-instance', conn.execute('SELECT owner_id FROM instance_leases').fetchone()[0])

    def test_active_writer_is_not_overridden_or_launched_over(self):
        with tempfile.TemporaryDirectory() as directory:
            with closing(sqlite3.connect(Path(directory) / 'flowerp.db')) as conn, conn:
                conn.execute('CREATE TABLE instance_leases (lease_name TEXT, expires_at TEXT)')
                conn.execute("INSERT INTO instance_leases VALUES ('sqlite-primary-writer',datetime('now','+60 seconds'))")
            with self.assertRaisesRegex(RuntimeError, '另一个实例'):
                wait_for_product_release(Path(directory), timeout=0)

    def test_flowerp_reuse_requires_the_same_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            payload = {'service':'flowerp', 'status':'ok', 'runtime_id':hashlib.sha256(os.path.normcase(str(runtime.resolve())).encode()).hexdigest()}
            for target, expected in [(runtime, 'same'), (runtime / 'another', 'occupied')]:
                with patch('workbench.desktop.urllib.request.build_opener') as opener:
                    opener.return_value.open.return_value.__enter__.return_value = io.StringIO(json.dumps(payload))
                    self.assertEqual(expected, inspect_service(8000, target, 'flowerp'))

    def test_malformed_health_is_not_adopted(self):
        with patch('workbench.desktop.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value = io.StringIO('[]')
            self.assertEqual('occupied', inspect_service(8000, Path('.'), 'flowerp'))

    def test_product_failure_keeps_workbench_access_and_returns_failure(self):
        with patch('workbench.desktop.launch', side_effect=[
            {'state': 'reused', 'url': 'http://127.0.0.1:8001'}, RuntimeError('端口占用')
        ]), patch('workbench.desktop.webbrowser.open') as browser, \
             patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(1, main(['--open-browser']))
            browser.assert_called_once_with('http://127.0.0.1:8001')

    def test_reopen_reuses_same_service_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('workbench.desktop.inspect_service', return_value='same'), \
             patch('workbench.desktop.subprocess.Popen') as process:
            self.assertEqual('reused', launch(Path(directory))['state'])
            process.assert_not_called()

    def test_other_service_is_not_reused_or_terminated(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('workbench.desktop.inspect_service', return_value='occupied'), \
             patch('workbench.desktop.subprocess.Popen') as process:
            with self.assertRaisesRegex(RuntimeError, '其他服务'):
                launch(Path(directory))
            process.assert_not_called()

    def test_child_failure_keeps_log_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('workbench.desktop.inspect_service', return_value='free'), \
             patch('workbench.desktop.subprocess.Popen') as process:
            process.return_value.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, '未能启动'):
                launch(Path(directory))
            self.assertEqual(1, len(list(Path(directory).glob('startup-logs/*.log'))))
