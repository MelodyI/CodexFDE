"""Local desktop entry with an explicit, identity-checked workbench restart."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import webbrowser

from .runtime_lease import WorkbenchRuntimeLease

ROOT = Path(__file__).resolve().parent.parent


def _posix_listener(port: int) -> tuple[int, list[str]]:
    """Resolve the single macOS/Linux listener with lsof and ps, without a shell."""
    try:
        owners = subprocess.run(['lsof', '-ti', f'tcp:{int(port)}', '-sTCP:LISTEN'],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('无法列出端口占用进程（需要 lsof），未停止任何进程') from error
    if owners.returncode not in (0, 1):
        raise RuntimeError('无法列出端口占用进程，未停止任何进程')
    pids = [line.strip() for line in owners.stdout.splitlines() if line.strip()]
    if not pids:
        raise RuntimeError('端口上没有可停止的监听进程')
    if len(pids) > 1:
        raise RuntimeError('监听进程不唯一，未停止任何进程')
    try:
        pid = int(pids[0])
    except ValueError as error:
        raise RuntimeError('无法识别监听进程，未停止任何进程') from error
    try:
        detail = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('无法核对旧服务命令，未停止任何进程') from error
    command = detail.stdout.strip()
    if detail.returncode or not command:
        raise RuntimeError('无法核对旧服务命令，未停止任何进程')
    return pid, shlex.split(command)


def listener_process(port: int) -> tuple[int, list[str]]:
    """Read the actual listener and parse its command line without a shell."""
    if os.name != 'nt':
        return _posix_listener(port)
    script = ('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
              f'$owners = @(Get-NetTCPConnection -LocalPort {int(port)} -State Listen '
              '| Select-Object -ExpandProperty OwningProcess -Unique); '
              'if ($owners.Count -ne 1) { throw "Listener is not unique" }; '
              'Get-CimInstance Win32_Process -Filter ("ProcessId=" + $owners[0]) '
              '| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('无法核对旧服务进程，未停止任何进程')
    try:
        data = json.loads(result.stdout.decode('utf-8-sig'))
        import ctypes
        from ctypes import wintypes
        parse = ctypes.windll.shell32.CommandLineToArgvW
        parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        parse.restype = ctypes.POINTER(wintypes.LPWSTR)
        count = ctypes.c_int()
        pointer = parse(data['CommandLine'], ctypes.byref(count))
        if not pointer:
            raise ValueError('Missing command line')
        try:
            arguments = [pointer[i] for i in range(count.value)]
        finally:
            free = ctypes.windll.kernel32.LocalFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
            free(ctypes.cast(pointer, ctypes.c_void_p))
        return int(data['ProcessId']), arguments
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError('无法识别旧服务命令，未停止任何进程') from error


def assert_no_running_work(port: int) -> None:
    """Refuse to stop a workbench that still owns running work."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def read(path):
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            return json.load(response)
    try:
        for item in read('/api/v1/initiatives')['items']:
            flow = read('/api/v1/initiatives/' + item['id'] + '/workflow')
            if flow.get('stage') in {'researching', 'queued', 'executing', 'cancelling', 'integrating'}:
                raise RuntimeError('工作台仍有运行中的事项，请先在页面停止任务并等待结束，再重启')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('无法核对运行中的事项，未停止旧服务') from error


