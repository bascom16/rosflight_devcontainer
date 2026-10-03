"""Start and stop ROS 2 processes, each in its own process group, with captured output."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import sys
import time
from collections import deque
from pathlib import Path

WS_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = Path('/tmp/sim_launcher')


def ros_env() -> dict[str, str]:
    """Environment for ROS children: drop anything pointing at the launcher's own venv.

    The launcher runs under a uv-managed Python 3.12; ROS (rclpy, ros2 CLI, rc.py with
    an `env python3` shebang) needs the system Python, so the venv must not leak in.
    """
    env = dict(os.environ)
    venv = env.pop('VIRTUAL_ENV', None) or sys.prefix
    for key in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT'):
        env.pop(key, None)
    env['PATH'] = os.pathsep.join(
        p for p in env.get('PATH', '').split(os.pathsep) if p and not p.startswith(venv)
    )
    return env


def ros_shell(command: str) -> list[str]:
    """argv that runs `command` in bash with ROS 2 and the workspace sourced."""
    distro = os.environ.get('ROS_DISTRO', 'humble')
    prelude = (
        'set +u; '
        'if [ -f "$HOME/.rosflight_env.bash" ]; then source "$HOME/.rosflight_env.bash"; '
        f'else source /opt/ros/{distro}/setup.bash; '
        f'[ -f {shlex.quote(str(WS_ROOT / "install/setup.bash"))} ] '
        f'&& source {shlex.quote(str(WS_ROOT / "install/setup.bash"))}; fi; '
    )
    return ['bash', '-c', prelude + 'exec ' + command]


class OutputBuffer:
    """Bounded line buffer that clients can read incrementally by line number."""

    def __init__(self, maxlen: int = 3000) -> None:
        self._lines: deque[str] = deque(maxlen=maxlen)
        self.total = 0

    def append(self, line: str) -> None:
        self._lines.append(line)
        self.total += 1

    def since(self, index: int) -> tuple[list[str], int]:
        """Lines numbered >= index (as many as are still buffered), and the new index."""
        missing = self.total - index
        if missing <= 0:
            return [], self.total
        return list(self._lines)[-min(missing, len(self._lines)):], self.total


class ManagedProcess:
    def __init__(self, name: str, argv: list[str], cwd: Path, output: OutputBuffer,
                 display: str = '') -> None:
        self.name = name
        self.argv = argv
        self.display = display or shlex.join(argv)
        self.cwd = cwd
        self.output = output
        self.proc: asyncio.subprocess.Process | None = None
        self.returncode: int | None = None
        self.started_at = 0.0
        self._reader: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.returncode is None

    async def start(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.output.append(f'--- {self.name}: {self.display} (cwd {self.cwd})')
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            env=ros_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self.returncode = None
        self.started_at = time.monotonic()
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        with open(LOG_DIR / f'{self.name}.log', 'w') as log:
            async for raw in self.proc.stdout:
                line = raw.decode(errors='replace').rstrip()
                self.output.append(line)
                log.write(line + '\n')
                log.flush()
        self.returncode = await self.proc.wait()
        self.output.append(f'--- {self.name} exited with code {self.returncode}')

    def _signal_group(self, sig: int) -> bool:
        """Signal the whole process group; False if no member is left."""
        try:
            os.killpg(self.proc.pid, sig)
            return True
        except ProcessLookupError:
            return False

    def _group_alive(self) -> bool:
        return self._signal_group(0)

    async def stop(self, grace: float = 10.0) -> None:
        """SIGINT the process group (clean `ros2 launch` shutdown), then escalate."""
        if self.proc is None:
            return
        for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
            if not self._signal_group(sig):
                break
            deadline = time.monotonic() + wait
            # The leader can exit before its children, so wait for the whole group.
            while time.monotonic() < deadline and self._group_alive():
                await asyncio.sleep(0.2)
            if not self._group_alive():
                break
            self.output.append(f'--- {self.name}: still running after {signal.Signals(sig).name}, '
                               'escalating')
        if self._reader:
            await self._reader


class ProcessManager:
    """Named processes (at most one per name), stopped in reverse start order."""

    def __init__(self) -> None:
        self.processes: dict[str, ManagedProcess] = {}
        self.outputs: dict[str, OutputBuffer] = {}

    def output(self, name: str) -> OutputBuffer:
        return self.outputs.setdefault(name, OutputBuffer())

    def running(self, name: str) -> bool:
        proc = self.processes.get(name)
        return proc is not None and proc.running

    async def start(self, name: str, argv: list[str], cwd: Path,
                    display: str = '') -> ManagedProcess:
        if self.running(name):
            raise RuntimeError(f'{name} is already running')
        proc = ManagedProcess(name, argv, cwd, self.output(name), display)
        await proc.start()
        self.processes.pop(name, None)
        self.processes[name] = proc
        return proc

    async def stop(self, name: str) -> None:
        proc = self.processes.get(name)
        if proc:
            await proc.stop()

    async def stop_all(self) -> None:
        for name in reversed(list(self.processes)):
            await self.stop(name)
