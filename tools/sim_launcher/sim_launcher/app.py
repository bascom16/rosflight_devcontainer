"""ROSflight sim launcher: a browser GUI to start sims and missions and shut them down again.

Run with scripts/sim_launcher.sh (serves http://localhost:8090).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from collections.abc import Awaitable, Callable

from nicegui import app, background_tasks, ui

from .bridge import BridgeError, RosBridge
from .processes import ProcessManager, ros_shell
from .profiles import Profile, expand, load_profiles
from .sweep import find_ros_processes, kill_processes

PORT = int(os.environ.get('SIM_LAUNCHER_PORT', '8090'))
HOST = os.environ.get('SIM_LAUNCHER_HOST', '127.0.0.1')
DISPLAY_PORT = os.environ.get('SIM_DISPLAY_PORT', '6080')
# Full URL of the noVNC page; by default the same host the launcher page was loaded from.
DISPLAY_URL = os.environ.get('SIM_DISPLAY_URL', '')

RC_MODES = {'sim': 'Simulated RC (buttons)', 'vimfly': 'VimFly (keyboard)',
            'gamepad': 'Browser gamepad (coming soon)'}
FIRMWARE_MODES = {'auto': 'If not saved yet', 'always': 'Every sim start', 'skip': 'Never'}
LOGS = {'launcher': 'Launcher', 'sim': 'Sim', 'autopilot': 'Autopilot', 'bridge': 'ROS bridge'}


class StepError(RuntimeError):
    pass


class Launcher:
    """State shared by every open browser tab."""

    def __init__(self) -> None:
        self.profiles = load_profiles()
        self.profile_key = 'multirotor' if 'multirotor' in self.profiles else next(iter(self.profiles))
        self.rc_mode = 'sim'
        self.firmware_mode = 'auto'
        self.mission = ''
        self.pm = ProcessManager()
        self.events = self.pm.output('launcher')
        self.bridge = RosBridge(self.pm.output('bridge'))
        self.busy: str | None = None
        self._task: asyncio.Task | None = None
        self.select_profile(self.profile_key)

    # --- state ------------------------------------------------------------------------------
    @property
    def profile(self) -> Profile:
        return self.profiles[self.profile_key]

    def select_profile(self, key: str) -> None:
        self.profile_key = key
        missions = self.profile.missions()
        self.mission = missions[0] if missions else ''

    def any_running(self) -> bool:
        return self.pm.running('sim') or self.pm.running('autopilot')

    def log(self, msg: str) -> None:
        self.events.append(f'{time.strftime("%H:%M:%S")}  {msg}')

    def run(self, label: str, action: Callable[[], Awaitable[None]]) -> None:
        """Run a step in the background (it keeps going if the browser tab closes)."""
        if self.busy:
            ui.notify(f'Busy: {self.busy}', type='warning')
            return

        async def wrapper() -> None:
            self.busy = label
            self.log(f'▶ {label}')
            try:
                await action()
                self.log(f'✔ {label}')
            except asyncio.CancelledError:
                self.log(f'■ {label}: cancelled')
            except (StepError, BridgeError, RuntimeError) as exc:
                self.log(f'✖ {label}: {exc}')
            finally:
                self.busy = None
                self._task = None

        self._task = background_tasks.create(wrapper(), name=label)

    async def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _wait_until(self, cond: Callable[[], bool], timeout: float, what: str,
                          proc: str | None = None) -> None:
        deadline = time.monotonic() + timeout
        while not cond():
            if proc and not self.pm.running(proc):
                raise StepError(f'{proc} exited while waiting for {what} (see the {LOGS[proc]} log)')
            if time.monotonic() > deadline:
                raise StepError(f'timed out after {timeout:.0f} s waiting for {what}')
            await asyncio.sleep(0.25)

    # --- steps ------------------------------------------------------------------------------
    async def _launch(self, name: str, command: str) -> None:
        self.log(f'{LOGS[name]}: {command}')
        await self.pm.start(name, ros_shell(command), self.profile.workdir, display=command)

    async def start_sim(self) -> None:
        p = self.profile
        await self.bridge.ensure_started()
        await self._launch('sim', p.sim.command({'use_vimfly': str(self.rc_mode == 'vimfly').lower()}))
        self.log('Waiting for the firmware to report status...')
        await self._wait_until(lambda: self.bridge.status_fresh(), 90, 'firmware status', 'sim')
        if self.firmware_mode == 'always' or (self.firmware_mode == 'auto' and not p.firmware_saved):
            await self.setup_firmware()

    async def setup_firmware(self) -> None:
        """Same service calls as rosflight_sim's *_init_firmware.launch.py."""
        param_file = expand(self.profile.firmware_param_file)
        # rosflight_io silently skips parameters it has not yet fetched from the firmware.
        self.log('Waiting for rosflight_io to receive all firmware parameters...')
        deadline = time.monotonic() + 60
        while not (await self.bridge.call('/all_params_received', 'std_srvs/srv/Trigger',
                                          timeout=30)).get('success'):
            if time.monotonic() > deadline:
                raise StepError('rosflight_io did not receive all firmware parameters')
            await asyncio.sleep(0.5)
        self.log(f'Loading firmware parameters from {param_file}')
        res = await self.bridge.call('/param_load_from_file', 'rosflight_msgs/srv/ParamFile',
                                     {'filename': param_file}, timeout=30)
        if not res.get('success'):
            raise StepError(f'param_load_from_file failed for {param_file}')
        self.log('Calibrating IMU...')
        await self.bridge.call('/calibrate_imu', 'std_srvs/srv/Trigger', timeout=30)
        await asyncio.sleep(10)  # the upstream launch file waits this long before saving
        await self.bridge.call('/param_write', 'std_srvs/srv/Trigger', timeout=30)
        self.profile.firmware_marker.write_text(param_file + '\n')
        self.log(f'Firmware parameters saved under {self.profile.workdir / "rosflight_memory"}')

    async def start_autopilot(self) -> None:
        await self._launch('autopilot', self.profile.autopilot.command())

    async def load_mission(self) -> None:
        if not self.mission:
            raise StepError('no mission file selected')
        if not os.path.isfile(self.mission):
            raise StepError(f'mission file not found: {self.mission}')
        self.log(f'Loading mission {self.mission}')
        res = await self.bridge.call(self.profile.mission_service, 'rosflight_msgs/srv/ParamFile',
                                     {'filename': self.mission}, timeout=60)
        if not res.get('success'):
            raise StepError('the path planner rejected the mission file (see the Autopilot log)')

    async def set_armed(self, armed: bool) -> None:
        await self._wait_until(lambda: self.bridge.status_fresh(), 10, 'firmware status')
        if self.bridge.armed() == armed:
            return
        await self.bridge.call('/toggle_arm', 'std_srvs/srv/Trigger')
        try:
            await self._wait_until(lambda: self.bridge.armed() == armed, 5,
                                   'armed' if armed else 'disarmed')
        except StepError:
            raise StepError(f'firmware did not {"arm" if armed else "disarm"} '
                            '(check the Sim log for failsafe or calibration errors)') from None

    async def set_autopilot(self, on: bool) -> None:
        """Autopilot in control = RC override switch off."""
        await self._wait_until(lambda: self.bridge.status_fresh(), 10, 'firmware status')
        if self.bridge.pilot_override() == (not on):
            return
        await self.bridge.call('/toggle_override', 'std_srvs/srv/Trigger')
        await self._wait_until(lambda: self.bridge.pilot_override() == (not on), 5,
                               'RC override to change')

    async def reset_vehicle(self) -> None:
        res = await self.bridge.call('/dynamics/set_sim_state', 'rosflight_msgs/srv/SetSimState')
        if not res.get('success'):
            raise StepError(res.get('message', 'set_sim_state failed'))

    async def run_mission(self) -> None:
        if not self.pm.running('sim'):
            await self.start_sim()
        elif self.firmware_mode == 'always':
            await self.setup_firmware()
        if not self.pm.running('autopilot'):
            await self.start_autopilot()
        await self.load_mission()
        if self.rc_mode == 'sim':
            await self.set_armed(True)
            await self.set_autopilot(True)
        else:
            self.log('Mission loaded. In the VimFly window (sim display taskbar: "vimfly"), '
                     'press t to arm, then r to hand control to the autopilot.')

    async def stop_all(self) -> None:
        await self.cancel()
        self.log('Stopping all launched processes...')
        await self.pm.stop_all()
        self.log('All launched processes stopped.')

    async def kill_all(self) -> None:
        await self.stop_all()
        exclude = {self.bridge.proc.pid} if self.bridge.running else set()
        procs = find_ros_processes(exclude)
        if not procs:
            self.log('No other ROS processes found.')
            return
        self.log(f'Killing {len(procs)} ROS process(es)...')
        survivors = await kill_processes(procs)
        for p in survivors:
            self.log(f'Could not kill pid {p.pid}: {p.cmdline}')
        self.log('Done.' if not survivors else f'{len(survivors)} process(es) survived.')