def terminate_listener(pid: int, *, timeout: float = 15) -> None:
    """Terminate one verified process; never a process tree."""
    if os.name == 'nt':
        result = subprocess.run(['taskkill.exe', '/PID', str(pid), '/F'], capture_output=True,
                                timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('未能停止旧服务，请核对权限或原启动窗口')
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as error:
        raise RuntimeError(f'未能停止旧服务：{error}') from error


def command_matches_service(arguments: list[str], runtime: Path, port: int, surface: str) -> bool:
    """Only accept a listener that is our own service for this data directory."""
    try:
        module = arguments.index('-m')
        location = arguments[arguments.index('--runtime-dir') + 1]
        listener_port = int(arguments[arguments.index('--port') + 1])
    except (ValueError, IndexError):
        return False
    expected = ['workbench.cli', 'serve-workbench'] if surface == 'workbench' else ['flowerp', 'serve']
    return (arguments[module + 1:module + 3] == expected
            and Path(location).is_absolute() and Path(location).resolve() == runtime.resolve()
            and listener_port == port)


def stop_service(runtime: Path, port: int, surface: str = 'workbench', timeout: float = 20) -> dict:
    """Stop only the identity-verified listener of this runtime; data and evidence stay."""
    if surface not in {'workbench', 'flowerp'}:
        raise ValueError('未知的本地服务')
    runtime = runtime.resolve()
    label = '工作台' if surface == 'workbench' else 'FlowERP'
    url = f'http://127.0.0.1:{port}'
    state = inspect_service(port, runtime, surface)
    if state == 'free':
        return {'state': 'not_running', 'surface': surface, 'port': port, 'url': url}
    if state != 'same':
        raise RuntimeError(f'端口 {port} 上的服务不是本运行目录的{label}，未停止任何进程')
    if surface == 'workbench':
        assert_no_running_work(port)
    pid, arguments = listener_process(port)
    if pid <= 0 or pid == os.getpid() or not command_matches_service(arguments, runtime, port, surface):
        raise RuntimeError(f'监听进程不是指定目录的{label}服务，未停止任何进程')
    print(f'正在停止{label}（端口 {port}，进程 {pid}），任务与证据保留。', flush=True)
    terminate_listener(pid)
    deadline = time.monotonic() + timeout
    force_at = time.monotonic() + min(5.0, timeout / 2)
    forced = False
    while time.monotonic() < deadline:
        state = inspect_service(port, runtime, surface)
        if state == 'free':
            return {'state': 'stopped', 'surface': surface, 'port': port, 'pid': pid, 'url': url}
        if state == 'occupied':
            raise RuntimeError('端口已被其他服务接管，请核对后再停止')
        if not forced and os.name != 'nt' and time.monotonic() >= force_at:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            forced = True
        time.sleep(.2)
    raise RuntimeError(f'{label}仍未释放端口 {port}，请查看启动日志后手工核对')


def stop_workbench(runtime: Path, port: int, timeout: float = 20) -> None:
    """Restart helper: stop the verified workbench listener before relaunch."""
    stop_service(runtime, port, 'workbench', timeout)


def stop_report(runtime: Path, port: int, surface: str) -> dict:
    """Report one stop attempt honestly; a failure is never reported as stopped."""
    try:
        return stop_service(runtime, port, surface)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        return {'state': 'failed', 'surface': surface, 'port': port, 'message': str(error)}


def wait_for_product_release(runtime: Path, *, timeout: float = 35) -> None:
    """Wait for an existing writer lease without changing it or its owner."""
    database = runtime / 'flowerp.db'
    if not database.exists():
        return
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='instance_leases'").fetchone()
            active = exists and connection.execute(
                "SELECT 1 FROM instance_leases WHERE lease_name='sqlite-primary-writer' AND expires_at>CURRENT_TIMESTAMP"
            ).fetchone()
        if not active:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('FlowERP 的数据仍由另一个实例使用。工作台可以继续打开；请核对其他 FlowERP 窗口后再重开。')
        if not announced:
            print('FlowERP 正在等待上次运行释放数据，通常不超过 35 秒，请保留窗口，无需重复点击。', flush=True)
            announced = True
        time.sleep(.5)


def inspect_service(port: int, runtime: Path, surface: str = 'workbench') -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        endpoint = '/api/health' if surface == 'workbench' else '/api/v1/health/live'
        with opener.open(f'http://127.0.0.1:{port}{endpoint}', timeout=1) as response:
            data = json.load(response)
        if surface == 'flowerp' and isinstance(data, dict):
            identity = hashlib.sha256(os.path.normcase(str(runtime.resolve())).encode()).hexdigest()
            return 'same' if (data.get('service') == 'flowerp' and data.get('status') == 'ok'
                              and data.get('runtime_id') == identity) else 'occupied'
        if (isinstance(data, dict) and data.get('surface') == 'workbench' and data.get('status') == 'ok'
                and Path(data.get('runtime', '')).resolve() == runtime.resolve()):
            return 'same'
        return 'occupied'
    except (OSError, ValueError, TypeError):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.3):
                return 'occupied'
        except OSError:
            return 'free'


