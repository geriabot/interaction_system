cmake_minimum_required(VERSION 3.10)
project(interaction_system_behaviors)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(behaviortree_cpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(ros2_dialog_interfaces REQUIRED)
find_package(simple_hri_interfaces REQUIRED)
find_package(interaction_system_interfaces REQUIRED)

# The plugin library with every BT node of the interaction system
add_library(${PROJECT_NAME}_bt_plugins SHARED
  src/register_bt_plugins.cpp
  src/interaction_system_behaviors/AskIntelligence.cpp
  src/interaction_system_behaviors/Listen.cpp
  src/interaction_system_behaviors/Speak.cpp
  src/interaction_system_behaviors/WithFillers.cpp
)
target_include_directories(${PROJECT_NAME}_bt_plugins PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(${PROJECT_NAME}_bt_plugins
  rclcpp::rclcpp
  rclcpp_action::rclcpp_action
  behaviortree_cpp::behaviortree_cpp
  ${std_msgs_TARGETS}
  ${ros2_dialog_interfaces_TARGETS}
  ${simple_hri_interfaces_TARGETS}
  ${interaction_system_interfaces_TARGETS}
)
target_compile_definitions(${PROJECT_NAME}_bt_plugins PRIVATE BT_PLUGIN_EXPORT)

install(TARGETS ${PROJECT_NAME}_bt_plugins
  EXPORT export_${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY behavior_tree_xml DESTINATION share/${PROJECT_NAME})

ament_export_targets(export_${PROJECT_NAME} HAS_LIBRARY_TARGET)
ament_export_dependencies(rclcpp rclcpp_action behaviortree_cpp std_msgs
  ros2_dialog_interfaces simple_hri_interfaces interaction_system_interfaces)
ament_package()