launcher = Launcher()
app.on_startup(launcher.bridge.ensure_started)  # so status and the node count show right away
app.on_shutdown(launcher.stop_all)
app.on_shutdown(launcher.bridge.stop)


# --- UI ------------------------------------------------------------------------------------
def mission_options() -> dict[str, str]:
    """Mission files of the selected vehicle, labelled 'parent_dir/file.yaml'."""
    options = {path: '/'.join(path.split('/')[-2:]) for path in launcher.profile.missions()}
    if launcher.mission and launcher.mission not in options:
        options[launcher.mission] = launcher.mission
    return options


def badge_props(state: bool | None, on: str, off: str) -> tuple[str, str]:
    if state is None:
        return 'n/a', 'grey'
    return (on, 'positive') if state else (off, 'grey-7')


@ui.page('/')
async def index() -> None:
    L = launcher
    if not L.mission:  # e.g. the launcher started before the workspace was built
        L.select_profile(L.profile_key)
    ui.page_title('ROSflight sim launcher')
    ui.add_css('.mono { font-family: ui-monospace, monospace; font-size: 12px; }')

    with ui.header().classes('items-center'):
        ui.label('ROSflight sim launcher').classes('text-h6')
        ui.space()
        busy = ui.label().classes('text-caption')

    with ui.row().classes('w-full no-wrap items-start gap-4 max-lg:flex-wrap'):
        with ui.column().classes('w-[26rem] max-w-full gap-3'):
            # Setup
            with ui.card().classes('w-full'):
                ui.label('Setup').classes('text-subtitle1 text-weight-medium')
                vehicle = ui.select({k: p.name for k, p in L.profiles.items()}, label='Vehicle',
                                    value=L.profile_key).classes('w-full')
                rc = ui.select(RC_MODES, label='RC input', value=L.rc_mode).classes('w-full')
                mission = ui.select(mission_options(), label='Mission', value=L.mission or None,
                                    new_value_mode='add-unique', with_input=True) \
                    .classes('w-full').props('hint="Pick a file or type an absolute path"')
                firmware = ui.select(FIRMWARE_MODES, label='Firmware setup',
                                     value=L.firmware_mode).classes('w-full')
                fw_note = ui.label().classes('text-caption text-grey-7')

                def on_vehicle(e) -> None:
                    L.select_profile(e.value)
                    mission.set_options(mission_options(), value=L.mission or None)

                def on_rc(e) -> None:
                    if e.value == 'gamepad':
                        ui.notify('Browser gamepad support is not implemented yet.')
                        rc.value = L.rc_mode
                        return
                    L.rc_mode = e.value

                vehicle.on_value_change(on_vehicle)
                rc.on_value_change(on_rc)
                mission.on_value_change(lambda e: setattr(L, 'mission', e.value or ''))
                firmware.on_value_change(lambda e: setattr(L, 'firmware_mode', e.value))

            # Actions
            with ui.card().classes('w-full'):
                ui.label('Run').classes('text-subtitle1 text-weight-medium')
                run_btn = ui.button('Run mission', icon='flight_takeoff', color='primary',
                                    on_click=lambda: L.run('Run mission', L.run_mission)) \
                    .classes('w-full').tooltip('Start sim + autopilot, load the mission, arm, '
                                               'and hand control to the autopilot')
                with ui.grid(columns=2).classes('w-full gap-2'):
                    sim_btn = ui.button('Start sim', on_click=lambda: L.run('Start sim', L.start_sim))
                    fw_btn = ui.button('Set up firmware',
                                       on_click=lambda: L.run('Set up firmware', L.setup_firmware))
                    ap_btn = ui.button('Start autopilot',
                                       on_click=lambda: L.run('Start autopilot', L.start_autopilot))
                    ms_btn = ui.button('Load mission',
                                       on_click=lambda: L.run('Load mission', L.load_mission))
                    arm_btn = ui.button('Arm')
                    ovr_btn = ui.button('Give control to autopilot') \
                        .tooltip('Toggle the RC override switch')
                    reset_btn = ui.button('Reset vehicle', on_click=lambda: L.run(
                        'Reset vehicle', L.reset_vehicle)).tooltip('Move the vehicle back to the origin')
                for b in (sim_btn, fw_btn, ap_btn, ms_btn, arm_btn, ovr_btn, reset_btn):
                    b.props('outline no-caps')
                vimfly_hint = ui.label('VimFly: in the sim display, click "vimfly" in the taskbar '
                                       '(the window may be behind RViz), then press t to arm and '
                                       'r to toggle autopilot control.') \
                    .classes('text-caption text-grey-8')

                def on_arm() -> None:
                    target = not L.bridge.armed()
                    L.run('Arm' if target else 'Disarm', lambda: L.set_armed(target))

                def on_override() -> None:
                    target = bool(L.bridge.pilot_override())
                    L.run('Autopilot control ' + ('on' if target else 'off'),
                          lambda: L.set_autopilot(target))

                arm_btn.on_click(on_arm)
                ovr_btn.on_click(on_override)

            # Shutdown
            with ui.card().classes('w-full'):
                ui.label('Shut down').classes('text-subtitle1 text-weight-medium')
                with ui.row().classes('w-full gap-2'):
                    ui.button('Stop all', icon='stop', color='negative',
                              on_click=lambda: background_tasks.create(L.stop_all())) \
                        .tooltip('Stop everything this launcher started')
                    kill_btn = ui.button('Kill all ROS processes', icon='dangerous') \
                        .props('outline color=negative') \
                        .tooltip('Also stops ROS nodes started from terminals')

                with ui.dialog() as kill_dialog, ui.card().classes('w-[40rem] max-w-full'):
                    ui.label('Kill all ROS processes?').classes('text-h6')
                    kill_list = ui.column().classes('mono w-full gap-0 max-h-80 overflow-auto')
                    with ui.row().classes('w-full justify-end'):
                        ui.button('Cancel', on_click=kill_dialog.close).props('flat')
                        ui.button('Kill', color='negative', on_click=lambda: confirm_kill())

                def open_kill_dialog() -> None:
                    exclude = {L.bridge.proc.pid} if L.bridge.running else set()
                    procs = find_ros_processes(exclude)
                    kill_list.clear()
                    with kill_list:
                        if not procs:
                            ui.label('No ROS processes found (launched processes are stopped too).')
                        for p in procs:
                            ui.label(f'{p.pid}  {p.cmdline}').classes('ellipsis w-full') \
                                .tooltip(p.cmdline)
                    kill_dialog.open()

                def confirm_kill() -> None:
                    kill_dialog.close()
                    background_tasks.create(L.kill_all())

                kill_btn.on_click(open_kill_dialog)

            # Status
            with ui.card().classes('w-full'):
                ui.label('Status').classes('text-subtitle1 text-weight-medium')
                proc_rows = {}
                for name in ('sim', 'autopilot'):
                    with ui.row().classes('w-full items-center no-wrap'):
                        ui.label(LOGS[name]).classes('w-24')
                        state = ui.badge()
                        ui.space()
                        stop = ui.button(icon='stop', on_click=lambda n=name: background_tasks.create(
                            L.pm.stop(n))).props('flat dense round').tooltip(f'Stop {LOGS[name]}')
                        proc_rows[name] = (state, stop)
                with ui.row().classes('w-full gap-2'):
                    armed_badge = ui.badge()
                    control_badge = ui.badge()
                    failsafe_badge = ui.badge()
                nodes_label = ui.label().classes('text-caption')

        # Sim display
        with ui.column().classes('grow min-w-0 gap-1 max-lg:w-full'):
            with ui.row().classes('w-full items-center'):
                ui.label('Sim display').classes('text-subtitle1 text-weight-medium')
                ui.space()
                open_link = ui.link('Open in new tab', '#', new_tab=True).classes('text-caption')
            frame = ui.element('iframe').classes('w-full h-[70vh] rounded-borders') \
                .style('border: 1px solid #ccc')

    # Logs
    log_views = {}
    with ui.tabs().classes('w-full') as tabs:
        for key, label in LOGS.items():
            ui.tab(key, label)
    with ui.tab_panels(tabs, value='launcher').classes('w-full'):
        for key in LOGS:
            with ui.tab_panel(key):
                log_views[key] = [ui.log(max_lines=2000).classes('mono w-full h-72'), 0]

    def refresh() -> None:
        for key, view in log_views.items():
            lines, view[1] = L.pm.output(key).since(view[1])
            for line in lines:
                view[0].push(line)

        busy.text = f'Running: {L.busy}…' if L.busy else ''
        running = L.any_running()
        idle = L.busy is None
        sim_up = L.pm.running('sim')
        for el in (vehicle, rc, firmware):
            el.set_enabled(not running and idle)
        mission.set_enabled(idle)
        p = L.profile
        fw_note.text = (f'Firmware settings for {p.airframe}: '
                        + ('saved' if p.firmware_saved else 'not saved yet'))

        run_btn.set_enabled(idle)
        sim_btn.set_enabled(idle and not sim_up)
        fw_btn.set_enabled(idle and sim_up)
        ap_btn.set_enabled(idle and not L.pm.running('autopilot'))
        ms_btn.set_enabled(idle and L.pm.running('autopilot'))
        reset_btn.set_enabled(idle and sim_up)
        sim_rc = L.rc_mode == 'sim'
        arm_btn.set_visibility(sim_rc)
        ovr_btn.set_visibility(sim_rc)
        vimfly_hint.set_visibility(not sim_rc)
        have_status = L.bridge.status_fresh()
        arm_btn.set_enabled(idle and sim_up and have_status)
        ovr_btn.set_enabled(idle and sim_up and have_status)
        arm_btn.text = 'Disarm' if L.bridge.armed() else 'Arm'
        ovr_btn.text = ('Give control to pilot' if L.bridge.pilot_override() is False
                        else 'Give control to autopilot')

        for name, (state, stop) in proc_rows.items():
            proc = L.pm.processes.get(name)
            if proc is None:
                state.text, color = 'not started', 'grey'
            elif proc.running:
                state.text, color = 'running', 'positive'
            else:
                state.text, color = f'exited ({proc.returncode})', 'warning'
            state.props(f'color={color}')
            stop.set_enabled(proc is not None and proc.running)

        text, color = badge_props(L.bridge.armed(), 'ARMED', 'disarmed')
        armed_badge.text = text
        armed_badge.props(f'color={"negative" if color == "positive" else color}')
        controller = L.bridge.controller()
        control_badge.text = {None: 'control: n/a', 'autopilot': 'control: autopilot',
                              'pilot': 'control: pilot (override switch)',
                              'rc': 'control: RC (no autopilot commands)'}[controller]
        control_badge.props(f'color={"grey" if controller is None else "primary"}')
        failsafe = L.bridge.status['failsafe'] if have_status else None
        failsafe_badge.set_visibility(bool(failsafe))
        failsafe_badge.text = 'FAILSAFE'
        failsafe_badge.props('color=negative')
        nodes_label.text = (f'{len(L.bridge.nodes)} ROS nodes running' if L.bridge.running
                            else 'ROS bridge not running')
    ui.timer(0.5, refresh)

    # Point the display at noVNC on the host the browser used to reach this page.
    await ui.context.client.connected()
    js_url = json.dumps(DISPLAY_URL) if DISPLAY_URL else (
        f"location.protocol + '//' + location.hostname + ':{DISPLAY_PORT}"
        "/vnc.html?autoconnect=1&resize=scale'")
    ui.run_javascript(f'''
        const url = {js_url};
        getHtmlElement({frame.id}).src = url;
        getHtmlElement({open_link.id}).href = url;
    ''')


def main() -> None:
    # scripts/sim_launcher.sh starts us as a background job, which inherits SIGINT as ignored.
    # Installing a handler makes children start with the default action again, so the
    # launch files we start can be stopped cleanly with SIGINT.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    ui.run(host=HOST, port=PORT, title='ROSflight sim launcher', reload=False, show=False,
           favicon='✈', dark=None)


if __name__ in {'__main__', '__mp_main__'}:
    main()
