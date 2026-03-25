# Dockerfile for noe_ros2_adapter build validation
#
# Target: Ubuntu 22.04 + ROS2 Humble (official ROS base image)
#
# Usage:
#   # From repo root:
#   docker build -t noe_ros2_build -f ros2_adapter/Dockerfile .
#   docker run --rm noe_ros2_build
#
# What this does:
#   1. Install ROS2 Humble, colcon, nlohmann-json3-dev, Rust toolchain
#   2. cargo build (produce libnoe_core.a)
#   3. colcon build --packages-select noe_ros2_adapter
#   4. Run the zone-entry scenario via manual topic publication
#   5. Print decisions.jsonl and exit

FROM ros:humble-ros-base-jammy

SHELL ["/bin/bash", "-c"]

# ── System dependencies ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    curl \
    ca-certificates \
    python3-colcon-common-extensions \
    python3-rosdep \
    ros-humble-rclcpp \
    ros-humble-rclcpp-lifecycle \
    ros-humble-std-msgs \
    nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Rust toolchain ─────────────────────────────────────────────────────────────
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

# ── Copy repo ──────────────────────────────────────────────────────────────────
WORKDIR /noe
COPY . .

# ── Build Rust library ─────────────────────────────────────────────────────────
RUN cd rust/noe_core && cargo build 2>&1 | tail -5

# ── Build ROS2 adapter ─────────────────────────────────────────────────────────
RUN source /opt/ros/humble/setup.bash \
    && cd ros2_adapter \
    && colcon build \
        --packages-select noe_ros2_adapter \
        --cmake-args \
            -DNOE_CORE_LIB_DIR=/noe/rust/noe_core/target/debug \
            -DNOE_CORE_INCLUDE_DIR=/noe/rust/noe_core/include \
            -DNOE_CPP_INCLUDE_DIR=/noe/rust/noe_core/cpp \
            -DCMAKE_BUILD_TYPE=Debug \
    2>&1

# ── Verification entrypoint ────────────────────────────────────────────────────
# Runs a background node, publishes two scenarios, then exits.
COPY ros2_adapter/docker_verify.sh /noe/docker_verify.sh
RUN chmod +x /noe/docker_verify.sh

ENTRYPOINT ["/bin/bash", "/noe/docker_verify.sh"]
