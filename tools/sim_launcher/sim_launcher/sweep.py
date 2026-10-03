"""Find and kill every ROS 2 process in the container, including ones started outside the launcher."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from .processes import WS_ROOT

ROS_PREFIXES = (str(WS_ROOT / 'install') + '/', '/opt/ros/')
ROS_NAMES = {'ros2', 'rviz2', '_ros2_daemon'}


@dataclass
class RosProcess:
    pid: int
    cmdline: str


def _ancestors(pid: int) -> set[int]:
    result = set()
    while pid > 1:
        result.add(pid)
        try:
            stat = Path(f'/proc/{pid}/stat').read_text()
            pid = int(stat.rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return result


def _is_ros(argv: list[str]) -> bool:
    # Interpreted nodes (python3 /opt/ros/.../ros2, python3 .../rc.py) show the script as argv[1].
    for arg in argv[:2]:
        if arg.startswith(ROS_PREFIXES) or os.path.basename(arg) in ROS_NAMES:
            return True
    return False


def find_ros_processes(exclude: set[int] = frozenset()) -> list[RosProcess]:
    """ROS processes (launch, nodes, rviz2, the ros2 daemon), excluding this process and its parents."""
    skip = _ancestors(os.getpid()) | set(exclude)
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        try:
            raw = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        argv = [a for a in raw.decode(errors='replace').split('\0') if a]
        if argv and _is_ros(argv):
            found.append(RosProcess(int(entry.name), ' '.join(argv)))
    return sorted(found, key=lambda p: p.pid)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Zombies count as gone.
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return False


async def kill_processes(procs: list[RosProcess]) -> list[RosProcess]:
    """SIGINT, then SIGTERM, then SIGKILL. Returns processes that survived (e.g. not ours)."""
    remaining = list(procs)
    for sig, wait in ((signal.SIGINT, 5.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
        for p in remaining:
            try:
                os.kill(p.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        for _ in range(int(wait / 0.2)):
            remaining = [p for p in remaining if _alive(p.pid)]
            if not remaining:
                return []
            await asyncio.sleep(0.2)
    return [p for p in remaining if _alive(p.pid)]
