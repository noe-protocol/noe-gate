#!/usr/bin/env python3
# launch/mobile_robot_zone_entry.launch.py
#
# Launches NoeGateNode for the mobile robot zone entry scenario.
#
# Lifecycle: TimerAction + ExecuteProcess (CLI shim).
# No lifecycle event matchers — works on all Humble point releases.
#
# Usage:
#   ros2 launch noe_ros2_adapter mobile_robot_zone_entry.launch.py
#
# Manual lifecycle control (alternative):
#   ros2 lifecycle set /noe_gate_node configure
#   ros2 lifecycle set /noe_gate_node activate

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, TimerAction
from launch_ros.actions import LifecycleNode


def generate_launch_description():
    # Resolve config path from the installed package share directory.
    # This works correctly whether launched from source or an installed overlay.
    pkg_share = get_package_share_directory('noe_ros2_adapter')
    config_file = os.path.join(pkg_share, 'config', 'zone_entry.yaml')

    noe_gate_node = LifecycleNode(
        package='noe_ros2_adapter',
        executable='noe_gate_node',
        name='noe_gate_node',
        namespace='',
        output='screen',
        parameters=[config_file],
    )

    # Run CLI configure/activate after fixed delays.
    # 2s gives the node time to start before configure is attempted.
    # 4s gives configure time to complete before activate is attempted.
    configure_cmd = ExecuteProcess(
        cmd=['ros2', 'lifecycle', 'set', '/noe_gate_node', 'configure'],
        output='screen',
    )

    activate_cmd = ExecuteProcess(
        cmd=['ros2', 'lifecycle', 'set', '/noe_gate_node', 'activate'],
        output='screen',
    )

    return LaunchDescription([
        noe_gate_node,
        TimerAction(period=2.0, actions=[configure_cmd]),
        TimerAction(period=4.0, actions=[activate_cmd]),
    ])
