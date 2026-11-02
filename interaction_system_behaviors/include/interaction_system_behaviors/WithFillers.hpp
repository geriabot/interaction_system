// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__WITH_FILLERS_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__WITH_FILLERS_HPP_

#include <array>
#include <chrono>
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
 * or of the opening). While the child is still running, the robot says a ladder of fixed filler
 * phrases whose wording changes as the wait grows: step k at after_s + STEP_OFFSETS_S[k] (with
 * after_s = 2: 2, 5, 9 and 14 s), then one more every repeat_s seconds (the last phrase). The
 * phrases come from the blackboard (one per line, in the order of the steps, fetched from the
 * intelligence at the start of the dialogue, in its language): "Hmm…", "One moment…", "Let me
 * think about it for a moment…", "Almost there…", "Still on it, just a second…" (the filler
 * ladder, decided by the author, 2026-11-01).
 *
 * A filler is not a line of the dialogue: it is published on /robot_filler (not on
 * /robot_utterance), it does not change spoken nor last_utterance, and no counter of the
 * dialogue. When the child finishes, the node returns its status as soon as the filler being
 * said (if any) has finished, so that the reply follows it. A text-to-speech that is not
 * available skips the filler. Halting the node halts the child and stops the voice.
 * after_s <= 0 disables the fillers; repeat_s <= 0: no filler after the ladder's steps.
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
        "Seconds between the fillers after the ladder's steps (<= 0: none after them)"),
      BT::InputPort<std::string>("phrases", "{fillers}", "The filler phrases, one per line"),
      BT::InputPort<double>("timeout_s", "{tts_timeout}", "Longest time to say a filler (s)"),
      BT::BidirectionalPort<std::string>(
        "last", "{last_filler}", "The last filler said"),
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
  /// When the steps of the ladder fall, after after_s (seconds)
  static constexpr std::array<double, 4> STEP_OFFSETS_S {0.0, 3.0, 7.0, 12.0};
  /// When filler number `step` (0-based) is due since the reply started (seconds); negative: none
  static double due_s(size_t step, double after_s, double repeat_s);
  /// The phrase of filler number `step`: the step's own, the last one after the ladder
  static size_t phrase_index(size_t step, size_t phrases);

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
