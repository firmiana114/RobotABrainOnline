
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from custom_action_interfaces.action import NaviWayPoint


class NaviWayPointActionClient(Node):

    def __init__(self):
        super().__init__('navi_way_point_action_client')
        self._action_client = ActionClient(self, NaviWayPoint, 'navi_way_point')

    def send_goal(self, x, y):
        goal_msg = NaviWayPoint.Goal()
        goal_msg.x = x
        goal_msg.y = y

        self._action_client.wait_for_server()

        self._send_goal_future = self._action_client.send_goal_async(goal_msg, feedback_callback=self.feedback_callback)

        self._send_goal_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().info('Goal rejected :(')
            return

        self.get_logger().info('Goal accepted :)')

        self._get_result_future = goal_handle.get_result_async()
        self._get_result_future.add_done_callback(self.get_result_callback)

    def get_result_callback(self, future):
        result = future.result().result
        self.get_logger().info('Result: ({0},{1})'.format(result.x_delta, result.y_delta))
        #rclpy.shutdown()

    def feedback_callback(self, feedback_msg):
        feedback = feedback_msg.feedback
        self.get_logger().info('Received feedback: ({0},{1})'.format(feedback.x_remaining, feedback.y_remaining))


def main(args=None):
    rclpy.init(args=args)

    action_client = NaviWayPointActionClient()

    action_client.send_goal(-2.0, 4.0)

    rclpy.spin(action_client)


if __name__ == '__main__':
    main()
