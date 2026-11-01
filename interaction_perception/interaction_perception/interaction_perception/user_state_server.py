# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
UserStateServer — the interface of the perception subsystem to the interaction system.

Keeps the latest estimates of the detectors and serves them on request:

Topics consumed:
  /user_emotion   (String) — JSON {"emotion", "confidence", "source"} (emotion_detector)
  /user_speaking  (Bool)   — lip activity (lip_activity_detector)

Service:
  /interaction_perception/get_user_state (interaction_perception_msgs/srv/GetUserState)

The interaction system asks for the state of the user when it builds a question to the LLM
(emotional context), and reads the lip activity as a stream for barge-in. A perception system
that replaces these detectors only has to serve the same service and topic.
"""

import json
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from interaction_perception_msgs.msg import UserState
from interaction_perception_msgs.srv import GetUserState


class UserStateServer(Node):

    def __init__(self):
        super().__init__("user_state_server")
        self.declare_parameter("emotion_topic", "/user_emotion")
        self.declare_parameter("speaking_topic", "/user_speaking")
        self._lock = threading.Lock()
        self._state = UserState()
        self._available = False
        self.create_subscription(
            String, self.get_parameter("emotion_topic").value, self._on_emotion, 10
        )
        self.create_subscription(
            Bool, self.get_parameter("speaking_topic").value, self._on_speaking, 10
        )
        self.create_service(
            GetUserState, "/interaction_perception/get_user_state", self._on_get
        )
        self.get_logger().info("User state server ready.")

    def _on_emotion(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except Exception as exc:
            self.get_logger().warning(f"Could not parse the emotion: {exc}")
            return
        with self._lock:
            self._state.stamp = self.get_clock().now().to_msg()
            self._state.emotion = str(data.get("emotion", "neutral"))
            self._state.confidence = float(data.get("confidence", 0.0))
            self._state.source = str(data.get("source", "vision"))
            self._available = True

    def _on_speaking(self, msg: Bool) -> None:
        with self._lock:
            self._state.speaking = msg.data

    def _on_get(self, request, response):
        with self._lock:
            response.available = self._available
            response.state = self._state
        return response


def main(args=None):
    rclpy.init(args=args)
    node = UserStateServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