def launch(runtime: Path, port: int = 8901, *, timeout: float = 20,
           surface: str = 'workbench', erp_port: int = 8900, restart: bool = False) -> dict:
    if surface not in {'workbench', 'flowerp'}:
        raise ValueError('未知的本地服务')
    label = '工作台' if surface == 'workbench' else 'FlowERP'
    runtime = runtime.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    # Serialize launchers separately from the server's lifetime lock.
    with WorkbenchRuntimeLease(runtime / ('desktop-launch-' + surface)):
        state = inspect_service(port, runtime, surface)
        url = f'http://127.0.0.1:{port}'
        restarted = False
        if state == 'same' and restart and surface == 'workbench':
            stop_workbench(runtime, port, timeout)
            restarted = True
            state = inspect_service(port, runtime, surface)
        if state == 'same':
            return {'state': 'reused', 'url': url}
        if state != 'free':
            raise RuntimeError(f'端口 {port} 已由其他服务或另一份任务目录使用。请核对原窗口，不要重复启动。')
        if surface == 'flowerp':
            wait_for_product_release(runtime)
            # Another launcher may have acquired the port while we waited.
            state = inspect_service(port, runtime, surface)
            if state == 'same':
                return {'state': 'reused', 'url': url}
            if state != 'free':
                raise RuntimeError(f'端口 {port} 已被使用，请核对已打开的客户项目。')
        logs = runtime / 'startup-logs'
        logs.mkdir(exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns()}'
        log = logs / f'{surface}-{stamp}.log'
        command = [sys.executable, '-X', 'utf8', '-m', 'workbench.cli',
                   'serve-workbench' if surface == 'workbench' else 'serve',
                   '--host', '127.0.0.1', '--port', str(port), '--runtime-dir', str(runtime)]
        if surface == 'workbench':
            command += ['--enable-code-execution', '--erp-url', f'http://127.0.0.1:{erp_port}']
        working_directory = ROOT
        if surface == 'flowerp':
            from .external_project import command as product_command
            working_directory, command = product_command(['serve', '--host', '127.0.0.1',
                '--port', str(port), '--runtime-dir', str(runtime)])
        options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        with log.open('wb') as output:
            from .codex_options import headless_environment
            process = subprocess.Popen(command, cwd=working_directory, env=headless_environment(), stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, **options)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'{label}未能启动。原因保存在：{log}')
            state = inspect_service(port, runtime, surface)
            if state == 'same':
                return {'state': 'restarted' if restarted else 'started', 'url': url, 'pid': process.pid, 'log': str(log)}
            time.sleep(.2)
        raise RuntimeError(f'{label}仍未就绪，请先查看启动日志，不要连续重试：{log}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='打开个人研发工作台，保留原任务和启动记录')
    parser.add_argument('--runtime-dir', type=Path, help='显式覆盖工作台运行目录')
    parser.add_argument('--erp-runtime-dir', type=Path, help='显式覆盖 FlowERP 运行目录')
    parser.add_argument('--port', type=int, default=8901)
    parser.add_argument('--erp-port', type=int, default=8900)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--reuse', action='store_true', help='复用已有工作台，不重启；默认重启同目录的旧工作台')
    parser.add_argument('--stop', nargs='?', const='all', choices=['all', 'workbench', 'flowerp'],
                        help='停止本机服务，任务与数据保留；默认同时停止工作台和 FlowERP')
    args = parser.parse_args(argv)
    if not all(1 <= port <= 65535 for port in (args.port, args.erp_port)) or args.port == args.erp_port:
        parser.error('工作台和 FlowERP 必须使用 1 到 65535 之间的不同端口')
    from .runtime_paths import service_runtime
    try:
        workbench_runtime = service_runtime('workbench', args.runtime_dir, root=ROOT)
        erp_runtime = service_runtime('flowerp', args.erp_runtime_dir, root=ROOT)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.stop:
        targets = [('workbench', workbench_runtime, args.port), ('flowerp', erp_runtime, args.erp_port)]
        if args.stop != 'all':
            targets = [item for item in targets if item[0] == args.stop]
        report = {surface: stop_report(runtime, port, surface) for surface, runtime, port in targets}
        print(json.dumps(report, ensure_ascii=False))
        return 1 if any(item['state'] == 'failed' for item in report.values()) else 0
    try:
        result = launch(workbench_runtime, args.port, erp_port=args.erp_port, restart=not args.reuse)
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print(f'暂时无法打开工作台：{error}', file=sys.stderr)
        return 1
    failed = False
    try:
        result['flowerp'] = launch(erp_runtime, args.erp_port, surface='flowerp')
    except (OSError, RuntimeError, sqlite3.Error) as error:
        failed = True
        result['flowerp'] = {'state': 'unavailable', 'message': str(error)}
        print(f'工作台可用，但客户项目尚未就绪：{error}', file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    if args.open_browser:
        webbrowser.open(result['url'])
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
