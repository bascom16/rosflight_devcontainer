import os

import pytest

from sim_launcher.processes import WS_ROOT
from sim_launcher.profiles import expand, load_profiles


def test_profiles_load():
    profiles = load_profiles()
    assert {'multirotor', 'fixedwing'} <= set(profiles)
    for p in profiles.values():
        assert p.sim.command().startswith('ros2 launch ')
        assert p.autopilot.command().startswith('ros2 launch ')
        assert p.mission_service.endswith('load_mission_from_file')


@pytest.mark.skipif(not (WS_ROOT / 'install').is_dir(), reason='workspace not built')
def test_profile_paths_resolve():
    for p in load_profiles().values():
        assert os.path.isfile(expand(p.firmware_param_file)), p.key
        assert p.missions(), f'{p.key}: no mission files found'


def test_launch_args_are_expanded():
    p = load_profiles()['multirotor']
    cmd = p.sim.command({'use_vimfly': 'true', 'x': '{ws}/a'})
    assert cmd.endswith(f'use_vimfly:=true x:={WS_ROOT}/a')
