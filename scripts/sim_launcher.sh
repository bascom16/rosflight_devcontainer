#!/usr/bin/env bash
#
# sim_launcher.sh — browser GUI for starting/stopping ROSflight sims and missions.
#
# Serves tools/sim_launcher (a NiceGUI app in its own uv environment) at
# http://localhost:8090. It embeds the noVNC sim display from scripts/sim_display.sh.
#
# Usage:
#   bash scripts/sim_launcher.sh [start|stop|restart|status|banner]   (default: start)
#
# 'banner' prints the URL (clickable in VS Code's terminal); if the launcher is
# not running it starts it in the background first. ~/.bashrc runs it for every
# interactive shell (see .devcontainer/setup.sh).
#
# Environment variables:
#   SIM_LAUNCHER_PORT    web port (default: 8090)
#   SIM_LAUNCHER_HOST    bind address (default: 127.0.0.1; host networking makes
#                        0.0.0.0 reachable from the host's network)
#   SIM_DISPLAY_URL      noVNC URL to embed (default: same host as the page, port 6080)
#
# Idempotent: 'start' does nothing if the launcher is already running.
# 'stop' also stops the sims the launcher started.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${WS_ROOT}/tools/sim_launcher"
SIM_LAUNCHER_PORT="${SIM_LAUNCHER_PORT:-8090}"
LOG_DIR="/tmp/sim_launcher"
PATTERN="^${APP_DIR}/.venv/bin/python -m sim_launcher.app"

log() { printf '\033[1;32m[sim_launcher]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sim_launcher]\033[0m %s\n' "$*"; }

is_running() { pgrep -f "${PATTERN}" >/dev/null 2>&1; }

start() {
    if is_running; then
        log "Already running at http://localhost:${SIM_LAUNCHER_PORT}"
        return
    fi
    export PATH="${HOME}/.local/bin:${PATH}"
    command -v uv >/dev/null 2>&1 || { warn "'uv' not found; run .devcontainer/setup.sh."; exit 1; }
    mkdir -p "${LOG_DIR}"

    # The app runs on uv's Python 3.12; keep ROS's Python 3.10 paths out of it.
    # (Its ROS children re-source ROS themselves.)
    unset PYTHONPATH VIRTUAL_ENV
    uv sync --quiet --project "${APP_DIR}" || { warn "uv sync failed; see above."; exit 1; }

    cd "${APP_DIR}"
    SIM_LAUNCHER_PORT="${SIM_LAUNCHER_PORT}" setsid nohup "${APP_DIR}/.venv/bin/python" -m sim_launcher.app \
        >"${LOG_DIR}/app.log" 2>&1 < /dev/null &

    for _ in $(seq 1 40); do
        curl -fs -o /dev/null "http://127.0.0.1:${SIM_LAUNCHER_PORT}/" && break
        is_running || break
        sleep 0.25
    done
    if curl -fs -o /dev/null "http://127.0.0.1:${SIM_LAUNCHER_PORT}/"; then
        log "Sim launcher ready: http://localhost:${SIM_LAUNCHER_PORT}"
    else
        warn "Sim launcher failed to start; see ${LOG_DIR}/app.log"
        exit 1
    fi
}

stop() {
    if is_running; then
        # SIGTERM lets the app stop the sims it launched before exiting. (Not SIGINT:
        # the app was started as a background job, which ignores it until uvicorn starts.)
        pkill -TERM -f "${PATTERN}" || true
        for _ in $(seq 1 60); do is_running || break; sleep 0.25; done
        is_running && pkill -KILL -f "${PATTERN}" || true
    fi
    log "Sim launcher stopped."
}

status() {
    if is_running; then
        log "running: http://localhost:${SIM_LAUNCHER_PORT} (log: ${LOG_DIR}/app.log)"
    else
        warn "stopped"
    fi
}

banner() {
    if is_running; then
        log "Sim launcher: http://localhost:${SIM_LAUNCHER_PORT}"
    else
        # Don't hold up the new shell; start() waits for the server to come up.
        (start >/dev/null 2>&1 &)
        log "Starting the sim launcher: http://localhost:${SIM_LAUNCHER_PORT} (log: ${LOG_DIR}/app.log)"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    banner)  banner ;;
    *) echo "Usage: $0 [start|stop|restart|status|banner]" >&2; exit 2 ;;
esac
