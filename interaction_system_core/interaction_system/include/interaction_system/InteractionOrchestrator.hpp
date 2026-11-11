// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM__INTERACTION_ORCHESTRATOR_HPP_
#define INTERACTION_SYSTEM__INTERACTION_ORCHESTRATOR_HPP_

#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "std_msgs/msg/string.hpp"

#include "behavior_architecture/base_orchestrator.hpp"

#include "ros2_dialog_interfaces/action/dialog_task.hpp"
#include "interaction_system_interfaces/msg/interaction_system_status.hpp"
#include "interaction_system_interfaces/srv/ask_for_dialogue_behavior.hpp"
#include "interaction_system_interfaces/srv/ask_intelligence.hpp"

namespace interaction_system
{

const char INTERACTION_ORCHESTRATOR_NAME[] = "interaction_orchestrator";

/// The states of a request (the coordination layer of the interaction system)
enum class InteractionState
{
  IDLE = 0,
  SETTING_BEHAVIOR_ACTION = 1,  // deciding the dialogue behavior and whether the request can run
  SETTING_BEHAVIOR_INPUT = 2,   // writing the request to the blackboard
  ACTING = 3                    // the behavior's tree runs the dialogue
};

/// A dialogue behavior of the catalog (interaction_orchestrator_params.yaml)
struct DialogueBehavior
{
  int behavior_id;
  std::string name;
  std::string mode;
  std::string explanation;
  std::vector<std::string> needs;
  std::string node_name;  // its runner (interaction_behaviors_config.yaml)
};

/**
 * The coordination layer of the interaction system: the finite state machine of a request.
 *
 * Serves the /dialog/execute_task action. The first phase of every request decides its dialogue
 * behavior, before any conversation: the request names it with dialog_mode, or the interaction
 * intelligence chooses it from the task description over the whole catalog (or none fits); then
 * the intelligence checks, without the LLM, that the request carries the material of that
 * behavior. A request that no behavior fits, or that lacks the material, ends there (outcome
 * not_feasible, no turns). Otherwise the request is written to the blackboard and the behavior's
 * runner (its Behavior Tree) is activated; the orchestrator sends the feedback of the dialogue
 * on each utterance and the result when the tree ends. One dialogue at a time: another
 * request is rejected while one runs (one microphone, one voice). Publishes
 * /interaction_system/status.
 */
class InteractionOrchestrator : public behavior_architecture::BaseOrchestrator
{
public:
  using DialogTask = ros2_dialog_interfaces::action::DialogTask;
  using GoalHandle = rclcpp_action::ServerGoalHandle<DialogTask>;
  using Status = interaction_system_interfaces::msg::InteractionSystemStatus;
  using AskForDialogueBehavior = interaction_system_interfaces::srv::AskForDialogueBehavior;
  using AskIntelligence = interaction_system_interfaces::srv::AskIntelligence;

  explicit InteractionOrchestrator(BT::Blackboard::Ptr blackboard);

protected:
  void control_cycle() override;
  /// Activates the runner of the behavior with this id (the FSM state is not changed)
  void go_to_state(int behavior_id) override;

  rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn
  on_activate(const rclcpp_lifecycle::State & previous_state) override;
  rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn
  on_deactivate(const rclcpp_lifecycle::State & previous_state) override;

private:
  void parse_catalog();
  const DialogueBehavior * behavior_by_mode(const std::string & mode) const;
  std::string available_modes() const;

  // /dialog/execute_task
  rclcpp_action::GoalResponse handle_goal(
    const rclcpp_action::GoalUUID & uuid, std::shared_ptr<const DialogTask::Goal> goal);
  rclcpp_action::CancelResponse handle_cancel(std::shared_ptr<GoalHandle> goal_handle);
  void handle_accepted(std::shared_ptr<GoalHandle> goal_handle);

  // The states
  void setting_behavior_action();
  void setting_behavior_input();
  void acting();

  void write_request_to_blackboard();
  void publish_feedback();
  /// Ends the request: sends the result, tells the intelligence that the dialogue ended and
  /// deactivates the runner
  void finish(
    bool success, bool canceled, const std::string & failure_reason,
    const std::string & outcome = "");
  void publish_status();
  void behavior_status_callback(std_msgs::msg::String::UniquePtr msg);

  template<typename T>
  T bb(const std::string & key, const T & fallback) const
  {
    T value;
    return blackboard_->get(key, value) ? value : fallback;
  }

  rclcpp_action::Server<DialogTask>::SharedPtr action_server_;
  rclcpp_lifecycle::LifecyclePublisher<Status>::SharedPtr status_pub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr behavior_status_sub_;
  rclcpp::Client<AskForDialogueBehavior>::SharedPtr behavior_client_;
  rclcpp::Client<AskIntelligence>::SharedPtr intelligence_client_;

  std::map<int, DialogueBehavior> catalog_;
  bool catalog_parsed_ {false};

  // The request. The action callbacks and the control cycle run in different threads
  std::mutex goal_mutex_;
  std::shared_ptr<GoalHandle> goal_handle_;
  std::shared_ptr<GoalHandle> pending_goal_;

  InteractionState state_ {InteractionState::IDLE};
  int chosen_behavior_ {-1};
  bool behavior_request_sent_ {false};
  rclcpp::Client<AskForDialogueBehavior>::SharedFuture behavior_future_;
  std::chrono::steady_clock::time_point deadline_;
  bool acting_initialized_ {false};
  std::string runner_result_;
  int last_spoken_ {0};
  bool last_failed_ {false};
  std::string dialogue_id_;
};

}  // namespace interaction_system

#endif  // INTERACTION_SYSTEM__INTERACTION_ORCHESTRATOR_HPP_
