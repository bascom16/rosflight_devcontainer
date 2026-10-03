"""Launcher-side client for ros_bridge.py, which runs under the system Python with rclpy."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shlex
import signal
import time
from pathlib import Path

from .processes import OutputBuffer, ros_env, ros_shell

BRIDGE_SCRIPT = Path(__file__).with_name('ros_bridge.py')

# Status.rc_override bits set while the RC override switch is on (pilot has control).
OVERRIDE_SWITCH_BITS = 0x01 | 0x02


class BridgeError(RuntimeError):
    pass


class RosBridge:
    def __init__(self, output: OutputBuffer) -> None:
        self.output = output
        self.proc: asyncio.subprocess.Process | None = None
        self.status: dict | None = None
        self.status_time = 0.0
        self.nodes: list[str] = []
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._start_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def status_fresh(self, max_age: float = 3.0) -> bool:
        return self.status is not None and time.monotonic() - self.status_time < max_age

    def armed(self) -> bool | None:
        return self.status['armed'] if self.status_fresh() else None

    def pilot_override(self) -> bool | None:
        """True while the RC override switch hands control to the pilot (not the autopilot)."""
        if not self.status_fresh():
            return None
        return bool(self.status['rc_override'] & OVERRIDE_SWITCH_BITS)

    def controller(self) -> str | None:
        """Who flies the vehicle: 'autopilot', 'pilot' (override switch on), or 'rc'
        (switch off, but no autopilot commands arriving, so RC is still in control)."""
        if not self.status_fresh():
            return None
        if self.pilot_override():
            return 'pilot'
        return 'autopilot' if self.status['offboard'] else 'rc'

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self.running:
                return
            cmd = f'/usr/bin/python3 {shlex.quote(str(BRIDGE_SCRIPT))}'
            self.output.append(f'--- starting ROS bridge: {cmd}')
            self.proc = await asyncio.create_subprocess_exec(
                *ros_shell(cmd),
                env=ros_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=1 << 20,
            )
            asyncio.create_task(self._read_stdout(self.proc))
            asyncio.create_task(self._read_stderr(self.proc))

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        async for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                self.output.append(raw.decode(errors='replace').rstrip())
                continue
            event = msg.get('event')
            if event == 'status':
                msg.pop('event')
                self.status, self.status_time = msg, time.monotonic()
            elif event == 'graph':
                self.nodes = msg['nodes']
            elif (fut := self._pending.pop(msg.get('id'), None)) and not fut.done():
                fut.set_result(msg)
        code = await proc.wait()
        self.output.append(f'--- ROS bridge exited with code {code}')
        self.status, self.nodes = None, []
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(BridgeError('ROS bridge exited'))
        self._pending.clear()

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        async for raw in proc.stderr:
            self.output.append(raw.decode(errors='replace').rstrip())

    async def call(self, service: str, srv_type: str, request: dict | None = None,
                   timeout: float = 10.0) -> dict:
        """Call a ROS service; waits up to `timeout` for it to appear. Returns the response."""
        await self.ensure_started()
        req_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        line = json.dumps({'id': req_id, 'op': 'call', 'service': service, 'type': srv_type,
                           'request': request or {}, 'timeout': timeout})
        self.proc.stdin.write(line.encode() + b'\n')
        await self.proc.stdin.drain()
        try:
            reply = await asyncio.wait_for(fut, timeout + 5.0)
        except asyncio.TimeoutError:
            raise BridgeError(f'{service}: no reply from the ROS bridge') from None
        finally:
            self._pending.pop(req_id, None)
        if not reply['ok']:
            raise BridgeError(reply['error'])
        return reply['response']

    async def stop(self) -> None:
        if not self.running:
            return
        self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), 5.0)
        except asyncio.TimeoutError:
            os.killpg(self.proc.pid, signal.SIGKILL)
            await self.proc.wait()
