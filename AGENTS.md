# ROSflight Simulation Workspace

This is a ROS 2 workspace and [devcontainer](https://containers.dev/) for
running **ROSflight** simulations, with AI coding agents (Claude Code, Codex)
preinstalled. It works with any tool implementing the Dev Container spec
([Devsy](https://devsy.sh/) with the Docker provider is the recommended
default, but the VS Code Dev Containers extension, the `devcontainer` CLI, and
Codespaces work too). It is modeled on the
[`jusevitch/agent_devcontainer`](https://github.com/jusevitch/agent_devcontainer)
template and follows the official
[ROSflight sim install docs](https://docs.rosflight.org/latest/user-guide/installation/installation-sim/).

## Project structure

- `.devcontainer/` — container definition
  - `Dockerfile` — `osrf/ros:${ROS_DISTRO}-desktop` base + ROS/dev tooling + non-root `rosflight` user
  - `devcontainer.json` — build args, features (Node, GitHub CLI), X11/networking, extensions, `postCreateCommand`
  - `setup.sh` — post-create: installs Claude Code + Codex, wires ROS sourcing, runs the workspace setup
  - `.bash_aliases` — git + colcon shortcuts
- `.claude/settings.json` — Claude Code runs with `bypassPermissions` inside the container
- `scripts/setup_workspace.sh` — clones the ROSflight repos, runs `rosdep`, builds with `colcon`
- `src/` — ROS 2 packages (cloned here; gitignored)
  - `rosflight_ros_pkgs` — core ROS stack: `rosflight_io`, `rosflight_sim`, `rosflight_msgs`, and the `rosflight_firmware` submodule (SIL)
  - `rosplane` — fixed-wing autopilot (`rosplane_sim`)
  - `roscopter` — multirotor autopilot (`roscopter_sim`)

## ROS distribution

- ROS 2 distro is configurable via the **`ROS_DISTRO` build arg** in
  `.devcontainer/devcontainer.json` (default: **`humble`**, Ubuntu 22.04).
- Gazebo Classic only works on **Humble**. On Jazzy, use the standalone (RViz)
  or HoloOcean sims.

## Building

Always source ROS 2 (and the workspace, once built) before running commands.
From the workspace root:

```bash
source /opt/ros/${ROS_DISTRO}/setup.bash   # ROS_DISTRO defaults to humble
colcon build --symlink-install
source install/setup.bash
```

The devcontainer runs this automatically on creation via
`scripts/setup_workspace.sh`. To rebuild a single package:

```bash
colcon build --symlink-install --packages-select <package_name>
```

If memory is constrained: `colcon build --executor sequential`.

## Running simulations

Run from the workspace root with the workspace sourced.

```bash
# Standalone (RViz) sim — works on all supported distros
ros2 launch rosflight_sim multirotor_standalone.launch.py
ros2 launch rosflight_sim fixedwing_standalone.launch.py
# Add keyboard manual control (VimFly):
ros2 launch rosflight_sim multirotor_standalone.launch.py use_vimfly:=true

# Gazebo Classic sim — Humble only
source /usr/share/gazebo/setup.sh
ros2 launch rosflight_sim multirotor_gazebo.launch.py

# Autopilot sims
ros2 launch rosplane_sim sim.launch.py      # fixed-wing
ros2 launch roscopter_sim sim.launch.py     # multirotor
```

GUI apps (RViz, Gazebo, PlotJuggler) display on the host over X11. If windows do
not appear, run `xhost +local:docker` on the host.

## Included tools

- **AI coding agents:** Claude Code (Anthropic), Codex CLI (OpenAI)
- **ROS 2** (`humble` by default) + `ros-dev-tools`, `plotjuggler`, `colcon`, `rosdep`
- **Dev tools:** Node.js, uv (Python package/env manager, with a managed Python 3.12), Rust (rustup), GitHub CLI, git, tmux, Zellij, ripgrep, vim

The default shell is **bash**. zsh is still installed if you prefer it, but note
that colcon's `install/setup.bash` cannot be sourced from zsh (it relies on
`$BASH_SOURCE`) — under zsh use `install/setup.zsh` instead.

## Conventions

- The three `src/` repos are cloned, not vendored — do not commit their contents
  to this repo. Edit them in place; each has its own upstream git history.
- Keep `scripts/setup_workspace.sh` idempotent (it must be safe to re-run).
- `rosflight_firmware` is a git submodule of `rosflight_ros_pkgs`.
