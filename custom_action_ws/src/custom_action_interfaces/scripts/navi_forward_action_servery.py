
import time

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node

from custom_action_interfaces.action import NaviForward


class NaviForwardActionServer(Node):

    def __init__(self):
        super().__init__('navi_forward_action_server')
        self._action_server = ActionServer(
            self,
            NaviForward,
            'navi_forward',
            self.execute_callback)

    def execute_callback(self, goal_handle):
        self.get_logger().info('Executing goal...')

        feedback_msg = NaviForward.Feedback()
        feedback_msg.remaining = goal_handle.request.distance

        while feedback_msg.remaining > 0:
            feedback_msg.remaining -= 0.1
            self.get_logger().info('Feedback: {0}'.format(feedback_msg.remaining))
            goal_handle.publish_feedback(feedback_msg)
            time.sleep(1)

        goal_handle.succeed()

        result = NaviForward.Result()
        result.delta = goal_handle.request.distance - feedback_msg.remaining
        return result


def main(args=None):
    rclpy.init(args=args)

    navi_forward_action_server = NaviForwardActionServer()

    rclpy.spin(navi_forward_action_server)


if __name__ == '__main__':
    main()
