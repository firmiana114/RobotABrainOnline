
import time
import threading

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from custom_action_interfaces.action import NaviForward


class NaviForwardActionClient(Node):

    def __init__(self):
        super().__init__('navi_forward_action_client')
        self._action_client = ActionClient(self, NaviForward, 'navi_forward')

    def send_goal(self, distance=2.0):
        goal_msg = NaviForward.Goal()
        goal_msg.distance = distance

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
        self.get_logger().info('Result: {0}'.format(result.delta))
        #rclpy.shutdown()

    def feedback_callback(self, feedback_msg):
        feedback = feedback_msg.feedback
        self.get_logger().info('Received feedback: {0}'.format(feedback.remaining))

    def __del__(self):
        self.stop()

    def stop(self):
        pass
        #rclpy.shutdown()


def multi_goal_test1(args=None):
    rclpy.init(args=args)
    action_client = NaviForwardActionClient()

    action_client.send_goal()

    action_client.send_goal()
    
    rclpy.spin(action_client)


def multi_goal_test2(args=None):
    rclpy.init(args=args)
    action_client = NaviForwardActionClient()

    spin_thread = threading.Thread(target=rclpy.spin, args=(action_client,), daemon=True)
    spin_thread.start()

    action_client.send_goal()
    # action_client.send_goal()

    # time.sleep(5)

    # rclpy.shutdown()
    spin_thread.join()


def main(args=None):
    rclpy.init(args=args)
    action_client = NaviForwardActionClient()
    action_client.send_goal()
    rclpy.spin(action_client)
    #rclpy.shutdown()

    time.sleep(5)

    rclpy.init(args=args)
    action_client = NaviForwardActionClient()
    action_client.send_goal()
    rclpy.spin(action_client)
    #rclpy.shutdown()


if __name__ == '__main__':
    #main()
    #multi_goal_test1()
    multi_goal_test2()
