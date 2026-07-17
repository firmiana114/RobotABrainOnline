- 介绍：该项目实现对清华VLN导航交互的接口桥接，包含三个接口，详细信息如下

**Forward Interface**

    # goal: distance to move forward (unit: meters)
    float32 distance
    ---
    # result: delta to the starting position
    float32 delta
    ---
    # feedback: remaining to move
    float32 remaining

**Rotation Interface**

    # goal
    float32 theta
    ---
    # result: delta to the starting position
    float32 delta
    ---
    # feedback: remaining to move
    float32 remaining


**Way Point Interface**

    # goal
    float32 x
    float32 y
    ---
    # result: delta to the starting position
    float32 x_delta
    float32 y_delta
    ---
    # feedback: remaining x, y to move
    float32 x_remaining
    float32 y_remaining



- 环境要求：`Ubuntu 20.04`、`ROS1 noetic`、`ROS2 foxy` 及 `ros_bridge`，需要用户自行配置以上环境之后，按照如下目录构建工作空间进行编译运行
- 目录格式：

      ros2_ws/
        │
        ├── build/
        ├── install/
        ├── log/
        └── src/
            └── custom_action_interfaces/
                ├── action
                    ├── NaviForward.action
                    ├── NaviRotate.action
                    └── NaviWayPoint.action
                ├── include
                ├── scripts
                    ├── kuavo_action_server.py
                    ├── navi_bot.py
                    ├── navi_forward_action_client.py
                    ├── navi_forward_action_servery.py
                    ├── navi_rotate_action_client.py
                    ├── navi_rotate_action_servery.py
                    ├── navi_way_point_action_client.py
                    └── navi_way_point_action_servery.py
                ├── src
                └── readme.md

               

- 启动步骤：(包含前进、旋转、导航以及头部运动接口)
  1. 终端1：ROS1系统下，启动机器人（仿真/实物）

         cd ~/kuavo-ros-control
         source ./devel/setup.bash
         roslaunch humanoid_controllers load_kuavo_real.launch (实物)
         roslaunch humanoid_controllers load_kuavo_gazebo.launch (仿真)


  2. 终端2：ROS2系统下，启动ros1_bridge桥接

         cd ~/kuavo-vln_ws/vln/ros2_ws
         source ./install/setup.bash
         ros2 run ros1_bridge dynamic_bridge --bridge-all-topics

  3. 终端3：ROS2系统下，启动action_server

         cd ~/kuavo-vln_ws/vln/ros2_ws
         source ./install/setup.bash
         cd src/custom_action_interfaces/scripts
         python3 kuavo_action_server.py
  
  4. 终端4：ROS1系统下，启动导航

         cd ~/kuavo_ros_application
         source ./devel/setup.bash
         roslaunch kuavo_navigation kuavo_navigation.launch 

- 手部运动启动步骤：

    1. 编译功能包

            cd ~/kuavo-ros-control
            catkin build humanoid_plan_arm_trajectory

    2. 在前面启动前进、旋转、导航的前提下，再另起终端，在ROS1系统下，启动手部运动节点

            cd ~/kuavo-ros-control
            source ./devel/setup.bash
            roslaunch humanoid_plan_arm_trajectory humanoid_plan_arm_trajectory.launch
