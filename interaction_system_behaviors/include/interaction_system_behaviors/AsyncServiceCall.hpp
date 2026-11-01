// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#ifndef INTERACTION_SYSTEM_BEHAVIORS__ASYNC_SERVICE_CALL_HPP_
#define INTERACTION_SYSTEM_BEHAVIORS__ASYNC_SERVICE_CALL_HPP_

#include <chrono>
#include <future>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"

namespace interaction_system_behaviors
{

/**
 * Non-blocking service call for Behavior Tree nodes.
 *
 * The tree tick must not wait (for the service to appear or for the response): the
 * request is sent as soon as the service is available and the response is checked on
 * every tick with poll(), both within a deadline. The client is created once and reused.
 */
template<typename ServiceT>
class AsyncServiceCall
{
public:
  using Request = typename ServiceT::Request;
  using Response = typename ServiceT::Response;

  enum class Status { RUNNING, SUCCEEDED, FAILED };

  void configure(const rclcpp::Node::SharedPtr & node, const std::string & service_name)
  {
    if (!client_ || service_name != service_name_) {
      node_ = node;
      service_name_ = service_name;
      client_ = node->create_client<ServiceT>(service_name);
    }
  }

  /// Start a call. The request is sent when the service is available, within timeout.
  void start(std::shared_ptr<Request> request, std::chrono::milliseconds timeout)
  {
    request_ = request;
    future_ = {};
    response_ = nullptr;
    deadline_ = std::chrono::steady_clock::now() + timeout;
    try_send();
  }

  /// To be called on every tick until it returns SUCCEEDED or FAILED.
  Status poll()
  {
    if (!request_) {
      return Status::FAILED;
    }
    if (!try_send()) {
      if (std::chrono::steady_clock::now() > deadline_) {
        RCLCPP_WARN(node_->get_logger(), "Service %s not available", service_name_.c_str());
        request_ = nullptr;
        return Status::FAILED;
      }
      return Status::RUNNING;
    }
    if (future_.wait_for(std::chrono::seconds(0)) == std::future_status::ready) {
      response_ = future_.get();
      request_ = nullptr;
      future_ = {};
      return Status::SUCCEEDED;
    }
    if (std::chrono::steady_clock::now() > deadline_) {
      RCLCPP_WARN(node_->get_logger(), "No response from %s", service_name_.c_str());
      request_ = nullptr;
      future_ = {};
      return Status::FAILED;
    }
    return Status::RUNNING;
  }

  typename Response::SharedPtr response() const {return response_;}

  /// Forget the call in progress (its response, if any, is ignored).
  void cancel()
  {
    request_ = nullptr;
    future_ = {};
  }

  /// Send a request without waiting for anything (e.g. cleanup when a node is halted):
  /// only if the service is available now.
  bool send_without_waiting(std::shared_ptr<Request> request)
  {
    if (!client_ || !client_->service_is_ready()) {
      if (node_) {
        RCLCPP_WARN(node_->get_logger(), "Service %s not available", service_name_.c_str());
      }
      return false;
    }
    client_->async_send_request(request);
    return true;
  }

private:
  /// Send the pending request if the service is available. True once it has been sent.
  bool try_send()
  {
    if (future_.valid()) {
      return true;
    }
    if (request_ && client_ && client_->service_is_ready()) {
      future_ = client_->async_send_request(request_).future.share();
      return true;
    }
    return false;
  }

  rclcpp::Node::SharedPtr node_;
  std::string service_name_;
  typename rclcpp::Client<ServiceT>::SharedPtr client_;
  std::shared_ptr<Request> request_;
  std::shared_future<typename Response::SharedPtr> future_;
  typename Response::SharedPtr response_;
  std::chrono::steady_clock::time_point deadline_;
};

}  // namespace interaction_system_behaviors

#endif  // INTERACTION_SYSTEM_BEHAVIORS__ASYNC_SERVICE_CALL_HPP_
