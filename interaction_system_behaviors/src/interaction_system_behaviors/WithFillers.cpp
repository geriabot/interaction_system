// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#include "interaction_system_behaviors/WithFillers.hpp"

#include <sstream>
#include <string>
#include <vector>

namespace interaction_system_behaviors
{

WithFillers::WithFillers(const std::string & name, const BT::NodeConfig & conf)
: BT::DecoratorNode(name, conf), rng_(std::random_device{}())
{
  node_ = config().blackboard->get<rclcpp::Node::SharedPtr>("node");
  if (!node_) {
    throw BT::RuntimeError("WithFillers: 'node' not found in the blackboard");
  }
  // The same voice as Speak; the filler goes on its own topic, not on /robot_utterance, so that
  // no client takes it for a line of the dialogue
  say_.configure(node_, "/tts_action");
  filler_pub_ = node_->create_publisher<std_msgs::msg::String>("/robot_filler", 10);
  is_speaking_pub_ = node_->create_publisher<std_msgs::msg::Bool>(
    "/is_speaking", rclcpp::QoS(1).reliable().transient_local());
}

std::vector<std::string>
WithFillers::parse_phrases(const std::string & text)
{
  std::vector<std::string> phrases;
  std::istringstream lines(text);
  std::string line;
  while (std::getline(lines, line)) {
    const auto begin = line.find_first_not_of(" \t\r");
    if (begin == std::string::npos) {
      continue;
    }
    const auto end = line.find_last_not_of(" \t\r");
    phrases.push_back(line.substr(begin, end - begin + 1));
  }
  return phrases;
}

size_t
WithFillers::choose(
  const std::vector<std::string> & phrases, const std::string & last, size_t pick)
{
  std::vector<size_t> candidates;
  for (size_t i = 0; i < phrases.size(); ++i) {
    if (phrases[i] != last) {
      candidates.push_back(i);
    }
  }
  if (candidates.empty()) {
    return phrases.empty() ? 0 : pick % phrases.size();
  }
  return candidates[pick % candidates.size()];
}

void
WithFillers::set_speaking(bool speaking)
{
  is_speaking_pub_->publish(std_msgs::msg::Bool().set__data(speaking));
}

void
WithFillers::set_deadline(std::chrono::steady_clock::time_point now, bool preparing)
{
  double budget_s = 0.0;
  if (!getInput("budget_s", budget_s)) {
    budget_s = 0.0;
  }
  const double deadline = preparing && budget_s > 0.0 ?
    std::chrono::duration<double>(now.time_since_epoch()).count() + budget_s : 0.0;
  setOutput("deadline", deadline);
}

void
WithFillers::start_filler()
{
  // Counted even when it cannot be said, so that it is not tried again on every tick
  ++fillers_said_;
  std::string text;
  getInput("phrases", text);
  const auto phrases = parse_phrases(text);
  if (phrases.empty()) {
    return;
  }
  if (!say_.server_ready()) {
    RCLCPP_WARN(node_->get_logger(), "WithFillers: text-to-speech not available, no filler");
    return;
  }
  std::string last;
  getInput("last", last);
  const auto & filler = phrases[choose(phrases, last, rng_())];
  setOutput("last", filler);

  filler_pub_->publish(std_msgs::msg::String().set__data(filler));
  Say::Goal goal;
  goal.text = filler;
  say_.send(goal);
  speaking_ = true;
  set_speaking(true);
  double timeout_s = 60.0;
  getInput("timeout_s", timeout_s);
  speech_deadline_ = std::chrono::steady_clock::now() +
    std::chrono::milliseconds(static_cast<int64_t>(timeout_s * 1000.0));
  RCLCPP_INFO(node_->get_logger(), "Filler %d: %s", fillers_said_, filler.c_str());
}

void
WithFillers::stop_filler()
{
  if (!speaking_) {
    return;
  }
  if (!say_.finished()) {
    say_.cancel();
  }
  speaking_ = false;
  set_speaking(false);
}

BT::NodeStatus
WithFillers::tick()
{
  const auto now = std::chrono::steady_clock::now();
  if (status() == BT::NodeStatus::IDLE) {
    started_ = now;
    child_done_ = false;
    child_status_ = BT::NodeStatus::IDLE;
    fillers_said_ = 0;
    speaking_ = false;
    // The budget of the reply runs from now (the end of the user's turn, or the opening)
    set_deadline(now, true);
  }
  setStatus(BT::NodeStatus::RUNNING);

  // The reply
  if (!child_done_) {
    const auto status = child_node_->executeTick();
    if (status != BT::NodeStatus::RUNNING) {
      child_done_ = true;
      child_status_ = status;
      resetChild();
      set_deadline(now, false);
    }
  }

  // The filler being said
  if (speaking_ && (say_.finished() || now > speech_deadline_)) {
    if (!say_.finished()) {
      RCLCPP_WARN(node_->get_logger(), "WithFillers: the filler did not finish in time");
    }
    stop_filler();
  }

  // The reply is ready: it follows the filler, if one is being said
  if (child_done_) {
    return speaking_ ? BT::NodeStatus::RUNNING : child_status_;
  }

  // Not ready: a filler at after_s, one more at after_s + repeat_s, then nothing
  double after_s = 0.0;
  double repeat_s = 0.0;
  getInput("after_s", after_s);
  getInput("repeat_s", repeat_s);
  const bool none_left = fillers_said_ >= 2 || (fillers_said_ == 1 && repeat_s <= 0.0);
  if (speaking_ || after_s <= 0.0 || none_left) {
    return BT::NodeStatus::RUNNING;
  }
  const double due_s = fillers_said_ == 0 ? after_s : after_s + repeat_s;
  if (std::chrono::duration<double>(now - started_).count() >= due_s) {
    start_filler();
  }
  return BT::NodeStatus::RUNNING;
}

void
WithFillers::halt()
{
  stop_filler();
  set_deadline(std::chrono::steady_clock::now(), false);
  BT::DecoratorNode::halt();
}

}  // namespace interaction_system_behaviors
