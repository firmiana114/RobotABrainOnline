#!/usr/bin/env python3

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer
from custom_action_interfaces.action import NaviForward, NaviRotate, NaviWayPoint, NaviHead, NaviArm, NaviIkArm, NaviGrab, NaviTargetChanged, NaviShakeHands
from geometry_msgs.msg import Twist, PoseStamped, Pose, Point
from std_msgs.msg import Float64MultiArray,String,Bool
from tf2_ros import Buffer, TransformListener
from tf2_ros import TransformException
from tf2_geometry_msgs import do_transform_pose
import time
import math
from tf_transformations import quaternion_from_matrix

class KuavoActionServer(Node):
    def __init__(self):
        super().__init__('kuavo_action_server')

        # 创建TF缓冲区和监听器
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.get_logger().info("waiting for TF transform...")
        # 定义一个变量用于保存手臂基部位置
        self.arm_base_position = Point()

        # 定时器定期更新手臂基部位置
        self.timer = self.create_timer(1.0, self.update_arm_base_position)

        # 初始化手臂抓取标志
        self.grab = False
        self.shake_hands = False

        # 依次创建启动前进、旋转和导航的Action Server
        # 注意：这里的Action Server名称需要与客户端一致
        self.forward_server = ActionServer(self, NaviForward, "navi_forward", self.forward_cb)
        self.rotation_server = ActionServer(self, NaviRotate, "navi_rotate", self.rotation_cb)
        self.waypoint_server = ActionServer(self, NaviWayPoint, "navi_way_point", self.waypoint_cb)
        self.head_server = ActionServer(self, NaviHead, "navi_head", self.head_cb)
        self.arm_server = ActionServer(self, NaviArm, "navi_arm", self.arm_cb)
        self.ik_arm_server = ActionServer(self, NaviIkArm, "navi_ik_arm", self.ik_arm_cb)
        self.grab_server = ActionServer(self, NaviGrab, "navi_grab", self.grab_cb)
        self.target_changer_server = ActionServer(self, NaviTargetChanged, "navi_target_changed", self.target_changed_cb)
        self.shake_hands_server = ActionServer(self, NaviShakeHands, "navi_shake_hands", self.shake_hands_cb)

        self.get_logger().info("Kuavo Action Server Started. Ready to receive goals.")

        # 发布速度命令到 /cmd_vel 话题
        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.navigation_control_pub = self.create_publisher(Bool, '/navigation_control', 10)

        # 发布导航目标点到 /move_base_simple/goal 话题
        self.way_point_pub = self.create_publisher(PoseStamped, '/move_base_simple/goal', 10)
        self.object_pub = self.create_publisher(PoseStamped, '/detected_object', 10)

        # 发布头部角度命令到 /head_degree 话题
        self.head_pub = self.create_publisher(Float64MultiArray, '/head_degree', 10)

        # 发布手部动作命令到 /arm_action 话题
        self.arm_pub = self.create_publisher(String, '/arm_action', 10)

        # 发布逆运动学手部目标点到 /ik_arm_goal 话题
        self.ik_arm_pub = self.create_publisher(PoseStamped, '/ik_arm_goal', 10)
        self.ik_arm_state_sub = self.create_subscription(Bool, '/ik_arm_state', self.ik_arm_state_cb, 10)
        self.object_pub = self.create_publisher(PoseStamped, '/detected_object', 10)
        self.shake_hands_pub = self.create_publisher(PoseStamped, '/shake_hands_goal', 10)

        # 发布抓取目标点到 /grab_goal 话题
        self.grab_pub = self.create_publisher(PoseStamped, '/grab_goal', 10)
        self.camera_point_pub = self.create_publisher(PoseStamped, '/camera_point', 10)
        self.target_changed_pub = self.create_publisher(Bool, '/target_changed', 10)

        # 获取参数
        self.linear_speed = self.get_parameter_or('~linear_speed', 0.2)
        self.angular_speed = self.get_parameter_or('~angular_speed', 0.2)

    def update_arm_base_position(self):
            self.left_arm_base_position, self.right_arm_base_position, self.left_shoulder_position, self.right_shoulder_position = self.get_arm_base_position()

    def get_arm_base_position(self):
        try:
            # 将手肘的位置坐标固定为期望位置
            
            # 4. 从变换中提取位置信息（translation部分）
            left_arm_base_position = Point()
            left_arm_base_position.x = 0.123
            left_arm_base_position.y = 0.293
            left_arm_base_position.z = 0.147
            
            
            # 4. 从变换中提取位置信息（translation部分）
            right_arm_base_position = Point()
            right_arm_base_position.x = 0.123
            right_arm_base_position.y = -0.293
            right_arm_base_position.z = 0.147

            # 4. 从变换中提取位置信息（translation部分）
            left_shoulder_position = Point()
            left_shoulder_position.x = 0.155
            left_shoulder_position.y = 0.293
            left_shoulder_position.z = 0.424

            # 4. 从变换中提取位置信息（translation部分）
            right_shoulder_position = Point()
            right_shoulder_position.x = 0.155
            right_shoulder_position.y = -0.293
            right_shoulder_position.z = 0.424
            
            return left_arm_base_position, right_arm_base_position, left_shoulder_position, right_shoulder_position
            
        except TransformException as e:
            self.get_logger().error(
                f"无法获取zarm_l4_link、zarm_r4_link到base_link的变换: {str(e)}"
            )
            return None

    # TF坐标系转换
    def transform_point(self,ik_arm_point_camera):
        point = Pose()

        try:
            transform = self.tf_buffer.lookup_transform("base_link", ik_arm_point_camera.header.frame_id, rclpy.time.Time(), timeout=rclpy.duration.Duration(seconds=1.0))
            
            point = ik_arm_point_camera.pose

            transformed_point = do_transform_pose(point, transform)

            self.get_logger().info(
                f"相机坐标系下的点: ({point.position.x:.2f}, {point.position.y:.2f}, {point.position.z:.2f})"
            )
            self.get_logger().info(
                f"基坐标系下的点: ({transformed_point.position.x:.2f}, {transformed_point.position.y:.2f}, {transformed_point.position.z:.2f})"
            )

            return transformed_point
        except TransformException as ex:
            self.get_logger().error(f'Could not transform {point.header.frame_id} to base_link: {ex}')
            return None
            
    # 计算手臂目标点朝向
    def calculate_arm_orientation(self, base_point, target_point):
        """
        base_point: Point()，zarm_r1_link在base_link下的坐标
        target_point: Point()，目标点坐标
        返回: 四元数 (x, y, z, w)
        """
        # 1. 计算 z 轴（末端z轴负半轴指向目标）
        z_axis = np.array([
            # target_point.x - base_point.x,
            # target_point.y - base_point.y,
            # target_point.z - base_point.z
            base_point.x - target_point.x,
            base_point.y - target_point.y,
            base_point.z - target_point.z
        ])
        norm_z = np.linalg.norm(z_axis)
        if norm_z < 1e-6:
            return (0.0, 0.0, 0.0, 1.0)
        z_axis /= norm_z

        # 2. 选参考向量，避免与z轴共线
        reference = np.array([0, 1, 0])
        if abs(np.dot(z_axis, reference)) > 0.99:
            reference = np.array([1, 0, 0])

        # 3. 计算 z 轴（右手系：reference × z）
        x_axis = np.cross(reference, z_axis)
        x_axis /= np.linalg.norm(x_axis)

        # 4. 计算 y 轴（右手系：z × x）
        y_axis = np.cross(z_axis, x_axis)
        y_axis /= np.linalg.norm(y_axis)

        # 5. 构造旋转矩阵（列向量分别为 x, y, z）
        rot_matrix = np.eye(4)
        rot_matrix[0:3, 0] = x_axis
        rot_matrix[0:3, 1] = y_axis
        rot_matrix[0:3, 2] = z_axis

        # 6. 转为四元数
        quat = quaternion_from_matrix(rot_matrix)
        return tuple(quat)
    
    # 计算目标点相对于手肘朝向
    def calculate_object_orientation(self, base_point, target_point):
        """
        base_point: Point()，zarm_r1_link在base_link下的坐标
        target_point: Point()，目标点坐标
        返回: 四元数 (x, y, z, w)
        """
        # 1. 计算 x 轴（末端x轴指向目标）
        x_axis = np.array([
            target_point.x - base_point.x,
            target_point.y - base_point.y,
            target_point.z - base_point.z
        ])
        norm_x = np.linalg.norm(x_axis)
        if norm_x < 1e-6:
            return (0.0, 0.0, 0.0, 1.0)
        x_axis /= norm_x

        # 2. 选参考向量，避免与x轴共线
        reference = np.array([0, 1, 0])
        if abs(np.dot(x_axis, reference)) > 0.99:
            reference = np.array([0, 0, 1])

        # 3. 计算 z 轴（右手系：reference × x）
        z_axis = np.cross(reference, x_axis)
        z_axis /= np.linalg.norm(z_axis)

        # 4. 计算 y 轴（右手系：z × x）
        y_axis = np.cross(z_axis, x_axis)
        y_axis /= np.linalg.norm(y_axis)

        # 5. 构造旋转矩阵（列向量分别为 x, y, z）
        rot_matrix = np.eye(4)
        rot_matrix[0:3, 0] = x_axis
        rot_matrix[0:3, 1] = y_axis
        rot_matrix[0:3, 2] = z_axis

        # 6. 转为四元数
        quat = quaternion_from_matrix(rot_matrix)
        return tuple(quat)

    # 计算抓取目标点朝向
    def calculate_grab_orientation(self, base_point, target_point, is_right):
        """
        base_point: Point()，zarm_r1_link在base_link下的坐标
        target_point: Point()，目标点坐标
        返回: 四元数 (x, y, z, w)，确保旋转后y轴朝上、x轴朝前、z轴朝右
        """
        # 1. 计算基础z轴（末端z轴负半轴指向目标）
        z_axis = np.array([
            base_point.x - target_point.x,
            base_point.y - target_point.y,
            base_point.z - target_point.z
        ])
        norm_z = np.linalg.norm(z_axis)
        if norm_z < 1e-6:
            return (0.0, 0.0, 0.0, 1.0)
        z_axis /= norm_z

        # 2. 选参考向量，避免与z轴共线
        reference = np.array([0, 1, 0])
        if abs(np.dot(z_axis, reference)) > 0.99:
            reference = np.array([1, 0, 0])

        # 3. 计算基础x轴（右手系：reference × z）
        x_axis = np.cross(reference, z_axis)
        x_axis /= np.linalg.norm(x_axis)
        if is_right:
            x_axis = -x_axis
        else:
            pass

        # 4. 计算基础y轴（右手系：z × x）
        y_axis = np.cross(z_axis, x_axis)
        y_axis /= np.linalg.norm(y_axis)

        # 5. 构造初始旋转矩阵（3x3）
        rot_matrix = np.array([
            [x_axis[0], y_axis[0], z_axis[0]],
            [x_axis[1], y_axis[1], z_axis[1]],
            [x_axis[2], y_axis[2], z_axis[2]]
        ])

        # 6. 创建绕Z轴逆时针旋转90度的旋转矩阵
        # 旋转角度（弧度）：90度 = π/2
        theta = np.pi / 2
        rot_z = np.array([
            [np.cos(theta), -np.sin(theta), 0],
            [np.sin(theta), np.cos(theta), 0],
            [0, 0, 1]
        ])
        # 代入θ=π/2后的简化结果（cos(π/2)=0, sin(π/2)=1）
        # rot_z = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])

        # 7. 组合旋转矩阵：先原有旋转，再绕Z轴旋转
        final_rot_matrix = np.dot(rot_matrix, rot_z)

        # 8. 转换为4x4矩阵（适配tf的转换函数）
        rot_matrix_4x4 = np.eye(4)
        rot_matrix_4x4[:3, :3] = final_rot_matrix

        # 9. 转为四元数
        quat = quaternion_from_matrix(rot_matrix_4x4)
        return tuple(quat)
    

    # 定义前进回调
    def forward_cb(self, goal_handle):
        # 获取前进距离，转化为速度命令
        distance = goal_handle.request.distance
        self.get_logger().info(f"Received new goal: Move {distance}m in forward direction")
        move_time = abs(distance) / self.linear_speed
        start_time = self.get_clock().now().to_msg().sec

        cmd_vel = Twist()
        while (self.get_clock().now().to_msg().sec - start_time) < move_time:
            if goal_handle.is_cancel_requested:
                self.get_logger().info("Goal preempted")
                goal_handle.set_canceled()
                self.cmd_vel_pub.publish(Twist())
                return

            cmd_vel.linear.x = self.linear_speed
            self.cmd_vel_pub.publish(cmd_vel)
            self.navigation_control_pub.publish(Bool(data=True))
            feedback = NaviForward.Feedback()
            elapsed_time = self.get_clock().now().to_msg().sec - start_time
            feedback.remaining = distance - (cmd_vel.linear.x * elapsed_time)
            goal_handle.publish_feedback(feedback)

            time.sleep(0.01)

        self.cmd_vel_pub.publish(Twist())

        goal_handle.succeed()
        result = NaviForward.Result()
        result.delta = distance - feedback.remaining
        self.get_logger().info(f"Successfully moved {distance}m in forward direction")
        return result

    # 定义旋转回调
    def rotation_cb(self, goal_handle):
        # 获取旋转角度，转化为速度命令
        theta = goal_handle.request.theta
        self.get_logger().info(f"Received new goal: Rotate {theta}°")
        radians = theta * math.pi / 180

        if radians < 0:
            self.angular_speed = -abs(self.angular_speed)
        else:
            self.angular_speed = abs(self.angular_speed)
        move_time = radians / self.angular_speed
        start_time = self.get_clock().now().to_msg().sec

        cmd_vel = Twist()
        while (self.get_clock().now().to_msg().sec - start_time) < move_time:
            if goal_handle.is_cancel_requested:
                self.get_logger().info("Goal preempted")
                goal_handle.set_canceled()
                self.cmd_vel_pub.publish(Twist())
                return

            cmd_vel.angular.z = self.angular_speed
            self.cmd_vel_pub.publish(cmd_vel)
            self.navigation_control_pub.publish(Bool(data=True))

            feedback = NaviRotate.Feedback()
            elapsed_time = self.get_clock().now().to_msg().sec - start_time
            remaining_radians = radians - (cmd_vel.angular.z * elapsed_time)
            remaining_theta = remaining_radians * 180 / math.pi
            feedback.remaining = remaining_theta
            goal_handle.publish_feedback(feedback)

            time.sleep(0.01)

        self.cmd_vel_pub.publish(Twist())

        goal_handle.succeed()
        result = NaviRotate.Result()
        result.delta = theta - feedback.remaining
        self.get_logger().info(f"Successfully rotated {theta}°")
        return result

    # 定义导航回调
    def waypoint_cb(self, goal_handle):
        # 获取目标点坐标，发送到move_base
        self.waypoints = PoseStamped()
        self.waypoints.header.frame_id = "map"
        self.waypoints.header.stamp = self.get_clock().now().to_msg()
        self.waypoints.pose.position.x = goal_handle.request.position_x
        self.waypoints.pose.position.y = goal_handle.request.position_y
        self.waypoints.pose.position.z = goal_handle.request.position_z
        self.waypoints.pose.orientation.x = goal_handle.request.orientation_x
        self.waypoints.pose.orientation.y = goal_handle.request.orientation_y
        self.waypoints.pose.orientation.z = goal_handle.request.orientation_z
        self.waypoints.pose.orientation.w = goal_handle.request.orientation_w

        self.get_logger().info(f"Received new goal: X:{goal_handle.request.position_x}, Y:{goal_handle.request.position_y}")
        self.way_point_pub.publish(self.waypoints)
        self.get_logger().info(f"Sending goal: X:{goal_handle.request.position_x}, Y:{goal_handle.request.position_y}")
        goal_handle.succeed()
        result = NaviWayPoint.Result()
        result.position_x_delta = goal_handle.request.position_x
        result.position_y_delta = goal_handle.request.position_y
        result.position_z_delta = goal_handle.request.position_z
        result.orientation_x_delta = goal_handle.request.orientation_x
        result.orientation_y_delta = goal_handle.request.orientation_y
        result.orientation_z_delta = goal_handle.request.orientation_z
        result.orientation_w_delta = goal_handle.request.orientation_w
        return result

    # 定义头部回调
    def head_cb(self, goal_handle):
        # 获取头部目标角度，转化为速度命令
        yaw = goal_handle.request.yaw
        pitch = goal_handle.request.pitch
        self.get_logger().info(f"Received new goal: Move head to {yaw}°, {pitch}°")
        head_msg = Float64MultiArray()
        head_msg.data = [yaw, pitch]
        self.head_pub.publish(head_msg)
        goal_handle.succeed()
        result = NaviHead.Result()
        result.yaw_delta = yaw
        result.pitch_delta = pitch
        self.get_logger().info(f"Successfully moved head to {yaw}°, {pitch}°")
        return result
    
    # 定义手部回调
    def arm_cb(self, goal_handle):
        # 获取手部动作指令，发布到 /arm_action 话题
        action_name = goal_handle.request.action_name
        self.get_logger().info(f"Received new action: Perform arm action '{action_name}'")
        print(type(action_name))
        arm_msg = String()
        arm_msg.data = action_name
        self.arm_pub.publish(arm_msg)
        goal_handle.succeed()
        result = NaviArm.Result()
        result.success = True
        result.message = f"Action '{action_name}' executed"
        self.get_logger().info(f"Successfully performed arm action '{action_name}'")
        return result
    
    # 定义抓取回调
    def grab_cb(self, goal_handle):
        self.get_logger().info("Received new grab task")
        self.grab = True
        self.ik_arm_cb(goal_handle)

        result = NaviGrab.Result()
        result.position_x_delta = goal_handle.request.position_x
        result.position_y_delta = goal_handle.request.position_y
        result.position_z_delta = goal_handle.request.position_z
        result.orientation_x_delta = goal_handle.request.orientation_x
        result.orientation_y_delta = goal_handle.request.orientation_y
        result.orientation_z_delta = goal_handle.request.orientation_z
        result.orientation_w_delta = goal_handle.request.orientation_w
        return result
    
    def shake_hands_cb(self, goal_handle):
        self.get_logger().info("Received new shake hands task")
        self.shake_hands = True
        self.ik_arm_cb(goal_handle)
        
        result = NaviShakeHands.Result()
        return result
    
    # 定义逆运动学手部回调
    def ik_arm_cb(self, goal_handle):
        # 1. 检查Z过低
        if goal_handle.request.position_z < -0.8:
            result = NaviIkArm.Result()
            result.position_x_delta = 0.0
            result.position_y_delta = 0.0
            result.position_z_delta = 0.0
            result.orientation_x_delta = 0.0
            result.orientation_y_delta = 0.0
            result.orientation_z_delta = 0.0
            result.orientation_w_delta = 0.0
            self.get_logger().info("Goal canceled: position_z is below -0.8m")
            return result

        self.get_logger().info(f"Received new goal: {goal_handle.request.position_x}, {goal_handle.request.position_y}, {goal_handle.request.position_z}")
        msg = PoseStamped()
        msg.header.frame_id = "camera_base"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.position.x = goal_handle.request.position_x
        msg.pose.position.y = goal_handle.request.position_y
        msg.pose.position.z = goal_handle.request.position_z
        msg.pose.orientation.x = goal_handle.request.orientation_x
        msg.pose.orientation.y = goal_handle.request.orientation_y
        msg.pose.orientation.z = goal_handle.request.orientation_z
        msg.pose.orientation.w = goal_handle.request.orientation_w
        self.camera_point_pub.publish(msg)

        # 2. 相机坐标系的点
        p_cam = np.array([
            goal_handle.request.position_x,
            goal_handle.request.position_y,
            goal_handle.request.position_z
        ])

        # 3. 静态旋转和平移（base_link ← camera_base）
        theta_grab = 0.996
        cos = math.cos(theta_grab)
        sin = math.sin(theta_grab)
        Rbc_grab = np.array([[ cos, 0, sin],
                        [ 0,   1, 0  ],
                        [-sin, 0, cos]])
        tbc_grab = np.array([0.112, 0.0, 0.665])

        theta_point = 0.520
        cos = math.cos(theta_point)
        sin = math.sin(theta_point)
        Rbc_point = np.array([[ cos, 0, sin],
                        [ 0,   1, 0  ],
                        [-sin, 0, cos]])
        tbc_point = np.array([0.068, 0.0, 0.718])

        # 4. 变换到base_link坐标系
        p_base_grab = Rbc_grab @ p_cam + tbc_grab
        p_base_point = Rbc_point @ p_cam + tbc_point
        if self.grab or self.shake_hands:
            p_base = p_base_grab
        else:
            p_base = p_base_point

        # 5. 组装 PoseStamped，限制手臂工作范围
        self.ik_arm_point = PoseStamped()
        self.ik_arm_point.header.frame_id = "base_link"
        self.ik_arm_point.header.stamp = self.get_clock().now().to_msg()
        self.ik_arm_point.pose.position.x = float(p_base[0])
        self.ik_arm_point.pose.position.y = float(p_base[1])
        self.ik_arm_point.pose.position.z = float(p_base[2])

        # 6. 根据左右侧选择手臂基座位置计算朝向
        point = Point(x=float(p_base[0]), y=float(p_base[1]), z=float(p_base[2]))
        point.x = round(point.x, 3)
        point.y = round(point.y, 3)
        point.z = round(point.z, 3)
        if goal_handle.request.position_y > 0:
            self.arm_base_position = self.left_arm_base_position
            self.shoulder_position = self.left_shoulder_position
            is_right = False
        else:
            self.arm_base_position = self.right_arm_base_position
            self.shoulder_position = self.right_shoulder_position
            is_right = True

        # 计算目标点与基座的距离
        base = self.shoulder_position
        dx = point.x - base.x
        dy = point.y - base.y
        dz = point.z - base.z
        dist = math.sqrt(dx*dx + dy*dy + dz*dz)

        max_reach = 0.55  # 手臂最大长度

        if dist > max_reach:
            self.get_logger().info("手臂目标点超出最大工作范围，进行缩放")
            self.get_logger().info(f"缩放前：{point}")
            scale = max_reach / dist
            # 沿着基座到目标点的方向缩放到最大长度
            point_new = Point()
            point_new.x = round(base.x + dx * scale, 3)
            point_new.y = round(base.y + dy * scale, 3)
            point_new.z = round(base.z + dz * scale, 3)
            # 同步更新 self.ik_arm_point.pose.position
            self.ik_arm_point.pose.position.x = point_new.x
            self.ik_arm_point.pose.position.y = point_new.y
            self.ik_arm_point.pose.position.z = point_new.z
            self.get_logger().info(f"缩放后：{point_new}")
            # 计算手臂逆解目标点朝向
            qx, qy, qz, qw = self.calculate_arm_orientation(self.arm_base_position, point_new)
            # 计算目标点相对于手肘朝向
            ox, oy, oz, ow = self.calculate_object_orientation(self.arm_base_position, point_new)
        else:
            # 计算手臂逆解目标点朝向
            qx, qy, qz, qw = self.calculate_arm_orientation(self.arm_base_position, point)
            # 计算目标点相对于手肘朝向
            ox, oy, oz, ow = self.calculate_object_orientation(self.arm_base_position, point)

        self.ik_arm_point.pose.orientation.x = round(qx, 3)
        self.ik_arm_point.pose.orientation.y = round(qy, 3)
        self.ik_arm_point.pose.orientation.z = round(qz, 3)
        self.ik_arm_point.pose.orientation.w = round(qw, 3)

        # 计算抓取目标点朝向
        gx, gy, gz, gw = self.calculate_grab_orientation(self.arm_base_position, point, is_right)
        grab_goal = PoseStamped()
        grab_goal.header.frame_id = "base_link"
        grab_goal.header.stamp = self.get_clock().now().to_msg()
        grab_goal.pose.position = self.ik_arm_point.pose.position
        grab_goal.pose.orientation.x = gx
        grab_goal.pose.orientation.y = gy
        grab_goal.pose.orientation.z = gz
        grab_goal.pose.orientation.w = gw

        
        detected_object = PoseStamped()
        detected_object.header.frame_id = "base_link"
        detected_object.header.stamp = self.get_clock().now().to_msg()
        detected_object.pose.position = self.ik_arm_point.pose.position
        detected_object.pose.orientation.x = ox
        detected_object.pose.orientation.y = oy
        detected_object.pose.orientation.z = oz
        detected_object.pose.orientation.w = ow
        self.object_pub.publish(detected_object)

        shake_hands_goal = PoseStamped()
        shake_hands_goal.header.frame_id = "base_link"
        shake_hands_goal.header.stamp = self.get_clock().now().to_msg()
        shake_hands_goal.pose.position = self.ik_arm_point.pose.position
        shake_hands_goal.pose.orientation.x = qx
        shake_hands_goal.pose.orientation.y = qy
        shake_hands_goal.pose.orientation.z = qz
        shake_hands_goal.pose.orientation.w = qw

        print(self.grab)
        print(self.shake_hands)
        if self.grab:
            self.grab_pub.publish(grab_goal)
            self.get_logger().info(f"Sending grab goal: {grab_goal.pose.position}")
        elif self.shake_hands:
            self.shake_hands_pub.publish(shake_hands_goal)
            self.get_logger().info(f"Sending shake hands goal: {shake_hands_goal.pose.position}")
        else:
            # 8. 发布并返回
            self.ik_arm_pub.publish(self.ik_arm_point)
            self.get_logger().info(f"Sending ik goal: {self.ik_arm_point.pose.position}")

        self.grab = False
        self.shake_hands = False

        print("-------------------------------------------------------------------------------------------------------------------------------------------------------------------")

        result = NaviIkArm.Result()
        goal_handle.succeed()
        result.position_x_delta = goal_handle.request.position_x
        result.position_y_delta = goal_handle.request.position_y
        result.position_z_delta = goal_handle.request.position_z
        result.orientation_x_delta = goal_handle.request.orientation_x
        result.orientation_y_delta = goal_handle.request.orientation_y
        result.orientation_z_delta = goal_handle.request.orientation_z
        result.orientation_w_delta = goal_handle.request.orientation_w
        return result
    
    def ik_arm_state_cb(self, msg):
        if msg.data:
            self.get_logger().info("IK solve success!")
        else:
            self.get_logger().warn("IK solve failed!")

    def target_changed_cb(self, goal_handle):
        self.get_logger().info(f"Received target changed notification")
        target_changed = True
        if target_changed:
            self.target_changed_pub.publish(Bool(data=True))
        else:
            return
        target_changed = False
        goal_handle.succeed()
        result = NaviTargetChanged.Result()
        return result
    



def main(args=None):
    rclpy.init(args=args)

    server = KuavoActionServer()

    try:
        rclpy.spin(server)
    except KeyboardInterrupt:
        server.get_logger().info('KeyboardInterrupt caught. Shutting down.')
    finally:
        server.forward_server.destroy()
        server.rotation_server.destroy()
        server.waypoint_server.destroy()
        server.head_server.destroy()
        server.arm_server.destroy()
        server.ik_arm_server.destroy()
        server.grab_server.destroy()
        server.target_changer_server.destroy()
        rclpy.shutdown()

if __name__ == '__main__':
    main()