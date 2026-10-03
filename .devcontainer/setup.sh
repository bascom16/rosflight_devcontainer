#!/usr/bin/env bash
#
# postCreateCommand for the ROSflight Sim devcontainer.
#
# 1. Installs the AI coding agents (Claude Code + Codex), matching the
#    jusevitch/agent_devcontainer template, plus uv, Rust, tmux and Zellij.
# 2. Wires up ROS 2 + workspace sourcing for both bash (the default shell) and
#    zsh.
# 3. Hands off to scripts/setup_workspace.sh to clone the ROSflight repos and
#    build the workspace.
#
# Runs as the "rosflight" user. Idempotent: safe to re-run.

set -euo pipefail

# Resolve paths. postCreateCommand runs from the workspace folder, but derive it
# from this script's location to be robust.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROS_DISTRO="${ROS_DISTRO:-humble}"

log() { printf '\n\033[1;34m[setup.sh]\033[0m %s\n' "$*"; }

# --- Node / npm ---------------------------------------------------------------
# The node devcontainer feature installs Node via nvm under /usr/local/share/nvm
# and sources it from /etc/bash.bashrc + /etc/zsh/zshrc for interactive shells.
# postCreateCommand runs before that init, so source nvm here to get npm.
#
# Do NOT set NPM_CONFIG_PREFIX: nvm hard-refuses to run while it is set
# ("nvm is not compatible with the NPM_CONFIG_PREFIX environment variable") and
# drops node off PATH, which is what broke the Codex install. It is not needed
# either — the feature makes the nvm prefix writable by this user, so
# 'npm install -g' works as-is.
unset NPM_CONFIG_PREFIX
export NVM_DIR="${NVM_DIR:-/usr/local/share/nvm}"
if [ -s "${NVM_DIR}/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${NVM_DIR}/nvm.sh"
    nvm use --silent default >/dev/null 2>&1 || true
fi
# Fallback if nvm did not put node on PATH (e.g. a future feature layout change).
if ! command -v npm >/dev/null 2>&1 && [ -x "${NVM_DIR}/current/bin/npm" ]; then
    export PATH="${NVM_DIR}/current/bin:${PATH}"
fi
# A 'prefix' in ~/.npmrc conflicts with nvm the same way; strip it if present.
if [ -f "${HOME}/.npmrc" ]; then
    sed -i '/^prefix=/d;/^globalconfig=/d' "${HOME}/.npmrc" || true
fi
export PATH="${HOME}/.local/bin:${PATH}"

# --- Persist PATH additions for future shells --------------------------------
add_line() {
    # add_line <file> <line>: append <line> to <file> if not already present.
    local file="$1" line="$2"
    touch "${file}"
    grep -qsF -- "${line}" "${file}" || printf '%s\n' "${line}" >> "${file}"
}

for rc in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    # Earlier revisions of this script exported NPM_CONFIG_PREFIX here, which
    # breaks the system-wide nvm init in /etc/bash.bashrc and /etc/zsh/zshrc and
    # leaves interactive shells without node/npm. Drop it from existing rc files.
    [ -f "${rc}" ] && sed -i '/^export NPM_CONFIG_PREFIX=/d' "${rc}"
done
export PATH="${HOME}/.cargo/bin:${PATH}"

# --- Claude Code (native installer, same as the reference template) ----------
if ! command -v claude >/dev/null 2>&1; then
    log "Installing Claude Code..."
    curl -fsSL https://claude.ai/install.sh | bash || log "WARNING: Claude Code install failed (continuing)."
else
    log "Claude Code already installed; skipping."
fi

# --- Codex CLI (npm) ----------------------------------------------------------
if ! command -v codex >/dev/null 2>&1; then
    if ! command -v npm >/dev/null 2>&1; then
        log "WARNING: npm not found (nvm at ${NVM_DIR} did not provide node);"
        log "         skipping Codex. Install it later with 'npm install -g @openai/codex'."
    else
        log "Installing Codex CLI (npm $(npm --version), node $(node --version))..."
        npm install -g @openai/codex --loglevel=error --no-fund --no-audit \
            || log "WARNING: Codex install failed (continuing)."
    fi
else
    log "Codex CLI already installed; skipping."
fi

# --- uv (Python package/environment manager) ---------------------------------
if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || log "WARNING: uv install failed (continuing)."
    # uv installs to ~/.local/bin, which is already on PATH and persisted above.
else
    log "uv already installed; skipping."
fi
# Provide a uv-managed CPython (independent of the system/ROS Python).
if command -v uv >/dev/null 2>&1; then
    uv python install 3.12 || log "WARNING: 'uv python install 3.12' failed (continuing)."
fi

# --- Sim launcher (browser GUI, tools/sim_launcher) ---------------------------
# Its own uv environment (Python 3.12 + NiceGUI), separate from ROS's Python.
# Started by postStartCommand via scripts/sim_launcher.sh.
if command -v uv >/dev/null 2>&1; then
    log "Syncing the sim launcher's Python environment..."
    (unset PYTHONPATH VIRTUAL_ENV; uv sync --quiet --project "${WS_ROOT}/tools/sim_launcher") \
        || log "WARNING: sim launcher 'uv sync' failed (continuing)."
fi

# --- Rust (rustup toolchain) --------------------------------------------------
if ! command -v rustc >/dev/null 2>&1; then
    log "Installing Rust (rustup)..."
    # --no-modify-path: the PATH line is persisted above, alongside the others.
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --default-toolchain stable --profile default \
        || log "WARNING: Rust install failed (continuing)."
else
    log "Rust already installed; skipping."
fi

# --- tmux ---------------------------------------------------------------------
# Normally already present from the Dockerfile; installed here too so this
# script is self-sufficient if the base image ever drops it.
if ! command -v tmux >/dev/null 2>&1; then
    log "Installing tmux..."
    sudo apt-get update -qq \
        && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux \
        || log "WARNING: tmux install failed (continuing)."
else
    log "tmux already installed; skipping."
fi

# --- Zellij (terminal multiplexer) -------------------------------------------
# Prebuilt static binary from GitHub releases; building from source with
# 'cargo install zellij' works too but takes many minutes at container create.
if ! command -v zellij >/dev/null 2>&1; then
    log "Installing Zellij..."
    case "$(uname -m)" in
        x86_64)          ZELLIJ_ARCH="x86_64-unknown-linux-musl" ;;
        aarch64 | arm64) ZELLIJ_ARCH="aarch64-unknown-linux-musl" ;;
        *)               ZELLIJ_ARCH="" ;;
    esac
    if [ -n "${ZELLIJ_ARCH}" ]; then
        ZELLIJ_URL="https://github.com/zellij-org/zellij/releases/latest/download/zellij-${ZELLIJ_ARCH}.tar.gz"
        mkdir -p "${HOME}/.local/bin"
        if curl -fsSL "${ZELLIJ_URL}" | tar -xz -C "${HOME}/.local/bin" zellij; then
            chmod +x "${HOME}/.local/bin/zellij"
        else
            log "WARNING: Zellij download failed (continuing)."
        fi
    else
        log "WARNING: no Zellij release for $(uname -m); skipping."
    fi
