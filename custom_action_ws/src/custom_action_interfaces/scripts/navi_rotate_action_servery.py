
import time

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node

from custom_action_interfaces.action import NaviRotate


class NaviRotateActionServer(Node):

    def __init__(self):
        super().__init__('navi_rotate_action_server')
        self._action_server = ActionServer(
            self,
            NaviRotate,
            'navi_rotate',
            self.execute_callback)

    def execute_callback(self, goal_handle):
        self.get_logger().info('Executing goal...')

        feedback_msg = NaviRotate.Feedback()
        feedback_msg.remaining = abs(goal_handle.request.theta)

        while feedback_msg.remaining > 0:
            feedback_msg.remaining -= 10
            self.get_logger().info('Feedback: {0}'.format(feedback_msg.remaining))
            goal_handle.publish_feedback(feedback_msg)
            time.sleep(1)

        goal_handle.succeed()

        result = NaviRotate.Result()
        result.delta = abs(goal_handle.request.theta) - feedback_msg.remaining
        return result


def main(args=None):
    rclpy.init(args=args)

    navi_rotate_action_server = NaviRotateActionServer()

    rclpy.spin(navi_rotate_action_server)


if __name__ == '__main__':
    main()
