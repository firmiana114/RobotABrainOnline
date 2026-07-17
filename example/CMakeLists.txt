cmake_minimum_required(VERSION 3.16)
project(unitree_slam_example_new LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

get_filename_component(PROJECT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
set(PROJECTS_ROOT "/mnt/ssd/navgation/projects" CACHE PATH "Root directory that contains unitree_sdk2")
set(UNITREE_SDK2_ROOT "${PROJECTS_ROOT}/unitree_sdk2" CACHE PATH "unitree_sdk2 source/install directory")
set(CUSTOM_ACTION_WS "${PROJECT_ROOT}/custom_action_ws" CACHE PATH "custom_action_ws workspace directory")
set(CUSTOM_ACTION_INSTALL "${CUSTOM_ACTION_WS}/install" CACHE PATH "custom_action_ws install directory")

if(NOT EXISTS "${UNITREE_SDK2_ROOT}/include")
    message(FATAL_ERROR "unitree_sdk2 include directory not found: ${UNITREE_SDK2_ROOT}/include")
endif()
if(NOT EXISTS "${CUSTOM_ACTION_INSTALL}/setup.bash")
    message(FATAL_ERROR "custom_action_ws install setup not found: ${CUSTOM_ACTION_INSTALL}/setup.bash")
endif()

if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
    set(UNITREE_ARCH "aarch64")
else()
    set(UNITREE_ARCH "x86_64")
endif()

set(UNITREE_SDK2_LIB "${UNITREE_SDK2_ROOT}/lib/${UNITREE_ARCH}/libunitree_sdk2.a")
set(UNITREE_DDS_LIB_DIR "${UNITREE_SDK2_ROOT}/thirdparty/lib/${UNITREE_ARCH}")
set(UNITREE_DDSC_LIB "${UNITREE_DDS_LIB_DIR}/libddsc.so")
set(UNITREE_DDSCXX_LIB "${UNITREE_DDS_LIB_DIR}/libddscxx.so")
if(EXISTS "${CUSTOM_ACTION_INSTALL}/lib")
    set(CUSTOM_ACTION_LIB_DIR "${CUSTOM_ACTION_INSTALL}/lib")
else()
    set(CUSTOM_ACTION_LIB_DIR "${CUSTOM_ACTION_INSTALL}/custom_action_interfaces/lib")
endif()

foreach(required_file IN ITEMS "${UNITREE_SDK2_LIB}" "${UNITREE_DDSC_LIB}" "${UNITREE_DDSCXX_LIB}")
    if(NOT EXISTS "${required_file}")
        message(FATAL_ERROR "Required Unitree library not found: ${required_file}")
    endif()
endforeach()

list(PREPEND CMAKE_PREFIX_PATH "${CUSTOM_ACTION_INSTALL}")

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(custom_action_interfaces REQUIRED)
find_package(Eigen3 REQUIRED)
find_package(Threads REQUIRED)

add_library(unitree_ddsc SHARED IMPORTED GLOBAL)
set_target_properties(unitree_ddsc PROPERTIES
    IMPORTED_LOCATION "${UNITREE_DDSC_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${UNITREE_SDK2_ROOT}/thirdparty/include"
    IMPORTED_NO_SONAME TRUE
)

add_library(unitree_ddscxx SHARED IMPORTED GLOBAL)
set_target_properties(unitree_ddscxx PROPERTIES
    IMPORTED_LOCATION "${UNITREE_DDSCXX_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${UNITREE_SDK2_ROOT}/thirdparty/include;${UNITREE_SDK2_ROOT}/thirdparty/include/ddscxx"
    INTERFACE_LINK_LIBRARIES "unitree_ddsc;Threads::Threads"
    IMPORTED_NO_SONAME TRUE
)

add_library(unitree_sdk2 STATIC IMPORTED GLOBAL)
set_target_properties(unitree_sdk2 PROPERTIES
    IMPORTED_LOCATION "${UNITREE_SDK2_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${UNITREE_SDK2_ROOT}/include"
    INTERFACE_LINK_LIBRARIES "unitree_ddsc;unitree_ddscxx;Threads::Threads"
    LINKER_LANGUAGE CXX
)

set(CMAKE_BUILD_RPATH "${UNITREE_DDS_LIB_DIR};/opt/ros/humble/lib;${CUSTOM_ACTION_LIB_DIR}")
set(CMAKE_INSTALL_RPATH "${UNITREE_DDS_LIB_DIR};/opt/ros/humble/lib;${CUSTOM_ACTION_LIB_DIR}")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)

function(configure_runtime_executable target_name)
    set(real_name "${target_name}_bin")
    set_target_properties(${target_name} PROPERTIES
        OUTPUT_NAME "${real_name}"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    )
    target_include_directories(${target_name} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include")

    set(wrapper_path "${CMAKE_BINARY_DIR}/${target_name}")
    file(GENERATE OUTPUT "${wrapper_path}" CONTENT "#!/usr/bin/env bash\nset -eo pipefail\n\nSCRIPT_DIR=\"\$(cd \"\$(dirname \"\${BASH_SOURCE[0]}\")\" && pwd)\"\nROS_SETUP=\"/opt/ros/humble/setup.bash\"\nCUSTOM_ACTION_SETUP=\"${CUSTOM_ACTION_INSTALL}/setup.bash\"\n\nset +u\nsource \"\${ROS_SETUP}\"\nsource \"\${CUSTOM_ACTION_SETUP}\"\nset -u\n\nexport LD_LIBRARY_PATH=\"${UNITREE_DDS_LIB_DIR}:/opt/ros/humble/lib:${CUSTOM_ACTION_LIB_DIR}:\${LD_LIBRARY_PATH:-}\"\nexec \"\${SCRIPT_DIR}/${real_name}\" \"\$@\"\n")
    add_custom_command(TARGET ${target_name} POST_BUILD
        COMMAND /bin/chmod +x "${wrapper_path}"
    )
endfunction()

function(link_common target_name)
    target_include_directories(${target_name} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include")
    target_link_libraries(${target_name} unitree_sdk2 Eigen3::Eigen)
endfunction()

function(link_ros target_name)
    ament_target_dependencies(${target_name}
        rclcpp
        rclcpp_action
        std_msgs
        geometry_msgs
        custom_action_interfaces
    )
endfunction()

add_executable(keyDemo src/keyDemo.cpp)
link_common(keyDemo)
configure_runtime_executable(keyDemo)

add_library(g1_actions STATIC src/actions/g1_actions.cpp)
target_include_directories(g1_actions PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

add_executable(goGoalNavigation src/goGoalNavigation_ros2.cpp)
link_common(goGoalNavigation)
link_ros(goGoalNavigation)
configure_runtime_executable(goGoalNavigation)

add_executable(goGoalNavigation66 src/goGoalNavigation_ros2_66.cpp)
link_common(goGoalNavigation66)
link_ros(goGoalNavigation66)
configure_runtime_executable(goGoalNavigation66)

add_executable(gestureTopicBridge src/gesture_topic_bridge.cpp)
link_common(gestureTopicBridge)
link_ros(gestureTopicBridge)
configure_runtime_executable(gestureTopicBridge)

add_executable(g1Arm7DIYActionExample src/g1_arm7__diy_action_example.cpp)
link_common(g1Arm7DIYActionExample)
link_ros(g1Arm7DIYActionExample)
target_link_libraries(g1Arm7DIYActionExample g1_actions)
configure_runtime_executable(g1Arm7DIYActionExample)

add_executable(g1ArmOfficialActionServer src/g1_arm_official_action_server.cpp)
link_common(g1ArmOfficialActionServer)
link_ros(g1ArmOfficialActionServer)
configure_runtime_executable(g1ArmOfficialActionServer)

add_dependencies(goGoalNavigation g1Arm7DIYActionExample)

install(TARGETS
    keyDemo
    goGoalNavigation
    goGoalNavigation66
    gestureTopicBridge
    g1Arm7DIYActionExample
    g1ArmOfficialActionServer
    RUNTIME DESTINATION bin
)
