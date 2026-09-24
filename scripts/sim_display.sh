#!/usr/bin/env bash
#
# sim_display.sh — virtual X display for the sim GUIs, viewed in a browser.
#
# Runs Xvfb (with Mesa llvmpipe software OpenGL) + a window manager + x11vnc +
# noVNC, so RViz/Gazebo/PlotJuggler work without an X server on the host. This
# is required on macOS: XQuartz only offers OpenGL 1.4 over indirect GLX, and
# RViz's Ogre renderer needs at least 1.5.
#
# Usage:
#   bash scripts/sim_display.sh [start|stop|restart|status]   (default: start)
#
# Then open http://localhost:6080/vnc.html?autoconnect=1&resize=scale
# and launch GUI apps with DISPLAY=:99 (new shells pick this up automatically
# when the host display is unusable; see .devcontainer/setup.sh).
#
# Environment variables:
#   SIM_DISPLAY          X display number (default: :99)
#   SIM_DISPLAY_SIZE     screen geometry (default: 1600x900x24)
#   SIM_DISPLAY_PORT     noVNC web port (default: 6080)
#
# Idempotent: 'start' leaves already-running components alone.

set -euo pipefail

SIM_DISPLAY="${SIM_DISPLAY:-:99}"
SIM_DISPLAY_SIZE="${SIM_DISPLAY_SIZE:-1600x900x24}"
SIM_DISPLAY_PORT="${SIM_DISPLAY_PORT:-6080}"
VNC_PORT=5900
LOG_DIR="/tmp/sim_display"

log() { printf '\033[1;32m[sim_display]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sim_display]\033[0m %s\n' "$*"; }

# is_running <pgrep -f pattern>
is_running() { pgrep -f "$1" >/dev/null 2>&1; }

display_up() { DISPLAY="${SIM_DISPLAY}" timeout 2 xdpyinfo >/dev/null 2>&1; }

# spawn <name> <cmd...>: start a detached background process logging to LOG_DIR.
spawn() {
    local name="$1"; shift
    setsid nohup "$@" >"${LOG_DIR}/${name}.log" 2>&1 < /dev/null &
}

start() {
    for bin in Xvfb x11vnc websockify fluxbox; do
        command -v "${bin}" >/dev/null 2>&1 || {
            warn "'${bin}' not found; rebuild the container (see .devcontainer/Dockerfile)."
            exit 1
        }
    done
    mkdir -p "${LOG_DIR}"

    if ! display_up; then
        log "Starting Xvfb on ${SIM_DISPLAY} (${SIM_DISPLAY_SIZE})"
        spawn xvfb Xvfb "${SIM_DISPLAY}" -screen 0 "${SIM_DISPLAY_SIZE}" +extension GLX -nolisten tcp
        for _ in $(seq 1 20); do display_up && break; sleep 0.25; done
        display_up || { warn "Xvfb failed to start; see ${LOG_DIR}/xvfb.log"; exit 1; }
    fi

    is_running "^fluxbox" || DISPLAY="${SIM_DISPLAY}" spawn fluxbox fluxbox

    if ! is_running "x11vnc -display ${SIM_DISPLAY}"; then
        spawn x11vnc x11vnc -display "${SIM_DISPLAY}" -forever -shared -nopw \
            -localhost -rfbport "${VNC_PORT}" -quiet
    fi

    if ! is_running "websockify.* ${SIM_DISPLAY_PORT} "; then
        spawn novnc websockify --web /usr/share/novnc "${SIM_DISPLAY_PORT}" "localhost:${VNC_PORT}"
    fi

    log "Virtual display ready. View it at:"
    log "  http://localhost:${SIM_DISPLAY_PORT}/vnc.html?autoconnect=1&resize=scale"
    log "GUI apps need DISPLAY=${SIM_DISPLAY} (current shell: DISPLAY=${DISPLAY:-<unset>})."
}

stop() {
    pkill -f "websockify.* ${SIM_DISPLAY_PORT} " || true
    pkill -f "x11vnc -display ${SIM_DISPLAY}" || true
    pkill -f "^fluxbox" || true
    pkill -f "Xvfb ${SIM_DISPLAY}" || true
    log "Virtual display stopped."
}

status() {
    local name pattern
    for entry in "Xvfb|Xvfb ${SIM_DISPLAY}" "fluxbox|^fluxbox" \
                 "x11vnc|x11vnc -display ${SIM_DISPLAY}" "noVNC|websockify.* ${SIM_DISPLAY_PORT} "; do
        name="${entry%%|*}"; pattern="${entry#*|}"
        if is_running "${pattern}"; then log "${name}: running"; else warn "${name}: stopped"; fi
    done
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    *) echo "Usage: $0 [start|stop|restart|status]" >&2; exit 2 ;;
esac
