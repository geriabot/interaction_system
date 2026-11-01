// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#include "interaction_system_behaviors/AskIntelligence.hpp"

#include <algorithm>
#include <chrono>
#include <memory>
#include <string>

namespace interaction_system_behaviors
{

AskIntelligence::AskIntelligence(const std::string & name, const BT::NodeConfig & conf)
: BT::StatefulActionNode(name, conf)
{
  node_ = config().blackboard->get<rclcpp::Node::SharedPtr>("node");
  if (!node_) {
    throw BT::RuntimeError("AskIntelligence: 'node' not found in the blackboard");
  }
  call_.configure(node_, "/interaction/ask_intelligence");
}

double
AskIntelligence::budget_for(double deadline, double now, double timeout_s)
{
  if (deadline <= 0.0) {
    return 0.0;
  }
  double budget = deadline - now;
  if (timeout_s > ANSWER_MARGIN_S) {
    budget = std::min(budget, timeout_s - ANSWER_MARGIN_S);
  }
  return std::max(budget, MIN_BUDGET_S);
}

BT::NodeStatus
AskIntelligence::onStart()
{
  auto request = std::make_shared<AskIntelligenceSrv::Request>();
  if (!getInput("skill", skill_) || skill_.empty()) {
    setOutput("error", std::string("AskIntelligence: the skill is missing"));
    return BT::NodeStatus::FAILURE;
  }
  request->skill = skill_;
  getInput("dialogue_id", request->dialogue_id);
  getInput("state", request->state);
  getInput("user_text", request->user_text);
  getInput("option", request->option);
  getInput("number", request->number);
  getInput("args", request->args);

  double timeout_s = 30.0;
  getInput("timeout_s", timeout_s);
  double deadline = 0.0;
  if (!getInput("deadline", deadline)) {
    deadline = 0.0;  // not in the blackboard: no reply is being prepared
  }
  const double now = std::chrono::duration<double>(
    std::chrono::steady_clock::now().time_since_epoch()).count();
  request->budget_s = budget_for(deadline, now, timeout_s);
  call_.start(
    request, std::chrono::milliseconds(static_cast<int64_t>(timeout_s * 1000.0)));
  RCLCPP_DEBUG(node_->get_logger(), "AskIntelligence: %s", skill_.c_str());
  return BT::NodeStatus::RUNNING;
}

BT::NodeStatus
AskIntelligence::onRunning()
{
  switch (call_.poll()) {
    case AsyncServiceCall<AskIntelligenceSrv>::Status::RUNNING:
      return BT::NodeStatus::RUNNING;
    case AsyncServiceCall<AskIntelligenceSrv>::Status::FAILED:
      setOutput("error", "The interaction intelligence did not answer (" + skill_ + ")");
      return BT::NodeStatus::FAILURE;
    case AsyncServiceCall<AskIntelligenceSrv>::Status::SUCCEEDED:
      break;
  }

  const auto response = call_.response();
  if (!response->success) {
    RCLCPP_ERROR(node_->get_logger(), "Skill failed: %s", response->message.c_str());
    setOutput("error", response->message);
    return BT::NodeStatus::FAILURE;
  }
  setOutput("state", response->state);
  setOutput("reply", response->reply);
  setOutput("label", response->label);
  setOutput("phase", response->phase);
  setOutput("data", response->data);
  RCLCPP_INFO(
    node_->get_logger(), "%s -> %s%s", skill_.c_str(),
    response->label.empty() ? "(no label)" : response->label.c_str(),
    response->reply.empty() ? "" : " (reply)");
  return BT::NodeStatus::SUCCESS;
}

void
AskIntelligence::onHalted()
{
  // A service call cannot be cancelled: its response, if it comes, is ignored
  call_.cancel();
}

}  // namespace interaction_system_behaviors