else
    log "Zellij already installed; skipping."
fi

# --- Shell config: aliases + vim ---------------------------------------------
if [ -f "${SCRIPT_DIR}/.bash_aliases" ]; then
    cp "${SCRIPT_DIR}/.bash_aliases" "${HOME}/.bash_aliases"
    add_line "${HOME}/.bashrc" '[ -f "$HOME/.bash_aliases" ] && . "$HOME/.bash_aliases"'
fi

if [ ! -f "${HOME}/.vimrc" ]; then
    cat > "${HOME}/.vimrc" <<'VIMRC'
syntax on
set number
set background=dark
set tabstop=4 shiftwidth=4 expandtab
set autoindent
set splitright splitbelow
" Treat ROS launch/world files as XML
autocmd BufRead,BufNewFile *.launch,*.world set filetype=xml
VIMRC
fi

# --- ROS 2 + workspace sourcing, PATH and display ----------------------------
# bash: everything goes in ~/.rosflight_env.bash, which is sourced from the TOP
# of ~/.bashrc. Ubuntu's stock ~/.bashrc returns early for non-interactive
# shells, so anything appended at the bottom is invisible to `bash -lc '...'`
# (e.g. `devsy workspace exec`, or an AI agent running commands) and ROS and
# cargo would not be found there. The workspace source is guarded so shells
# don't error before the first colcon build.
ROSFLIGHT_ENV="${HOME}/.rosflight_env.bash"
cat > "${ROSFLIGHT_ENV}" <<ENVFILE
# Generated by .devcontainer/setup.sh; sourced from the top of ~/.bashrc.
export PATH="\$HOME/.npm-global/bin:\$HOME/.local/bin:\$HOME/.cargo/bin:\$PATH"
# Fall back to the virtual display (scripts/sim_display.sh) when the host
# display is unusable, e.g. on macOS where DISPLAY is a host-only launchd path.
if ! timeout 1 xdpyinfo >/dev/null 2>&1 && DISPLAY=:99 timeout 1 xdpyinfo >/dev/null 2>&1; then export DISPLAY=:99; fi
source /opt/ros/${ROS_DISTRO}/setup.bash
[ -f "${WS_ROOT}/install/setup.bash" ] && source "${WS_ROOT}/install/setup.bash"
# Temporary fix for running ROS in Docker (matches ROSflight image).
ulimit -n 1024
ENVFILE
BASHRC_HOOK='[ -f "$HOME/.rosflight_env.bash" ] && . "$HOME/.rosflight_env.bash"'
if ! grep -qsF -- "${BASHRC_HOOK}" "${HOME}/.bashrc"; then
    touch "${HOME}/.bashrc"
    { printf '%s\n' "${BASHRC_HOOK}"; cat "${HOME}/.bashrc"; } > "${HOME}/.bashrc.new" \
        && mv "${HOME}/.bashrc.new" "${HOME}/.bashrc"
