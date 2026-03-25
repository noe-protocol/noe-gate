#!/usr/bin/env bash
# ubuntu_setup.sh
#
# Run once on a fresh Ubuntu 22.04 install to prepare the noe_ros2_adapter build environment.
# Tested against: Ubuntu 22.04 LTS (jammy) — the official ROS2 Humble target.
#
# Usage:
#   chmod +x ubuntu_setup.sh
#   ./ubuntu_setup.sh
#
# After this script completes, run: ./build_and_verify.sh

set -euo pipefail

echo "=========================================="
echo "  Noe ROS2 adapter — Ubuntu 22.04 setup"
echo "=========================================="

# ── 1. System updates ──────────────────────────────────────────────────────────
echo ""
echo "[1/6] System update..."
sudo apt-get update -q
sudo apt-get upgrade -y -q

# ── 2. ROS2 Humble ────────────────────────────────────────────────────────────
echo ""
echo "[2/6] Installing ROS2 Humble..."
sudo apt-get install -y -q software-properties-common curl gnupg lsb-release

sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

sudo apt-get update -q
sudo apt-get install -y -q \
    ros-humble-ros-base \
    ros-humble-rclcpp \
    ros-humble-rclcpp-lifecycle \
    ros-humble-std-msgs \
    python3-colcon-common-extensions \
    python3-rosdep

# ── 3. Build tools + nlohmann ─────────────────────────────────────────────────
echo ""
echo "[3/6] Installing build tools and nlohmann-json3-dev..."
sudo apt-get install -y -q \
    build-essential \
    cmake \
    git \
    nlohmann-json3-dev

# ── 4. Rust toolchain ─────────────────────────────────────────────────────────
echo ""
echo "[4/6] Installing Rust stable..."
if ! command -v cargo &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --default-toolchain stable --profile minimal
    source "$HOME/.cargo/env"
else
    echo "  cargo already installed: $(cargo --version)"
fi

# ── 5. Clone repo (skip if already present) ───────────────────────────────────
echo ""
echo "[5/6] Repository..."
REPO_DIR="$HOME/noe_reference"
if [[ -d "$REPO_DIR/.git" ]]; then
    echo "  Repo already at $REPO_DIR — pulling latest..."
    git -C "$REPO_DIR" pull
else
    echo "  Cloning repo to $REPO_DIR"
    echo "  (Edit this URL to match your GitHub repo)"
    # git clone https://github.com/YOUR_USERNAME/noe_reference.git "$REPO_DIR"
    echo ""
    echo "  ⚠  Clone step skipped — set your GitHub URL above and re-run,"
    echo "      OR copy the repo manually to $REPO_DIR"
fi

# ── 6. Shell environment ──────────────────────────────────────────────────────
echo ""
echo "[6/6] Adding ROS2 + Cargo to shell..."
BASHRC="$HOME/.bashrc"
grep -qxF 'source /opt/ros/humble/setup.bash' "$BASHRC" \
    || echo 'source /opt/ros/humble/setup.bash' >> "$BASHRC"
grep -qxF 'source "$HOME/.cargo/env"' "$BASHRC" \
    || echo 'source "$HOME/.cargo/env"' >> "$BASHRC"

echo ""
echo "=========================================="
echo "  Setup complete."
echo ""
echo "  Next steps:"
echo "    source ~/.bashrc"
echo "    cd $REPO_DIR"
echo "    ./build_and_verify.sh"
echo "=========================================="
