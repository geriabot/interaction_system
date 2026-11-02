// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#include "interaction_system/InteractionOrchestrator.hpp"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "lifecycle_msgs/msg/state.hpp"
#include "nlohmann/json.hpp"

#include "behavior_architecture/orchestrator_factory.hpp"

namespace interaction_system
{

using std::placeholders::_1;
using std::placeholders::_2;
using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;
using namespace std::chrono_literals;

static behavior_architecture::OrchestratorRegistrar<InteractionOrchestrator>
interaction_orchestrator_registrar(INTERACTION_ORCHESTRATOR_NAME);

namespace
{
// Longest wait for the interaction intelligence to appear when a request needs it
constexpr auto INTELLIGENCE_WAIT = 10s;

std::string trim(const std::string & text)
{
  const auto begin = text.find_first_not_of(" \t\n\r");
  if (begin == std::string::npos) {
    return "";
  }
  const auto end = text.find_last_not_of(" \t\n\r");
  return text.substr(begin, end - begin + 1);
}

// A limit of the request, or the system's value when the request leaves it unset (negative)
template<typename T>
T limit_or_default(T requested, T system_value)
{
  return requested < 0 ? system_value : requested;
}

std::string uuid_to_string(const rclcpp_action::GoalUUID & uuid)
{
  std::ostringstream out;
  for (const auto byte : uuid) {
    out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
  }
  return out.str();
}
}  // namespace

InteractionOrchestrator::InteractionOrchestrator(BT::Blackboard::Ptr blackboard)
: behavior_architecture::BaseOrchestrator(INTERACTION_ORCHESTRATOR_NAME, blackboard)
{
  declare_parameter<double>("wait_timeout", 15.0);
  declare_parameter<double>("max_silence", 90.0);
  declare_parameter<int>("max_stalled", 4);
  declare_parameter<int>("max_turns", 0);
  declare_parameter<int>("max_attempts", 3);
  declare_parameter<int>("max_off_task", 6);
  declare_parameter<double>("tts_timeout", 60.0);
  declare_parameter<double>("intelligence_timeout", 30.0);
  declare_parameter<double>("reply_budget", 20.0);
  declare_parameter<double>("opening_budget", 30.0);
  declare_parameter<std::string>("user_speaking_topic", "/user_speaking");
  declare_parameter<double>("filler_after_s", 2.0);
  declare_parameter<double>("filler_repeat_s", 8.0);

  status_pub_ = create_publisher<Status>("/interaction_system/status", 10);
  behavior_status_sub_ = create_subscription<std_msgs::msg::String>(
    "/behavior_status", 10,
    std::bind(&InteractionOrchestrator::behavior_status_callback, this, _1));
  behavior_client_ = create_client<AskForDialogueBehavior>(
    "/interaction/ask_intelligence_for_behavior");
  intelligence_client_ = create_client<AskIntelligence>("/interaction/ask_intelligence");
}

// ==== CATALOG ====

void
InteractionOrchestrator::parse_catalog()
{
  if (catalog_parsed_) {
    return;
  }
  const auto names = declare_parameter<std::vector<std::string>>(
    "dialogue_behaviors.behaviors", std::vector<std::string>{});
  for (const auto & name : names) {
    const std::string prefix = "dialogue_behaviors." + name + ".";
    DialogueBehavior behavior;
    behavior.name = name;
    behavior.behavior_id = declare_parameter<int>(prefix + "behavior_id", -1);
    behavior.mode = declare_parameter<std::string>(prefix + "mode", "");
    behavior.explanation = declare_parameter<std::string>(prefix + "explanation", "");
    behavior.node_name = declare_parameter<std::string>(prefix + "node_name", "");
    for (const auto & need : declare_parameter<std::vector<std::string>>(
        prefix + "needs", std::vector<std::string>{""}))
    {
      if (!need.empty()) {
        behavior.needs.push_back(need);
      }
    }
    if (behavior.behavior_id < 0 || behavior.mode.empty() || behavior.node_name.empty()) {
      RCLCPP_ERROR(get_logger(), "Dialogue behavior %s is incomplete: ignored", name.c_str());
      continue;
    }
    std::string needs;
    for (const auto & need : behavior.needs) {
      needs += (needs.empty() ? "" : ", ") + need;
    }
    RCLCPP_INFO(
      get_logger(), "Dialogue behavior %d: %s (%s), needs [%s], runner %s",
      behavior.behavior_id, name.c_str(), behavior.mode.c_str(), needs.c_str(),
      behavior.node_name.c_str());
    catalog_[behavior.behavior_id] = behavior;
  }
  catalog_parsed_ = true;
}

const DialogueBehavior *
InteractionOrchestrator::behavior_by_mode(const std::string & mode) const
{
  for (const auto & [id, behavior] : catalog_) {
    if (behavior.mode == mode) {
      return &behavior;
    }
  }
  return nullptr;
}

std::string
InteractionOrchestrator::available_modes() const
{
  std::vector<std::string> modes;
  for (const auto & [id, behavior] : catalog_) {
    modes.push_back("'" + behavior.mode + "'");
  }
  std::sort(modes.begin(), modes.end());
  std::string text;
  for (const auto & mode : modes) {
    text += (text.empty() ? "" : ", ") + mode;
  }
  return "[" + text + "]";
}

// ==== LIFECYCLE ====

CallbackReturn
InteractionOrchestrator::on_activate(const rclcpp_lifecycle::State & previous_state)
{
  const auto result = BaseOrchestrator::on_activate(previous_state);
  if (result != CallbackReturn::SUCCESS) {
    return result;
  }
  parse_catalog();
  status_pub_->on_activate();
  state_ = InteractionState::IDLE;
  action_server_ = rclcpp_action::create_server<DialogTask>(
    this, "/dialog/execute_task",
    std::bind(&InteractionOrchestrator::handle_goal, this, _1, _2),
    std::bind(&InteractionOrchestrator::handle_cancel, this, _1),
    std::bind(&InteractionOrchestrator::handle_accepted, this, _1));
  RCLCPP_INFO(get_logger(), "Interaction orchestrator active: serving /dialog/execute_task");
  return CallbackReturn::SUCCESS;
}

CallbackReturn
InteractionOrchestrator::on_deactivate(const rclcpp_lifecycle::State & previous_state)
{
  if (goal_handle_ && goal_handle_->is_active()) {
    finish(false, false, "The interaction system was deactivated");
  }
  action_server_.reset();
  status_pub_->on_deactivate();
  return BaseOrchestrator::on_deactivate(previous_state);
}

// ==== /dialog/execute_task ====

rclcpp_action::GoalResponse
InteractionOrchestrator::handle_goal(
  const rclcpp_action::GoalUUID &, std::shared_ptr<const DialogTask::Goal> goal)
{
  std::lock_guard<std::mutex> lock(goal_mutex_);
  if (goal_handle_ || pending_goal_) {
    RCLCPP_WARN(get_logger(), "Rejecting new dialogue: another one is already running");
    return rclcpp_action::GoalResponse::REJECT;
  }
  RCLCPP_INFO(get_logger(), "New dialogue task: '%s'", goal->task_description.substr(0, 60).c_str());
  return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}

rclcpp_action::CancelResponse
InteractionOrchestrator::handle_cancel(std::shared_ptr<GoalHandle>)
{
  RCLCPP_INFO(get_logger(), "Cancel requested: stopping the current dialogue");
  return rclcpp_action::CancelResponse::ACCEPT;
}

void
InteractionOrchestrator::handle_accepted(std::shared_ptr<GoalHandle> goal_handle)
{
  std::lock_guard<std::mutex> lock(goal_mutex_);
  pending_goal_ = goal_handle;
}

// ==== THE STATE MACHINE ====

void
InteractionOrchestrator::control_cycle()
{
  try {
    switch (state_) {
      case InteractionState::IDLE:
        {
          std::lock_guard<std::mutex> lock(goal_mutex_);
          if (pending_goal_) {
            goal_handle_ = pending_goal_;
            pending_goal_.reset();
            dialogue_id_ = uuid_to_string(goal_handle_->get_goal_id());
            chosen_behavior_ = -1;
            behavior_request_sent_ = false;
            last_failed_ = false;
            deadline_ = std::chrono::steady_clock::now() + INTELLIGENCE_WAIT;
            state_ = InteractionState::SETTING_BEHAVIOR_ACTION;
          }
        }
        break;
      case InteractionState::SETTING_BEHAVIOR_ACTION:
        setting_behavior_action();
        break;
      case InteractionState::SETTING_BEHAVIOR_INPUT:
        setting_behavior_input();
        break;
      case InteractionState::ACTING:
        acting();
        break;
    }
    publish_status();
  } catch (const std::exception & e) {
    RCLCPP_ERROR(get_logger(), "Error in control_cycle: %s", e.what());
  }
}

void
InteractionOrchestrator::setting_behavior_action()
{
  if (goal_handle_->is_canceling()) {
    finish(false, true, "Cancelled");
    return;
  }
  const auto goal = goal_handle_->get_goal();
  const std::string mode = trim(goal->dialog_mode);

  // The request names the behavior: no question to the intelligence
  if (!mode.empty()) {
    const auto * behavior = behavior_by_mode(mode);
    if (!behavior) {
      finish(
        false, false, "Unknown dialog mode '" + mode + "'. Available: " + available_modes());
      return;
    }
    chosen_behavior_ = behavior->behavior_id;
    state_ = InteractionState::SETTING_BEHAVIOR_INPUT;
    return;
  }

  // The intelligence chooses among the behaviors whose needs the request provides
  if (!behavior_request_sent_) {
    if (!behavior_client_->service_is_ready()) {
      if (std::chrono::steady_clock::now() > deadline_) {
        finish(false, false, "The interaction intelligence is not available");
      }
      return;
    }
    auto request = std::make_shared<AskForDialogueBehavior::Request>();
    request->task_description = goal->task_description;
    request->domain = goal->domain;
    request->resources_json = goal->resources_json;
    for (const auto & [id, behavior] : catalog_) {
      interaction_system_interfaces::msg::DialogueBehaviorDetails details;
      details.behavior_id = static_cast<int8_t>(behavior.behavior_id);
      details.name = behavior.name;
      details.mode = behavior.mode;
      details.explanation = behavior.explanation;
      details.needs = behavior.needs;
      request->behaviors.push_back(details);
    }
    behavior_future_ = behavior_client_->async_send_request(request).future.share();
    behavior_request_sent_ = true;
    return;
  }

  if (behavior_future_.wait_for(0s) != std::future_status::ready) {
    return;
  }
  const auto response = behavior_future_.get();
  behavior_request_sent_ = false;
  if (!response->success) {
    finish(false, false, "Choosing the dialogue behavior failed: " + response->message);
    return;
  }
  const int chosen = response->behavior_id;
  const auto offered = std::find(
    response->offered_ids.begin(), response->offered_ids.end(), chosen);
  if (chosen < 0 || offered == response->offered_ids.end() || !catalog_.count(chosen)) {
    finish(false, false, "No dialogue behavior fits the request");
    return;
  }
  chosen_behavior_ = chosen;
  RCLCPP_INFO(get_logger(), "Dialogue behavior chosen: %s", catalog_[chosen].name.c_str());
  state_ = InteractionState::SETTING_BEHAVIOR_INPUT;
}

void
InteractionOrchestrator::setting_behavior_input()
{
  write_request_to_blackboard();
  acting_initialized_ = false;
  state_ = InteractionState::ACTING;
}

void
InteractionOrchestrator::write_request_to_blackboard()
{
  const auto goal = goal_handle_->get_goal();
  const auto & behavior = catalog_.at(chosen_behavior_);

  // The limits: the request's, or the system's when the request leaves them unset
  const int max_turns = limit_or_default<int>(
    goal->max_turns, static_cast<int>(get_parameter("max_turns").as_int()));
  const double max_silence = limit_or_default<double>(
    goal->max_silence, get_parameter("max_silence").as_double());
  const int max_stalled = limit_or_default<int>(
    goal->max_stalled, static_cast<int>(get_parameter("max_stalled").as_int()));
  const double filler_after_s = limit_or_default<double>(
    goal->filler_after_s, get_parameter("filler_after_s").as_double());
  const int max_attempts = limit_or_default<int>(
    goal->max_attempts, static_cast<int>(get_parameter("max_attempts").as_int()));
  const int max_off_task = limit_or_default<int>(
    goal->max_off_task, static_cast<int>(get_parameter("max_off_task").as_int()));
  // wait_timeout: 0 is not "never" here (silence is counted in waits of it): the system's value
  const double wait_timeout = goal->wait_timeout > 0.0 ?
    goal->wait_timeout : get_parameter("wait_timeout").as_double();
  const double reply_budget = limit_or_default<double>(
    goal->reply_budget, get_parameter("reply_budget").as_double());
  const double opening_budget = limit_or_default<double>(
    goal->opening_budget, get_parameter("opening_budget").as_double());
  const double filler_repeat_s = limit_or_default<double>(
    goal->filler_repeat_s, get_parameter("filler_repeat_s").as_double());
  // Silence is counted in waits of wait_timeout; 0 = the dialogue never ends on silence
  const int max_silent_waits = max_silence <= 0.0 ? 0 :
    std::max(1, static_cast<int>(std::ceil(max_silence / wait_timeout - 1e-9)));

  // The request as the intelligence receives it ("begin"), with the behavior's mode
  const nlohmann::json request = {
    {"task_description", goal->task_description},
    {"frame_schema_json", goal->frame_schema_json},
    {"initial_frame_json", goal->initial_frame_json},
    {"max_turns", max_turns},
    {"wait_timeout", wait_timeout},
    {"dialog_mode", behavior.mode},
    {"resources_json", goal->resources_json},
    {"domain", goal->domain},
    {"skip_intro", goal->skip_intro},
    {"acknowledgments", goal->acknowledgments},
    {"reasoning", goal->reasoning},
  };

  blackboard_->set("goal", request.dump());
  blackboard_->set("dialogue_id", dialogue_id_);
  blackboard_->set("dialogue", std::string());
  blackboard_->set("behavior", behavior.name);
  blackboard_->set("skip_intro", static_cast<bool>(goal->skip_intro));
  blackboard_->set("max_turns", max_turns);

  blackboard_->set("wait_timeout", wait_timeout);
  blackboard_->set("max_silent_waits", max_silent_waits);
  blackboard_->set("max_stalled", max_stalled);
  blackboard_->set("max_attempts", max_attempts);
  blackboard_->set("max_off_task", max_off_task);
  blackboard_->set("tts_timeout", get_parameter("tts_timeout").as_double());
  blackboard_->set("intelligence_timeout", get_parameter("intelligence_timeout").as_double());
  // The time the intelligence may spend asking the model for one reply (WithFillers starts it;
  // reply_deadline is when it ends, 0 while no reply is being prepared)
  blackboard_->set("reply_budget", reply_budget);
  blackboard_->set("opening_budget", opening_budget);
  blackboard_->set("reply_deadline", 0.0);
  blackboard_->set("user_speaking_topic", get_parameter("user_speaking_topic").as_string());
  // Conversational fillers while the reply is prepared (0 = none); the phrases are fetched from
  // the intelligence when the dialogue begins
  blackboard_->set("filler_after_s", filler_after_s);
  blackboard_->set("filler_repeat_s", filler_repeat_s);

  for (const char * counter :
    {"turns", "timeouts", "stalled", "attempts", "help", "spoken", "off_task"})
  {
    blackboard_->set(counter, 0);
  }
  blackboard_->set("done", false);
  blackboard_->set("progress", true);
  for (const char * text : {"user_text", "reply", "label", "phase", "last_utterance",
      "failure_reason", "result_reason", "ending", "outcome", "error_closing", "fillers",
      "last_filler"})
  {
    blackboard_->set(text, std::string());
  }
  blackboard_->set("data", std::string("{}"));
  last_spoken_ = 0;
}

void
InteractionOrchestrator::acting()
{
  if (!acting_initialized_) {
    acting_initialized_ = true;
    runner_result_.clear();
    go_to_state(chosen_behavior_);
    return;
  }
  if (goal_handle_->is_canceling()) {
    finish(false, true, "Cancelled");
    return;
  }
  publish_feedback();
  if (runner_result_.empty()) {
    return;
  }

  // The outcome is written by the tree's conclusion; if the tree failed before it, the ending
  // it had reached, or an error of the system
  const bool success = runner_result_ == "SUCCESS";
  std::string outcome = bb<std::string>("outcome", "");
  if (outcome.empty()) {
    outcome = bb<std::string>("ending", "");
  }
  if (outcome.empty()) {
    outcome = success ? DialogTask::Result::OUTCOME_COMPLETED : DialogTask::Result::OUTCOME_ERROR;
  }
  std::string reason;
  if (!success) {
    // An error says what failed (failure_reason, from the node that failed); the other
    // endings say why the dialogue ended (result_reason, from the conclusion)
    const auto failure = bb<std::string>("failure_reason", "");
    const auto result = bb<std::string>("result_reason", "");
    reason = outcome == DialogTask::Result::OUTCOME_ERROR ?
      (failure.empty() ? result : failure) : (result.empty() ? failure : result);
    if (reason.empty()) {
      reason = "The dialogue ended without success";
    }
  }
  finish(success, false, reason, outcome);
}

void
InteractionOrchestrator::go_to_state(int behavior_id)
{
  const auto & behavior = catalog_.at(behavior_id);
  deactivate_all_runners();
  activate_runner(behavior.node_name);

  // The runner builds its Behavior Tree when activated: if that fails (e.g. invalid XML) it
  // does not reach the active state and the request fails
  const auto runner = runners_.find(behavior.node_name);
  if (runner == runners_.end() ||
    runner->second->get_current_state().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE)
  {
    finish(false, false, "The behavior runner " + behavior.node_name + " could not be activated");
    return;
  }
  RCLCPP_INFO(get_logger(), "Dialogue behavior %s running", behavior.name.c_str());
}

void
InteractionOrchestrator::behavior_status_callback(std_msgs::msg::String::UniquePtr msg)
{
  if (state_ == InteractionState::ACTING && (msg->data == "SUCCESS" || msg->data == "FAILURE")) {
    runner_result_ = msg->data;
  }
}

// ==== FEEDBACK, RESULT AND STATUS ====

void
InteractionOrchestrator::publish_feedback()
{
  const int spoken = bb<int>("spoken", 0);
  if (spoken == last_spoken_) {
    return;
  }
  last_spoken_ = spoken;
  auto feedback = std::make_shared<DialogTask::Feedback>();
  feedback->state = bb<std::string>("phase", "");
  feedback->current_frame_json = bb<std::string>("data", "{}");
  feedback->last_utterance = bb<std::string>("last_utterance", "");
  feedback->turns = bb<int>("turns", 0);
  goal_handle_->publish_feedback(feedback);
}

void
InteractionOrchestrator::finish(
  bool success, bool canceled, const std::string & failure_reason, const std::string & outcome)
{
  deactivate_all_runners();

  const int turns = bb<int>("turns", 0);
  auto result = std::make_shared<DialogTask::Result>();
  result->success = success;
  if (canceled) {
    result->outcome = DialogTask::Result::OUTCOME_CANCELLED;
  } else if (!outcome.empty()) {
    result->outcome = outcome;
  } else {
    result->outcome = success ?
      DialogTask::Result::OUTCOME_COMPLETED : DialogTask::Result::OUTCOME_ERROR;
  }
  result->final_frame_json = bb<std::string>("data", "{}");
  result->failure_reason = failure_reason;
  result->total_turns = turns;

  if (goal_handle_ && goal_handle_->is_active()) {
    if (canceled) {
      goal_handle_->canceled(result);
    } else if (success) {
      goal_handle_->succeed(result);
    } else {
      goal_handle_->abort(result);
    }
  }
  if (success) {
    RCLCPP_INFO(get_logger(), "Dialogue completed (%d turns)", turns);
  } else {
    RCLCPP_WARN(
      get_logger(), "Dialogue ended: %s (%s)", result->outcome.c_str(), failure_reason.c_str());
  }

  // The intelligence closes the dialogue: its pending LLM calls, sessions and transcript
  if (intelligence_client_->service_is_ready()) {
    auto request = std::make_shared<AskIntelligence::Request>();
    request->dialogue_id = dialogue_id_;
    request->skill = "finish";
    request->state = bb<std::string>("dialogue", "");
    request->option = result->outcome + (failure_reason.empty() ? "" : " (" + failure_reason + ")");
    request->number = turns;
    intelligence_client_->async_send_request(request);
  }

  last_failed_ = !success;
  {
    std::lock_guard<std::mutex> lock(goal_mutex_);
    goal_handle_.reset();
  }
  state_ = InteractionState::IDLE;
}

void
InteractionOrchestrator::publish_status()
{
  if (!status_pub_->is_activated()) {
    return;
  }
  Status status;
  switch (state_) {
    case InteractionState::IDLE:
      status.status = last_failed_ ? Status::FAILED : Status::IDLE;
      break;
    case InteractionState::SETTING_BEHAVIOR_ACTION:
    case InteractionState::SETTING_BEHAVIOR_INPUT:
      status.status = Status::REASONING;
      break;
    case InteractionState::ACTING:
      status.status = Status::RUNNING;
      status.behavior = catalog_.count(chosen_behavior_) ? catalog_[chosen_behavior_].name : "";
      status.phase = bb<std::string>("phase", "");
      break;
  }
  status_pub_->publish(status);
}

}  // namespace interaction_system
