cmake_minimum_required(VERSION 3.10)
project(interaction_system)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_lifecycle REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(lifecycle_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(behaviortree_cpp REQUIRED)
find_package(behavior_architecture REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(ros2_dialog_interfaces REQUIRED)
find_package(interaction_system_interfaces REQUIRED)

# The orchestrator, a plugin of behavior_architecture's mission_executor (loaded by name from
# config/interaction_behaviors_config.yaml)
add_library(${PROJECT_NAME} SHARED
  src/interaction_system/InteractionOrchestrator.cpp
)
target_include_directories(${PROJECT_NAME} PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
  ${behavior_architecture_INCLUDE_DIRS})
target_link_libraries(${PROJECT_NAME}
  ${behavior_architecture_LIBRARIES}
  rclcpp::rclcpp
  rclcpp_lifecycle::rclcpp_lifecycle
  rclcpp_action::rclcpp_action
  behaviortree_cpp::behaviortree_cpp
  nlohmann_json::nlohmann_json
  ${lifecycle_msgs_TARGETS}
  ${std_msgs_TARGETS}
  ${ros2_dialog_interfaces_TARGETS}
  ${interaction_system_interfaces_TARGETS}
)

install(TARGETS ${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})

ament_package()
