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
    # ~/.npm-global/bin stays on PATH so anything installed there previously
    # keeps working; new global installs go to the nvm prefix.
    add_line "${rc}" 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"'
    add_line "${rc}" 'export PATH="$HOME/.cargo/bin:$PATH"'
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

# --- ROS 2 + workspace sourcing ----------------------------------------------
# bash uses setup.bash, zsh uses setup.zsh. Guard the workspace source so shells
# don't error before the first colcon build.
setup_ros_sourcing() {
    local rc="$1" ext="$2"
    add_line "${rc}" "source /opt/ros/${ROS_DISTRO}/setup.${ext}"
    add_line "${rc}" "[ -f \"${WS_ROOT}/install/setup.${ext}\" ] && source \"${WS_ROOT}/install/setup.${ext}\""
    # Temporary fix for running ROS in Docker (matches ROSflight image).
    add_line "${rc}" "ulimit -n 1024"
}
setup_ros_sourcing "${HOME}/.bashrc" "bash"
setup_ros_sourcing "${HOME}/.zshrc" "zsh"
# ROS 2 CLI autocompletion for zsh.
add_line "${HOME}/.zshrc" 'eval "$(register-python-argcomplete3 ros2)"'
add_line "${HOME}/.zshrc" 'eval "$(register-python-argcomplete3 colcon)"'

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
