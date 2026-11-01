// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#include "interaction_system_behaviors/Listen.hpp"

#include <string>

namespace interaction_system_behaviors
{

using namespace std::chrono_literals;

namespace
{
// A transcription reaches /interaction/user_utterance through the intelligence: how long to
// wait for it after the recognizer finished before taking it as noise
constexpr auto UTTERANCE_GRACE = 1500ms;
}  // namespace

Listen::Listen(const std::string & name, const BT::NodeConfig & conf)
: BT::StatefulActionNode(name, conf)
{
  node_ = config().blackboard->get<rclcpp::Node::SharedPtr>("node");
  if (!node_) {
    throw BT::RuntimeError("Listen: 'node' not found in the blackboard");
  }
  utterance_sub_ = node_->create_subscription<std_msgs::msg::String>(
    "/interaction/user_utterance", 10, [this](std_msgs::msg::String::ConstSharedPtr msg) {
      std::lock_guard<std::mutex> lock(mutex_);
      last_utterance_ = msg->data;
      ++received_;
    });
  turn_pub_ = node_->create_publisher<std_msgs::msg::String>("/interaction/user_turn", 10);
  listen_.configure(node_, "/stt_action");
}

BT::NodeStatus
Listen::onStart()
{
  double timeout_s = 20.0;
  getInput("timeout_s", timeout_s);
  const auto now = std::chrono::steady_clock::now();
  deadline_ = now + std::chrono::milliseconds(static_cast<int64_t>(timeout_s * 1000.0));
  relisten_at_ = now;
  listening_ = false;
  std::lock_guard<std::mutex> lock(mutex_);
  received_at_start_ = received_;
  return BT::NodeStatus::RUNNING;
}

BT::NodeStatus
Listen::onRunning()
{
  std::string text;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (received_ > received_at_start_) {
      text = last_utterance_;
    }
  }
  if (!text.empty()) {
    if (listening_) {
      listen_.cancel();
      listening_ = false;
    }
    setOutput("text", text);
    turn_pub_->publish(std_msgs::msg::String().set__data(text));
    return BT::NodeStatus::SUCCESS;
  }

  const auto now = std::chrono::steady_clock::now();
  if (now > deadline_) {
    if (listening_) {
      listen_.cancel();
      listening_ = false;
    }
    RCLCPP_INFO(node_->get_logger(), "Listen: the user said nothing");
    return BT::NodeStatus::FAILURE;
  }
  // simple_hri listens only when asked: ask it again until an utterance comes
  if (listening_ && listen_.finished()) {
    listening_ = false;
    const auto result = listen_.result();
    if (result && result->timed_out) {
      RCLCPP_INFO(node_->get_logger(), "Listen: the user said nothing");
      return BT::NodeStatus::FAILURE;
    }
    // A transcription (or a failed recording): wait for the utterance before listening again
    relisten_at_ = now + UTTERANCE_GRACE;
  }
  if (!listening_ && now >= relisten_at_) {
    if (!listen_.server_ready()) {
      return BT::NodeStatus::RUNNING;
    }
    ListenAction::Goal goal;
    goal.max_wait = static_cast<float>(
      std::chrono::duration<double>(deadline_ - now).count());
    listen_.send(goal);
    listening_ = true;
  }
  return BT::NodeStatus::RUNNING;
}

void
Listen::onHalted()
{
  if (listening_) {
    listen_.cancel();
    listening_ = false;
  }
}

}  // namespace interaction_system_behaviors
