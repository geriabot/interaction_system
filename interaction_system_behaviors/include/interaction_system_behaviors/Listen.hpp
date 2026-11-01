// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__LISTEN_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__LISTEN_HPP_

#include <chrono>
#include <cstdint>
#include <mutex>
#include <string>

#include "behaviortree_cpp/behavior_tree.h"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

#include "simple_hri_interfaces/action/listen.hpp"
#include "interaction_system_behaviors/ActionCall.hpp"

namespace interaction_system_behaviors
{

/**
 * Waits for the next thing the user says.
 *
 * The transcriptions reach the interaction intelligence, which drops the noise (hallucinated
 * filler, stray words) and publishes the rest on /interaction/user_utterance: the node takes
 * the first one after it started. The node asks the speech capability (simple_hri) to listen
 * (/stt_action) until an utterance comes.
 *
 * Succeeds with the utterance (also published on /interaction/user_turn, the turns of the
 * dialogue), or fails if the user says nothing within timeout_s. Halting it stops listening.
 */
class Listen : public BT::StatefulActionNode
{
public:
  using ListenAction = simple_hri_interfaces::action::Listen;

  Listen(const std::string & name, const BT::NodeConfig & conf);

  static BT::PortsList providedPorts()
  {
    return {
      BT::InputPort<double>("timeout_s", "{wait_timeout}", "Longest wait for the user (s)"),
      BT::OutputPort<std::string>("text", "{user_text}", "What the user said"),
    };
  }

  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override;

private:
  rclcpp::Node::SharedPtr node_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr utterance_sub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr turn_pub_;
  ActionCall<ListenAction> listen_;

  std::mutex mutex_;  // the subscription runs in an executor thread while the tree ticks
  uint64_t received_ {0};
  std::string last_utterance_;

  bool listening_ {false};
  uint64_t received_at_start_ {0};
  std::chrono::steady_clock::time_point deadline_;
  // After a transcription with no utterance (all noise), when to listen again
  std::chrono::steady_clock::time_point relisten_at_;
};

}  // namespace interaction_system_behaviors

#endif  // INTERACTION_SYSTEM_BEHAVIORS__LISTEN_HPP_
