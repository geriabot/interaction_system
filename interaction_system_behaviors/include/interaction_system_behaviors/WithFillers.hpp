// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__WITH_FILLERS_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__WITH_FILLERS_HPP_

#include <chrono>
#include <random>
#include <string>
#include <vector>

#include "behaviortree_cpp/decorator_node.h"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/bool.hpp"
#include "std_msgs/msg/string.hpp"

#include "simple_hri_interfaces/action/say.hpp"
#include "interaction_system_behaviors/ActionCall.hpp"

namespace interaction_system_behaviors
{

/**
 * Conversational fillers while the robot prepares its reply.
 *
 * Wraps what computes the reply (the questions to the interaction intelligence of a user turn,
 * or of the opening). If the child is still running after_s seconds after it started, the robot
 * says one fixed filler phrase ("Hmm…", "Just a moment…"), and one more after a further
 * repeat_s seconds; then it waits. The phrases come from the blackboard (one per line, fetched
 * from the intelligence at the start of the dialogue, in its language); a phrase is never
 * chosen twice in a row (last_filler).
 *
 * A filler is not a line of the dialogue: it is published on /robot_filler (not on
 * /robot_utterance), it does not change spoken nor last_utterance, and no counter of the
 * dialogue. When the child finishes, the node returns its status as soon as the filler being
 * said (if any) has finished, so that the reply follows it. A text-to-speech that is not
 * available skips the filler. Halting the node halts the child and stops the voice.
 * after_s <= 0 disables the fillers; repeat_s <= 0 allows only one.
 *
 * It also bounds the wait for the model: when the child starts, it writes the deadline of the
 * reply (`deadline`: now + budget_s, steady clock in seconds; reply_budget after a user turn,
 * opening_budget for the opening), which every AskIntelligence inside sends to the
 * intelligence as what is left of the budget; it writes 0 when the child finishes or is halted.
 * budget_s <= 0: no budget.
 */
class WithFillers : public BT::DecoratorNode
{
public:
  using Say = simple_hri_interfaces::action::Say;

  WithFillers(const std::string & name, const BT::NodeConfig & conf);

  static BT::PortsList providedPorts()
  {
    return {
      BT::InputPort<double>(
        "after_s", "{filler_after_s}",
        "Seconds without a reply before the first filler (<= 0: no fillers)"),
      BT::InputPort<double>(
        "repeat_s", "{filler_repeat_s}",
        "Further seconds before the second and last filler (<= 0: only one)"),
      BT::InputPort<std::string>("phrases", "{fillers}", "The filler phrases, one per line"),
      BT::InputPort<double>("timeout_s", "{tts_timeout}", "Longest time to say a filler (s)"),
      BT::BidirectionalPort<std::string>(
        "last", "{last_filler}", "The last filler said (not repeated in a row)"),
      BT::InputPort<double>(
        "budget_s", "{reply_budget}",
        "Time the intelligence may spend asking the model for this reply (s; <= 0: no budget)"),
      BT::OutputPort<double>(
        "deadline", "{reply_deadline}",
        "When the budget of the reply ends (steady clock, s; 0 when no reply is being prepared)"),
    };
  }

  void halt() override;

  /// The phrases of a newline-separated list, trimmed, without empty lines
  static std::vector<std::string> parse_phrases(const std::string & text);
  /// The index of the phrase to say: any but `last` (when there is another), drawn by `pick`
  static size_t choose(
    const std::vector<std::string> & phrases, const std::string & last, size_t pick);

private:
  BT::NodeStatus tick() override;
  void start_filler();
  void stop_filler();
  void set_speaking(bool speaking);
  void set_deadline(std::chrono::steady_clock::time_point now, bool preparing);

  rclcpp::Node::SharedPtr node_;
  ActionCall<Say> say_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr filler_pub_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr is_speaking_pub_;
  std::mt19937 rng_;

  // The reply being prepared
  std::chrono::steady_clock::time_point started_;
  bool child_done_ {false};
  BT::NodeStatus child_status_ {BT::NodeStatus::IDLE};
  int fillers_said_ {0};

  // The filler being said
  bool speaking_ {false};
  std::chrono::steady_clock::time_point speech_deadline_;
};

}  // namespace interaction_system_behaviors

#endif  // INTERACTION_SYSTEM_BEHAVIORS__WITH_FILLERS_HPP_
