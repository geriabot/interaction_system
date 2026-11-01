// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__SPEAK_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__SPEAK_HPP_

#include <atomic>
#include <chrono>
#include <string>

#include "behaviortree_cpp/behavior_tree.h"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/bool.hpp"
#include "std_msgs/msg/string.hpp"

#include "ros2_dialog_interfaces/msg/barge_in.hpp"
#include "simple_hri_interfaces/action/say.hpp"
#include "interaction_system_behaviors/ActionCall.hpp"

namespace interaction_system_behaviors
{

/**
 * Says a text through the speech capability (simple_hri's /tts_action).
 *
 * The text is published on /robot_utterance and written to `last_utterance` when the node
 * starts, and `spoken` is incremented (the orchestrator sends the feedback of the dialogue on
 * each new utterance). While the robot speaks, /is_speaking (latched Bool) is true, for the
 * embodiment (gestures, eye LEDs). If the user starts speaking (barge-in: True on the
 * user-speaking topic, e.g. the lip activity of the perception) the speech is cut, /barge_in is
 * published, and the node succeeds: what the user says next is the next turn. A text-to-speech
 * that is not available or does not finish in time does not stop the dialogue (the node
 * succeeds with a warning). Halting the node stops the voice.
 */
class Speak : public BT::StatefulActionNode
{
public:
  using Say = simple_hri_interfaces::action::Say;

  Speak(const std::string & name, const BT::NodeConfig & conf);

  static BT::PortsList providedPorts()
  {
    return {
      BT::InputPort<std::string>("text", "What to say"),
      BT::InputPort<double>("timeout_s", "{tts_timeout}", "Longest time to say it (s)"),
      BT::InputPort<std::string>(
        "barge_in_topic", "{user_speaking_topic}",
        "Bool topic, True while the user speaks (empty: no barge-in)"),
      BT::OutputPort<std::string>("last_utterance", "{last_utterance}", "The text said"),
      BT::BidirectionalPort<int>("spoken", "{spoken}", "Utterances said in the dialogue"),
    };
  }

  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override;

private:
  void set_speaking(bool speaking);

  rclcpp::Node::SharedPtr node_;
  ActionCall<Say> say_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr utterance_pub_;
  rclcpp::Publisher<ros2_dialog_interfaces::msg::BargeIn>::SharedPtr barge_in_pub_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr is_speaking_pub_;
  bool speaking_ {false};
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr user_speaking_sub_;
  std::string user_speaking_topic_;
  std::atomic<bool> user_speaking_ {false};

  bool sent_ {false};
  std::string text_;
  std::chrono::steady_clock::time_point start_;
  std::chrono::steady_clock::time_point deadline_;
  const std::chrono::seconds server_wait_ {5};
};

}  // namespace interaction_system_behaviors

#endif  // INTERACTION_SYSTEM_BEHAVIORS__SPEAK_HPP_
