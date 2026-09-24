# ROSflight simulation devcontainer.
#
# Uses the same base image as the official ROSflight Docker setup
# (osrf/ros:${ROS_DISTRO}-desktop). The ROS distro is configurable via the
# ROS_DISTRO build arg (set it in .devcontainer/devcontainer.json). Default is
# Humble, which is the only distro that supports Gazebo Classic.
#
# This Dockerfile only installs system/ROS tooling and creates the user. The AI
# coding agents (Claude Code, Codex) and the ROS workspace build happen at
# container-create time in .devcontainer/setup.sh, because the workspace source
# is bind-mounted and is not available during the image build.

ARG ROS_DISTRO=humble
FROM osrf/ros:${ROS_DISTRO}-desktop

# Re-declare after FROM so the value is available in later layers, and export it
# as an env var so setup.sh / setup_workspace.sh can read $ROS_DISTRO.
ARG ROS_DISTRO=humble
ENV ROS_DISTRO=${ROS_DISTRO}
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

ARG USERNAME=rosflight
ARG USER_UID=1000
ARG USER_GID=1000

# System + ROS dev tools. Mirrors the ROSflight docker image
# (ros-dev-tools, plotjuggler, zsh, vim) and adds the reference template's
# developer conveniences (git, curl, tmux, ripgrep, ...).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ros-dev-tools \
        ros-${ROS_DISTRO}-plotjuggler-ros \
        git \
        curl \
        wget \
        sudo \
        zsh \
        vim \
        tmux \
        ripgrep \
        ca-certificates \
        locales \
    && rm -rf /var/lib/apt/lists/*

# Virtual X display for the sim GUIs, viewed in a browser via noVNC
# (scripts/sim_display.sh). Xvfb renders with Mesa llvmpipe, which provides
# modern OpenGL; needed on macOS, where XQuartz only offers OpenGL 1.4 and RViz
# fails to start.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        fluxbox \
        x11-utils \
        mesa-utils \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching the ROSflight conventions (bash login shell; zsh is
# still installed and usable, it is just not the default. dialout/plugdev for
# USB flight-controller access). Passwordless sudo so setup.sh can run
# rosdep/apt at container-create time.
RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --create-home --shell /bin/bash ${USERNAME} \
    && usermod -aG sudo,dialout,plugdev ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

# The runtime user is selected by "remoteUser" in devcontainer.json. The image
# is intentionally left as root here so devcontainer features (node, github-cli)
# install cleanly on top of this image.
