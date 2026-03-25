// src/main.cpp — entry point for noe_gate_node executable

#include <memory>
#include <rclcpp/rclcpp.hpp>
#include "noe_ros2_adapter/noe_gate_node.hpp"

int main(int argc, char* argv[]) {
    rclcpp::init(argc, argv);
    auto node = std::make_shared<noe_adapter::NoeGateNode>();
    rclcpp::spin(node->get_node_base_interface());
    rclcpp::shutdown();
    return 0;
}
