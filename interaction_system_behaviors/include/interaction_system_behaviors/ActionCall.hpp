// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__ACTION_CALL_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__ACTION_CALL_HPP_

#include <memory>
#include <mutex>
#include <optional>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"

namespace interaction_system_behaviors
{

/**
 * Non-blocking action call for Behavior Tree nodes: send a goal, check on every tick whether
 * it finished, cancel it. The goal and result callbacks run in executor threads while the tree
 * ticks in another, so the state is guarded by a mutex.
 */
template<typename ActionT>
class ActionCall
{
public:
  using GoalHandle = rclcpp_action::ClientGoalHandle<ActionT>;
  using Result = typename ActionT::Result;

  void configure(const rclcpp::Node::SharedPtr & node, const std::string & action_name)
  {
    if (!client_ || action_name != action_name_) {
      action_name_ = action_name;
      client_ = rclcpp_action::create_client<ActionT>(node, action_name);
    }
  }

  bool server_ready() const {return client_ && client_->action_server_is_ready();}

  void send(const typename ActionT::Goal & goal)
  {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      goal_handle_.reset();
      rejected_ = false;
      result_.reset();
      finished_ = false;
    }
    typename rclcpp_action::Client<ActionT>::SendGoalOptions options;
    options.goal_response_callback = [this](typename GoalHandle::SharedPtr handle) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (handle) {
          goal_handle_ = handle;
        } else {
          rejected_ = true;
          finished_ = true;
        }
      };
    options.result_callback = [this](const typename GoalHandle::WrappedResult & wrapped) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (wrapped.result) {
          result_ = *wrapped.result;
        }
        finished_ = true;
      };
    client_->async_send_goal(goal, options);
  }

  /// True once the goal was rejected or has a result
  bool finished()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return finished_;
  }

  /// The result, if the goal finished with one
  std::optional<Result> result()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return result_;
  }

  /// Cancel the goal if it is still running (a finished goal is left as it is)
  void cancel()
  {
    typename GoalHandle::SharedPtr handle;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (!finished_) {
        handle = goal_handle_;
      }
      goal_handle_.reset();
      finished_ = true;
    }
    if (handle) {
      try {
        client_->async_cancel_goal(handle);
      } catch (const rclcpp_action::exceptions::UnknownGoalHandleError &) {
        // It finished meanwhile
      }
    }
  }

private:
  std::string action_name_;
  typename rclcpp_action::Client<ActionT>::SharedPtr client_;
  std::mutex mutex_;
  typename GoalHandle::SharedPtr goal_handle_;
  std::optional<Result> result_;
  bool rejected_ {false};
  bool finished_ {false};
};

}  // namespace interaction_system_behaviors

#endif  // INTERACTION_SYSTEM_BEHAVIORS__ACTION_CALL_HPP_
