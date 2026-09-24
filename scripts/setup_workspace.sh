#!/usr/bin/env bash
#
# setup_workspace.sh — set up the ROSflight ROS 2 workspace.
#
# Creates the src/ folder structure, clones the required ROSflight repositories
# (idempotently — existing clones are left untouched), installs dependencies
# with rosdep, and builds the workspace with colcon.
#
# Usage:
#   bash scripts/setup_workspace.sh
#
# Environment variables:
#   ROS_DISTRO           ROS 2 distro to source (default: humble)
#   ROSFLIGHT_SKIP_BUILD if set to 1, clone + rosdep only (skip colcon build)
#   ROSFLIGHT_BUILD_WORKERS  packages colcon builds in parallel (default: 2)
#   ROSFLIGHT_BUILD_JOBS     compile jobs per package, i.e. make -j (default:
#                            ~1 per 2 GB of RAM, capped at the CPU count). The
#                            default keeps the build from running out of memory
#                            in small Docker VMs such as Docker Desktop on macOS.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROS_DISTRO="${ROS_DISTRO:-humble}"
ROSFLIGHT_SKIP_BUILD="${ROSFLIGHT_SKIP_BUILD:-0}"

log() { printf '\n\033[1;32m[setup_workspace]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[setup_workspace]\033[0m %s\n' "$*"; }

# Repositories to clone into src/. Format: "<dir> <url> <extra-git-args>".
# Add or remove lines here to change which ROSflight packages are set up.
REPOS=(
    "rosflight_ros_pkgs https://github.com/rosflight/rosflight_ros_pkgs --recursive"
    "rosplane          https://github.com/rosflight/rosplane"
    "roscopter         https://github.com/rosflight/roscopter"
)

# --- 1. Folder structure ------------------------------------------------------
log "Ensuring workspace structure at ${WS_ROOT}"
mkdir -p "${WS_ROOT}/src"
cd "${WS_ROOT}/src"

# --- 2. Clone repos (idempotent) ---------------------------------------------
for entry in "${REPOS[@]}"; do
    # shellcheck disable=SC2086
    set -- ${entry}
    dir="$1"; url="$2"; shift 2 || true
    extra_args=("$@")
    if [ -d "${dir}/.git" ]; then
        log "Repo '${dir}' already present; skipping clone."
        # Make sure submodules (e.g. rosflight_firmware) are initialized.
        git -C "${dir}" submodule update --init --recursive || \
            warn "Could not update submodules for '${dir}'."
    else
        log "Cloning ${url} -> src/${dir}"
        git clone "${extra_args[@]}" "${url}" "${dir}"
    fi
done

cd "${WS_ROOT}"

# --- 3. Source ROS 2 ----------------------------------------------------------
if [ ! -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]; then
    warn "ROS distro '${ROS_DISTRO}' not found at /opt/ros/${ROS_DISTRO}. Aborting build steps."
    exit 1
fi
# NOTE: the ament/colcon setup scripts read undefined variables (e.g.
# AMENT_TRACE_SETUP_FILES), so they abort under `set -u`. Disable nounset
# across the source, then restore it.
set +u
# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO}/setup.bash"
set -u

# --- 4. rosdep dependencies ---------------------------------------------------
log "Installing dependencies with rosdep..."
# The base image clears /var/lib/apt/lists, so apt cannot resolve any package
# name until the lists are refreshed. rosdep shells out to `apt-get install`,
# so without this it fails with "Unable to locate package ...".
sudo apt-get update
# rosdep init errors harmlessly if already initialized.
sudo rosdep init >/dev/null 2>&1 || true
rosdep update
rosdep install --from-paths src --ignore-src --rosdistro "${ROS_DISTRO}" -y

# --- 5. Build -----------------------------------------------------------------
if [ "${ROSFLIGHT_SKIP_BUILD}" = "1" ]; then
    log "ROSFLIGHT_SKIP_BUILD=1 set; skipping colcon build."
else
    # Limit build parallelism by available memory. An unbounded colcon build
    # starts roughly one compiler per CPU per package, which is killed by the
    # OOM killer in small VMs (e.g. Docker Desktop's ~8 GB default on macOS).
    mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
    cgroup_max="$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo max)"
    if [[ "${cgroup_max}" =~ ^[0-9]+$ ]] && (( cgroup_max / 1024 < mem_kb )); then
        mem_kb=$(( cgroup_max / 1024 ))
    fi
    default_jobs=$(( mem_kb / (2 * 1024 * 1024) ))
    (( default_jobs > $(nproc) )) && default_jobs="$(nproc)"
    (( default_jobs < 1 )) && default_jobs=1
    ROSFLIGHT_BUILD_JOBS="${ROSFLIGHT_BUILD_JOBS:-${default_jobs}}"
    ROSFLIGHT_BUILD_WORKERS="${ROSFLIGHT_BUILD_WORKERS:-2}"

    log "Building the workspace with colcon (this can take several minutes)..."
    log "Parallelism: ${ROSFLIGHT_BUILD_WORKERS} packages x ${ROSFLIGHT_BUILD_JOBS} jobs ($(( mem_kb / 1024 / 1024 )) GB RAM detected)"
    MAKEFLAGS="-j${ROSFLIGHT_BUILD_JOBS}" \
        colcon build --symlink-install --parallel-workers "${ROSFLIGHT_BUILD_WORKERS}"
fi

# --- Done ---------------------------------------------------------------------
cat <<EOF

$(log "Workspace ready.")
Next steps (from ${WS_ROOT}):
  source install/setup.bash

Run a simulation, e.g.:
  ros2 launch rosflight_sim multirotor_standalone.launch.py            # RViz standalone sim
  ros2 launch rosflight_sim fixedwing_standalone.launch.py use_vimfly:=true
  ros2 launch rosflight_sim multirotor_gazebo.launch.py                # Gazebo Classic (Humble only)
  ros2 launch rosplane_sim sim.launch.py                               # ROSplane sim
  ros2 launch roscopter_sim sim.launch.py                              # ROScopter sim
EOF
