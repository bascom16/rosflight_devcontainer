# ROSflight Devcontainer

A [devcontainer](https://containers.dev/) for developing and running
[ROSflight](https://rosflight.org) simulations, with AI coding agents
(Claude Code, Codex CLI) preinstalled.

Everything lives in a standard `.devcontainer/` configuration, so it works with
any tool that implements the
[Dev Container specification](https://containers.dev/implementors/spec/) —
Devsy, the VS Code Dev Containers extension, the `devcontainer` CLI, GitHub
Codespaces, and others. **[Devsy](https://devsy.sh/) with the Docker provider is
the recommended default.**

## Quick start (Devsy)

(Skip steps 1–3 if you've done this before)

1. [Install Docker](https://docs.docker.com/engine/install/)
2. [Install the Devsy CLI](https://devsy.sh/docs/getting-started/install) — on macOS/Linux:
```bash
brew install devsy-org/homebrew-tap/devsy
```
3. Add Docker to Devsy as the default provider:
```bash
devsy provider add docker
devsy provider use docker
```
4. Clone this repository and `cd` into it
5. Run `devsy workspace up . --ide vscode`

This launches a VS Code window connected to a container with ROS 2 and
ROSflight installed. On first launch the workspace is cloned and built with
`colcon build` — **the first build takes several minutes.**

With VS Code's Dev Containers extension instead, "Reopen in Container" does the
same thing; see the next section.

### Opening it in VS Code (one click)

If you use VS Code, the easiest way in is the
[Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers):

1. Open this folder in VS Code (`code .`).
2. Click **Reopen in Container** in the popup, or run **Dev Containers: Reopen in
   Container** from the command palette. The first time builds the image and the
   workspace, which takes several minutes (watch the progress in the terminal that
   opens).
3. From then on, reopen it with **File → Open Recent** and pick the entry labelled
   `[Dev Container]`. VS Code starts the container for you if it is stopped
   (Docker must be running).

Use the integrated terminal to run the sims. VS Code forwards the container's
ports to your Mac automatically, so the browser display needs no extra setup;
check the **Ports** panel for the address of port 6080 (usually
`localhost:6080`, or another port such as 6081 if 6080 is taken). Ignore port
5900: that is the raw VNC port, not a web page.

> **Use either Devsy or VS Code for a given checkout, not both at once.** Both
> start a container that uses host networking, ROS DDS discovery, port 6080 and
> the shared X11 display `:99`, so a second container ends up sharing (or fighting
> over) the first one's virtual display. Stop one before starting the other
> (`devsy workspace stop <workspace-name>`, or stop the container in Docker
> Desktop / the VS Code **Dev Containers: Stop Container**). They do share the
> checkout's `build/` and `install/`, which is fine.

You'll also want an X11 server on the host (standard on Linux) for the GUI sim
tools.

## Running a simulation

From the workspace root (open a fresh shell so ROS is sourced, or
`source install/setup.bash`):

```bash
ros2 launch rosflight_sim multirotor_standalone.launch.py             # RViz standalone
ros2 launch rosflight_sim fixedwing_standalone.launch.py use_vimfly:=true

# Gazebo Classic — Humble on amd64 only (see "CPU architecture" below):
source /usr/share/gazebo/setup.sh
ros2 launch rosflight_sim multirotor_gazebo.launch.py

ros2 launch rosplane_sim sim.launch.py     # ROSplane (fixed-wing)
ros2 launch roscopter_sim sim.launch.py    # ROScopter (multirotor)
```

If GUI windows don't appear, run the following on the **host computer** (not in
the container): `xhost +local:docker`.

### Viewing the GUI in a browser (macOS, or no host X server)

On macOS, RViz can't run through XQuartz: XQuartz only offers OpenGL 1.4 to
containers, and RViz needs at least 1.5. Instead, the container runs a virtual
display with software OpenGL (Xvfb + noVNC). It starts automatically each time
the container starts. Open it in your browser:

**http://localhost:6080/vnc.html?autoconnect=1&resize=scale**

With Docker Desktop on macOS the container's `--network=host` is the Docker VM's
network, so `localhost:6080` is **not** reachable from the Mac by default.
Forward the port and keep the command running while you use the page:

```bash
devsy workspace ssh <workspace-name> -L 6080:localhost:6080
```

(`devsy workspace list` shows the workspace name, e.g. `arm64-native-rosflight`.
Your IDE's port forwarding does the same thing if you connect with one.)

New shells set `DISPLAY=:99` automatically when the host display is unusable,
so `ros2 launch ...` windows show up there. Manage the display with
`bash scripts/sim_display.sh [start|stop|restart|status]`. Rendering happens on
the CPU, so expect it to be slower than native (much slower if the container
runs as amd64 under emulation on Apple Silicon; see below).


## Troubleshooting

**The first build keeps going after `devsy workspace up` returns.** Devsy runs
`postCreateCommand` (tools, `rosdep`, `colcon build`) inside the container after
it has already reported "ready", and does not show that output. Follow it with:

```bash
devsy workspace exec <workspace-name> -- tail -f /tmp/rosflight_setup.log
```

The workspace is built once `install/.build_arch` exists. A failed step (e.g.
`rosdep`) is logged as an error there, and the container is still usable;
re-run `bash scripts/setup_workspace.sh` after fixing it. In non-interactive
shells (`devsy workspace exec <name> -- bash -lc '...'`) ROS and the workspace
are sourced too, via `~/.rosflight_env.bash`.

Frankly, just ask any capable AI agent for help. As of July 2026 this will probably be more effective than outdated instructions in this README.md. 

You can point your AI agent to the instructions on the [project website](https://docs.rosflight.org/latest/user-guide/overview/) for context.

## CPU architecture (Apple Silicon)

The image builds natively for whichever architecture your Docker runs: arm64
on Apple Silicon Macs, amd64 on most Linux/Windows machines. Native arm64 is
much faster than emulation and supports everything set up here (ROS 2, the
standalone/RViz sims, ROSplane, ROScopter, PlotJuggler, firmware toolchains).

The exception is **Gazebo Classic**, which has no arm64 packages. If you need
it on an Apple Silicon Mac, run the container as amd64 under Rosetta
emulation (slower):

```bash
ROSFLIGHT_PLATFORM=linux/amd64 devsy workspace up . --ide vscode --platform linux/amd64 --recreate
```

`ROSFLIGHT_PLATFORM` is what makes the *image build* use amd64 (it becomes the
`TARGET_PLATFORM` build arg in `devcontainer.json`). Devsy's `--platform` flag
only applies to `docker run`: on its own it fails with `exit status 125`,
because the image Devsy built is still arm64. To go back to native arm64, run
`devsy workspace up . --ide vscode --recreate` without the variable.

Switching architectures needs a clean workspace build;
`scripts/setup_workspace.sh` detects a `build/`/`install/` from the other
architecture and removes it automatically.

## Changing the ROS distribution

Edit the single build arg in [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json):

```jsonc
"build": { "dockerfile": "Dockerfile", "args": { "ROS_DISTRO": "humble" } }
```

Set it to `jazzy` for ROS 2 Jazzy (Ubuntu 24.04), then rebuild the container.
**Note:** Gazebo Classic is EOL and does **not** work on Jazzy — only the
standalone (RViz) and HoloOcean sims are available there.


## Additional included features

- **Claude Code** and the **Codex CLI**
  - Run `claude` or `codex` to launch these.
  - Claude Code runs with permission prompts bypassed (safe within containers);
    edit `.claude/settings.json` to change this.
- **uv**, plus a uv-managed **Python 3.12**
- **Rust** (rustup, stable toolchain)
- **tmux** and **Zellij**



## Layout

```
.
├── .devcontainer/   # Dockerfile, devcontainer.json, setup.sh, .bash_aliases
├── .claude/         # Claude Code settings (bypassPermissions)
├── scripts/         # setup_workspace.sh
├── src/             # ROSflight repos (cloned by the setup script; gitignored)
├── AGENTS.md        # guidance for AI coding agents
└── CLAUDE.md        # imports AGENTS.md
```

## Resources

- [Dev Container Specification](https://containers.dev/implementors/spec/)
- [Devsy Documentation](https://devsy.sh/docs)
- [ROSflight Documentation](https://docs.rosflight.org/latest/)
