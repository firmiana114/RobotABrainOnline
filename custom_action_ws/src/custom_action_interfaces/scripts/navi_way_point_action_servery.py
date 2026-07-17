
import time

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node

from custom_action_interfaces.action import NaviWayPoint


class NaviWayPointActionServer(Node):

    def __init__(self):
        super().__init__('navi_way_point_action_server')
        self._action_server = ActionServer(
            self,
            NaviWayPoint,
            'navi_way_point',
            self.execute_callback)

    def execute_callback(self, goal_handle):
        self.get_logger().info('Executing goal...')

        feedback_msg = NaviWayPoint.Feedback()
        feedback_msg.x_remaining = abs(goal_handle.request.x)
        feedback_msg.y_remaining = abs(goal_handle.request.y)

        while feedback_msg.x_remaining > 0 or feedback_msg.y_remaining > 0:
            feedback_msg.x_remaining -= 10
            feedback_msg.y_remaining -= 10
            self.get_logger().info('Feedback: ({0},{1})'.format(feedback_msg.x_remaining, feedback_msg.y_remaining))
            goal_handle.publish_feedback(feedback_msg)
            time.sleep(1)

        goal_handle.succeed()

        result = NaviWayPoint.Result()
        result.x_delta = abs(goal_handle.request.x) - feedback_msg.x_remaining
        result.y_delta = abs(goal_handle.request.y) - feedback_msg.y_remaining
        return result


def main(args=None):
    rclpy.init(args=args)

    navi_way_point_action_server = NaviWayPointActionServer()

    rclpy.spin(navi_way_point_action_server)


if __name__ == '__main__':
    main()
