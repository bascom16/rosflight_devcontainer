"""Vehicle profiles: declarative YAML describing how to launch and fly one vehicle type."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .processes import WS_ROOT

PROFILE_DIR = Path(__file__).resolve().parents[1] / 'profiles'


def share_dir(package: str) -> Path:
    """Installed share directory of a ROS package (isolated or merged colcon install, or /opt/ros)."""
    distro = os.environ.get('ROS_DISTRO', 'humble')
    candidates = [
        WS_ROOT / 'install' / package / 'share' / package,
        WS_ROOT / 'install' / 'share' / package,
        Path('/opt/ros') / distro / 'share' / package,
    ]
    return next((c for c in candidates if c.is_dir()), candidates[0])


def expand(path: str) -> str:
    path = path.replace('{ws}', str(WS_ROOT))
    return re.sub(r'\{share:([\w-]+)\}', lambda m: str(share_dir(m.group(1))), path)


@dataclass
class LaunchSpec:
    package: str
    launch: str
    args: dict[str, str] = field(default_factory=dict)

    def command(self, extra: dict[str, str] | None = None) -> str:
        args = {**self.args, **(extra or {})}
        parts = ['ros2', 'launch', self.package, self.launch]
        parts += [f'{k}:={expand(str(v))}' for k, v in args.items()]
        return ' '.join(parts)


@dataclass
class Profile:
    key: str
    name: str
    airframe: str
    sim: LaunchSpec
    autopilot: LaunchSpec
    firmware_param_file: str
    mission_service: str
    mission_globs: list[str]

    @property
    def workdir(self) -> Path:
        """cwd for this airframe's processes; sil_board keeps rosflight_memory/ here."""
        state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state'))
        return state / 'sim_launcher' / self.airframe

    @property
    def firmware_marker(self) -> Path:
        """Written after firmware setup succeeds. (sil_board creates rosflight_memory/mem.bin
        with defaults on every start, so that file alone does not mean the setup ran.)"""
        return self.workdir / 'firmware_configured'

    @property
    def firmware_saved(self) -> bool:
        return (self.firmware_marker.is_file()
                and (self.workdir / 'rosflight_memory' / 'mem.bin').is_file())

    def missions(self) -> list[str]:
        found: list[str] = []
        for pattern in self.mission_globs:
            for path in sorted(glob.glob(expand(pattern))):
                if path not in found:
                    found.append(path)
        return found


def load_profile(path: Path) -> Profile:
    data = yaml.safe_load(path.read_text())
    return Profile(
        key=path.stem,
        name=data['name'],
        airframe=data['airframe'],
        sim=LaunchSpec(**data['sim']),
        autopilot=LaunchSpec(**data['autopilot']),
        firmware_param_file=data['firmware']['param_file'],
        mission_service=data['mission']['service'],
        mission_globs=list(data['mission'].get('files', [])),
    )


def load_profiles(directory: Path = PROFILE_DIR) -> dict[str, Profile]:
    return {p.key: p for p in map(load_profile, sorted(directory.glob('*.yaml')))}