fi
# Drop the lines earlier revisions of this script appended to the bottom of
# ~/.bashrc; the env file above replaces them.
sed -i -e '\|^export PATH="\$HOME/.npm-global/bin:\$HOME/.local/bin:\$PATH"$|d' \
       -e '\|^export PATH="\$HOME/.cargo/bin:\$PATH"$|d' \
       -e '\|^if ! timeout 1 xdpyinfo|d' \
       -e '\|^source /opt/ros/.*/setup\.bash$|d' \
       -e '\|^\[ -f ".*/install/setup\.bash" \] && source |d' \
       -e '\|^ulimit -n 1024$|d' "${HOME}/.bashrc"

# zsh (not the default shell): the same, appended to ~/.zshrc.
add_line "${HOME}/.zshrc" 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"'
add_line "${HOME}/.zshrc" 'if ! timeout 1 xdpyinfo >/dev/null 2>&1 && DISPLAY=:99 timeout 1 xdpyinfo >/dev/null 2>&1; then export DISPLAY=:99; fi'
add_line "${HOME}/.zshrc" "source /opt/ros/${ROS_DISTRO}/setup.zsh"
add_line "${HOME}/.zshrc" "[ -f \"${WS_ROOT}/install/setup.zsh\" ] && source \"${WS_ROOT}/install/setup.zsh\""
add_line "${HOME}/.zshrc" "ulimit -n 1024"
# ROS 2 CLI autocompletion for zsh.
add_line "${HOME}/.zshrc" 'eval "$(register-python-argcomplete3 ros2)"'
add_line "${HOME}/.zshrc" 'eval "$(register-python-argcomplete3 colcon)"'

# Interactive shells print the sim launcher's URL (and start it if it isn't
# running). ~/.bashrc returns early for non-interactive shells before this line,
# and ~/.zshrc is only read by interactive ones, so scripts stay quiet.
LAUNCHER_BANNER="[ -f \"${WS_ROOT}/scripts/sim_launcher.sh\" ] && bash \"${WS_ROOT}/scripts/sim_launcher.sh\" banner"
add_line "${HOME}/.bashrc" "${LAUNCHER_BANNER}"
add_line "${HOME}/.zshrc" "${LAUNCHER_BANNER}"

# --- Workspace: clone repos + rosdep + colcon build --------------------------
# Non-fatal: a build failure should not abort container creation.
log "Setting up the ROSflight workspace (clone + rosdep + colcon build)..."
if bash "${WS_ROOT}/scripts/setup_workspace.sh"; then
    log "Workspace setup complete."
else
    log "WARNING: workspace setup reported an error. The container is still usable;"
    log "         re-run 'bash scripts/setup_workspace.sh' after resolving the issue."
fi

log "postCreate finished. Open a new shell (or 'source ~/.bashrc') to load ROS."
