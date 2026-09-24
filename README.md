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
same thing.

You'll also want an X11 server on the host (standard on Linux) for the GUI sim
tools.

## Running a simulation

From the workspace root (open a fresh shell so ROS is sourced, or
`source install/setup.bash`):

```bash
ros2 launch rosflight_sim multirotor_standalone.launch.py             # RViz standalone
ros2 launch rosflight_sim fixedwing_standalone.launch.py use_vimfly:=true

# Gazebo Classic — Humble only:
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

New shells set `DISPLAY=:99` automatically when the host display is unusable,
so `ros2 launch ...` windows show up there. Manage the display with
`bash scripts/sim_display.sh [start|stop|restart|status]`. Rendering happens on
the CPU, so expect it to be slower than native (especially on Apple Silicon,
where the container runs under x86 emulation).


## Troubleshooting

Frankly, just ask any capable AI agent for help. As of July 2026 this will probably be more effective than outdated instructions in this README.md. 

You can point your AI agent to the instructions on the [project website](https://docs.rosflight.org/latest/user-guide/overview/) for context.

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
