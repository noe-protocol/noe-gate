cmake_minimum_required(VERSION 3.16)
project(noe_ros2_adapter CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ─── Dependencies ──────────────────────────────────────────────────────────────

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(std_msgs REQUIRED)
find_package(nlohmann_json REQUIRED)

# ─── Rust / noe_core library ──────────────────────────────────────────────────
#
# NOE_CORE_LIB_DIR: path to the directory containing libnoe_core.a
# Defaults to <repo_root>/rust/noe_core/target/debug.
# Override with: colcon build --cmake-args -DNOE_CORE_LIB_DIR=/path/to/dir
#
# NOE_CORE_INCLUDE_DIR: path to noe_core.h
# Defaults to <repo_root>/rust/noe_core/include
#
# NOE_CPP_INCLUDE_DIR: path to noe.hpp
# Defaults to <repo_root>/rust/noe_core/cpp
#
# Build order: run 'cargo build' inside rust/noe_core BEFORE 'colcon build'.

# Resolve repo root relative to this CMakeLists.txt (two levels up from
# ros2_adapter/noe_ros2_adapter/CMakeLists.txt → repo root)
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

set(NOE_CORE_LIB_DIR
    "${REPO_ROOT}/rust/noe_core/target/debug"
    CACHE PATH "Directory containing libnoe_core.a (built by cargo build)")

set(NOE_CORE_INCLUDE_DIR
    "${REPO_ROOT}/rust/noe_core/include"
    CACHE PATH "Directory containing noe_core.h")

set(NOE_CPP_INCLUDE_DIR
    "${REPO_ROOT}/rust/noe_core/cpp"
    CACHE PATH "Directory containing noe.hpp")

# Fail loudly and clearly if the static library is missing.
set(NOE_CORE_LIB "${NOE_CORE_LIB_DIR}/libnoe_core.a")
if(NOT EXISTS "${NOE_CORE_LIB}")
  message(FATAL_ERROR
    "\n"
    "  libnoe_core.a not found at: ${NOE_CORE_LIB}\n"
    "\n"
    "  You must build the Rust library before running colcon build:\n"
    "    cd ${REPO_ROOT}/rust/noe_core && cargo build\n"
    "\n"
    "  To use a non-default path:\n"
    "    colcon build --cmake-args -DNOE_CORE_LIB_DIR=/your/path\n")
endif()

if(NOT EXISTS "${NOE_CORE_INCLUDE_DIR}/noe_core.h")
  message(FATAL_ERROR
    "noe_core.h not found in: ${NOE_CORE_INCLUDE_DIR}\n"
    "Check NOE_CORE_INCLUDE_DIR.")
endif()

message(STATUS "noe_core: using library ${NOE_CORE_LIB}")
message(STATUS "noe_core: include dirs ${NOE_CORE_INCLUDE_DIR}, ${NOE_CPP_INCLUDE_DIR}")

# ─── Target ───────────────────────────────────────────────────────────────────

add_executable(noe_gate_node
    src/main.cpp
    src/noe_gate_node.cpp
    src/context_builder.cpp
)

target_include_directories(noe_gate_node PRIVATE
    include
    ${NOE_CORE_INCLUDE_DIR}
    ${NOE_CPP_INCLUDE_DIR}
)

# Static link noe_core.
# We use whole-archive / force_load to ensure all #[no_mangle] FFI symbols
# from the Rust crate are included even though they are not referenced by name
# from the C++ translation units at link time.
#
# On Linux, Rust's staticlib brings transitive runtime deps (dl, pthread, m)
# that are NOT self-describing to C/C++ linkers. They must be listed explicitly.
# build.rs cargo:rustc-link-lib does not propagate to CMake consumers.
if(APPLE)
    # macOS: -force_load includes all symbols from the specified archive.
    target_link_libraries(noe_gate_node
        -Wl,-force_load,${NOE_CORE_LIB}
        nlohmann_json::nlohmann_json
    )
else()
    # Linux (and other ELF platforms): --whole-archive / --no-whole-archive.
    # System libs after the archive: dl (dynamic loader), pthread, m (math).
    target_link_libraries(noe_gate_node
        -Wl,--whole-archive ${NOE_CORE_LIB} -Wl,--no-whole-archive
        nlohmann_json::nlohmann_json
        dl
        pthread
        m
    )
endif()

ament_target_dependencies(noe_gate_node
    rclcpp
    rclcpp_lifecycle
    std_msgs
)

# ─── Install ──────────────────────────────────────────────────────────────────

install(TARGETS noe_gate_node
    DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY launch config
    DESTINATION share/${PROJECT_NAME})

ament_package()
