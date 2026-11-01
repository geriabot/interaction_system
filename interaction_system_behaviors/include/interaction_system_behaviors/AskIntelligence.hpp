// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__ASK_INTELLIGENCE_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__ASK_INTELLIGENCE_HPP_

#include <string>

#include "behaviortree_cpp/behavior_tree.h"
#include "rclcpp/rclcpp.hpp"

#include "interaction_system_interfaces/srv/ask_intelligence.hpp"
#include "interaction_system_behaviors/AsyncServiceCall.hpp"

namespace interaction_system_behaviors
{

/**
 * Asks the interaction intelligence one skill (/interaction/ask_intelligence): a question to
 * the LLM, such as understanding an utterance or phrasing a reply, or an operation on the data
 * of the dialogue. The state of the dialogue goes in and comes back updated (the intelligence
 * keeps none), with what the robot must say (reply), what the skill found (label) and the
 * phase and data of the dialogue.
 *
 * Inside a reply being prepared (WithFillers writes its deadline, `deadline`), the request
 * carries what is left of the reply's time budget (budget_s), capped so that the intelligence
 * answers before timeout_s: once it is spent, the intelligence answers with the skill's
 * fallbacks instead of waiting for the model. Outside a reply (deadline 0), no budget.
 *
 * Fails if the skill fails (the reason is written to `error`) or the intelligence does not
 * answer within timeout_s (the hard limit of every request).
 */
class AskIntelligence : public BT::StatefulActionNode
{
public:
  using AskIntelligenceSrv = interaction_system_interfaces::srv::AskIntelligence;

  AskIntelligence(const std::string & name, const BT::NodeConfig & conf);

  static BT::PortsList providedPorts()
  {
    return {
      BT::InputPort<std::string>("skill", "The skill to ask, e.g. slot_filling.update"),
      BT::InputPort<std::string>("user_text", "", "What the user said, if the skill needs it"),
      BT::InputPort<std::string>("option", "", "A word the skill needs (e.g. done, stuck)"),
      BT::InputPort<int>("number", 0, "A number the skill needs (e.g. the attempts so far)"),
      BT::InputPort<std::string>("args", "", "A JSON object the skill needs (begin: the goal)"),
      BT::InputPort<std::string>("dialogue_id", "{dialogue_id}", "The dialogue"),
      BT::InputPort<double>(
        "timeout_s", "{intelligence_timeout}", "Longest wait for the answer (s)"),
      BT::InputPort<double>(
        "deadline", "{reply_deadline}",
        "When the time budget of the reply being prepared ends (steady clock, s; 0: none)"),
      BT::BidirectionalPort<std::string>("state", "{dialogue}", "The state of the dialogue"),
      BT::OutputPort<std::string>("reply", "{reply}", "What the robot must say"),
      BT::OutputPort<std::string>("label", "{label}", "What the skill found"),
      BT::OutputPort<std::string>("phase", "{phase}", "The phase of the dialogue"),
      BT::OutputPort<std::string>("data", "{data}", "The data of the dialogue (JSON)"),
      BT::OutputPort<std::string>("error", "{failure_reason}", "Why the skill failed"),
    };
  }

  /// The budget_s of a request: what is left until `deadline` (steady clock, s), at most
  /// timeout_s - ANSWER_MARGIN_S and at least MIN_BUDGET_S (spent, but not 0, which means no
  /// budget); 0 when deadline <= 0 (no reply is being prepared)
  static double budget_for(double deadline, double now, double timeout_s);

  /// Time the intelligence has to answer after its budget (the skill's own work and the
  /// transport), so that a skill that spends its budget still answers before timeout_s
  static constexpr double ANSWER_MARGIN_S = 2.0;
  static constexpr double MIN_BUDGET_S = 0.001;

  BT::NodeStatus onStart() override;
  BT::NodeStatus onRunning() override;
  void onHalted() override;

private:
  rclcpp::Node::SharedPtr node_;
  AsyncServiceCall<AskIntelligenceSrv> call_;
  std::string skill_;
};

}  // namespace interaction_system_behaviors

#endif  // INTERACTION_SYSTEM_BEHAVIORS__ASK_INTELLIGENCE_HPP_
