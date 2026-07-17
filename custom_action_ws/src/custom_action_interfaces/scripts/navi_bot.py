
import rclpy
import asyncio
import time
import threading
from enum import Enum
from rclpy.executors import SingleThreadedExecutor
from navi_forward_action_client import NaviForwardActionClient
from navi_rotate_action_client import NaviRotateActionClient
from navi_way_point_action_client import NaviWayPointActionClient


class MoveType(Enum):
    STOP = 0
    FORWARD = 1
    LEFT = 2
    RIGHT = 3


class NaviBot(object):

    def __init__(self):
        self.started = False

    def start(self):
        if self.started:
            return
        self.started = True

        rclpy.init()

        self._forward_action_client = NaviForwardActionClient()
        self._rotate_action_client = NaviRotateActionClient()
        self._way_point_action_client = NaviWayPointActionClient()

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._forward_action_client)
        self._executor.add_node(self._rotate_action_client)
        self._executor.add_node(self._way_point_action_client)
        self._spin_thread = threading.Thread(target=self._spin_executor, args=(), daemon=True)
        self._spin_thread.start()

        time.sleep(5)

        print("NaviBot started")

    def _spin_executor(self):
        try:
            self._executor.spin()
        except Exception:
            print(f"Spin exception")

    def jazzy_control_sync(self, move_type: MoveType):
        if move_type == MoveType.FORWARD:
            self._forward_action_client.send_goal(2.0)
        elif move_type == MoveType.LEFT:
            self._rotate_action_client.send_goal(-90.0)
        elif move_type == MoveType.RIGHT:
            self._rotate_action_client.send_goal(180.0)

    def _jazzy_done_callback(self, _):
        pass

    async def move_with_jazzy(self, move_type: MoveType, blocking: bool = True):
        asyncio.new_event_loop
        if blocking:
            self.jazzy_control_sync(move_type)
            return

        if move_type == MoveType.STOP:
            await self._move_task
            return

        if hasattr(self, '_move_task'):
            self._move_task.remove_done_callback(self._jazzy_done_callback)
            await self._move_task
        # Ensure intermediate execution
        self._move_task = asyncio.get_running_loop().run_in_executor(
            None, self.jazzy_control_sync, move_type)
        self._move_task.add_done_callback(self._jazzy_done_callback)

    def __del__(self):
        self.stop()

    def stop(self):
        if not self.started:
            return
        self.started = False
        if hasattr(self, '_move_task'):
            self._move_task.cancel()
        rclpy.shutdown()
        self._spin_thread.join()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return True


def create_robot():
    return NaviBot()


class AppContext:
    """
    Context manager for the application and agents, ensuring proper initialization and cleanup.
    """
    def __init__(
        self,
    ):
        self.robot = create_robot()

    async def __aenter__(self):
        self.robot.__enter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.robot.__exit__(exc_type, exc_value, traceback)


async def test_navi_bot():
    async with AppContext() as ctx:
        robot = ctx.robot

        await robot.move_with_jazzy(MoveType.FORWARD, blocking=False)
        time.sleep(5)
        await robot.move_with_jazzy(MoveType.LEFT, blocking=False)
        time.sleep(5)
        await robot.move_with_jazzy(MoveType.RIGHT, blocking=False)
        time.sleep(5)


async def main():
    await test_navi_bot()


if __name__ == '__main__':
    asyncio.run(main())
