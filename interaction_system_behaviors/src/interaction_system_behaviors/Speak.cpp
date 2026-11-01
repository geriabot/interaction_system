// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#include "interaction_system_behaviors/Speak.hpp"

#include <string>

namespace interaction_system_behaviors
{

Speak::Speak(const std::string & name, const BT::NodeConfig & conf)
: BT::StatefulActionNode(name, conf)
{
  node_ = config().blackboard->get<rclcpp::Node::SharedPtr>("node");
  if (!node_) {
    throw BT::RuntimeError("Speak: 'node' not found in the blackboard");
  }
  say_.configure(node_, "/tts_action");
  utterance_pub_ = node_->create_publisher<std_msgs::msg::String>("/robot_utterance", 10);
  // Reliable: the barge-in notice is the GUI's only source of truth for it
  barge_in_pub_ = node_->create_publisher<ros2_dialog_interfaces::msg::BargeIn>("/barge_in", 10);
  // Latched: a late subscriber (gestures, LEDs) gets the current state
  is_speaking_pub_ = node_->create_publisher<std_msgs::msg::Bool>(
    "/is_speaking", rclcpp::QoS(1).reliable().transient_local());
}

void
Speak::set_speaking(bool speaking)
{
  if (speaking == speaking_) {
    return;
  }
  speaking_ = speaking;
  is_speaking_pub_->publish(std_msgs::msg::Bool().set__data(speaking));
}

BT::NodeStatus
Speak::onStart()
{
  text_.clear();
  getInput("text", text_);
  if (text_.empty()) {
    return BT::NodeStatus::SUCCESS;
  }

  // The utterance is reported before it is said, so that the clients of the dialogue see it
  // while the robot speaks
  setOutput("last_utterance", text_);
  int spoken = 0;
  getInput("spoken", spoken);
  setOutput("spoken", spoken + 1);

  std::string topic;
  getInput("barge_in_topic", topic);
  if (topic != user_speaking_topic_) {
    user_speaking_topic_ = topic;
    user_speaking_sub_.reset();
    if (!topic.empty()) {
      user_speaking_sub_ = node_->create_subscription<std_msgs::msg::Bool>(
        topic, 10, [this](std_msgs::msg::Bool::ConstSharedPtr msg) {
          user_speaking_ = msg->data;
        });
    }
  }
  // Only a barge-in during this utterance interrupts it
  user_speaking_ = false;

  sent_ = false;
  start_ = std::chrono::steady_clock::now();
  deadline_ = start_ + server_wait_;
  return onRunning();
}

BT::NodeStatus
Speak::onRunning()
{
  const auto now = std::chrono::steady_clock::now();

  if (!sent_) {
    if (!say_.server_ready()) {
      if (now > deadline_) {
        RCLCPP_WARN(node_->get_logger(), "Speak: text-to-speech not available, not said");
        return BT::NodeStatus::SUCCESS;
      }
      return BT::NodeStatus::RUNNING;
    }
    utterance_pub_->publish(std_msgs::msg::String().set__data(text_));
    Say::Goal goal;
    goal.text = text_;
    say_.send(goal);
    sent_ = true;
    set_speaking(true);
    double timeout_s = 60.0;
    getInput("timeout_s", timeout_s);
    deadline_ = now + std::chrono::milliseconds(static_cast<int64_t>(timeout_s * 1000.0));
    return BT::NodeStatus::RUNNING;
  }

  if (say_.finished()) {
    set_speaking(false);
    return BT::NodeStatus::SUCCESS;
  }

  if (user_speaking_) {
    // The user cut the robot off: the only place where this is known for certain
    say_.cancel();
    ros2_dialog_interfaces::msg::BargeIn msg;
    msg.utterance = text_;
    msg.spoken_ms = static_cast<int32_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(now - start_).count());
    barge_in_pub_->publish(msg);
    set_speaking(false);
    RCLCPP_INFO(node_->get_logger(), "Speak: barge-in after %d ms", msg.spoken_ms);
    return BT::NodeStatus::SUCCESS;
  }

  if (now > deadline_) {
    say_.cancel();
    set_speaking(false);
    RCLCPP_WARN(node_->get_logger(), "Speak: the utterance did not finish in time");
    return BT::NodeStatus::SUCCESS;
  }
  return BT::NodeStatus::RUNNING;
}

void
Speak::onHalted()
{
  if (sent_) {
    say_.cancel();
    set_speaking(false);
  }
}

}  // namespace interaction_system_behaviors
