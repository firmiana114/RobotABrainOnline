#pragma once

/**
 * @file goGoalNavigation_ros2.h
 * @brief 混合架构导航节点声明 - ROS2 话题订阅 + Unitree SLAM 控制
 */

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/u_int8.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <unitree/robot/client/client.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>
#include <unitree/robot/g1/loco/g1_loco_client.hpp>
#include <unitree/idl/ros2/String_.hpp>

#include <custom_action_interfaces/action/navi_forward.hpp>
#include <custom_action_interfaces/action/navi_rotate.hpp>
#include <custom_action_interfaces/action/navi_way_point.hpp>

#include "json.hpp"

#include <atomic>
#include <future>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <cstdlib>

#define SlamInfoTopic "rt/slam_info"
#define SlamKeyInfoTopic "rt/slam_key_info"

class PoseData
{
public:
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
    float q_x = 0.0f;
    float q_y = 0.0f;
    float q_z = 0.0f;
    float q_w = 1.0f;
    float speed = -1.0f;
    int mode = 1;

    std::string toJsonStr() const;
    void printInfo(const std::string& prefix = "") const;
};

namespace unitree::robot::slam
{

extern const std::string TEST_SERVICE_NAME;
extern const std::string TEST_API_VERSION;

extern const int32_t ROBOT_API_ID_STOP_NODE;
extern const int32_t ROBOT_API_ID_START_MAPPING_PL;
extern const int32_t ROBOT_API_ID_END_MAPPING_PL;
extern const int32_t ROBOT_API_ID_START_RELOCATION_PL;
extern const int32_t ROBOT_API_ID_POSE_NAV_PL;
extern const int32_t ROBOT_API_ID_PAUSE_NAV;
extern const int32_t ROBOT_API_ID_RESUME_NAV;
extern const int32_t ROBOT_API_ID_JOY_CTRL;

class SlamClient : public unitree::robot::Client
{
private:
    unitree::robot::ChannelSubscriberPtr<std_msgs::msg::dds_::String_> subSlamInfo;
    unitree::robot::ChannelSubscriberPtr<std_msgs::msg::dds_::String_> subSlamKeyInfo;

    PoseData curPose;
    std::mutex poseMutex;
    std::atomic<bool> is_arrived{false};
    std::atomic<bool> threadControl{false};
    std::future<void> futThread;
    std::promise<void> prom;
    std::thread controlThread;

    void slamInfoHandler(const void* message);
    void slamKeyInfoHandler(const void* message);

public:
    SlamClient();
    ~SlamClient() override;

    void Init() override;

    void stopNodeFun();
    void startMappingPlFun();
    void endMappingPlFun(const std::string& pcdPath = "/home/unitree/test1.pcd");
    void relocationPlFun(const std::string& pcdPath = "/home/unitree/test.pcd");
    void pauseNavFun();
    void resumeNavFun();
    void joyCtrlFun(float lx, float ly, float rx);

    void navigateToGoal(const PoseData& goal);
    void forward(float distance, float speed = 0.0f);
    void rotate(float theta_deg);
    void stopMove();
    void taskLoopFun(std::promise<void>& prom, const PoseData goal);
    void taskThreadRun(const PoseData& goal);
    void taskThreadStop();

    PoseData getCurrentPose();
    bool hasValidPose();
    bool isArrived() const;
};

} // namespace unitree::robot::slam

class HybridNavigationNode : public rclcpp::Node
{
private:
    using NaviForward = custom_action_interfaces::action::NaviForward;
    using NaviRotate = custom_action_interfaces::action::NaviRotate;
    using NaviWayPoint = custom_action_interfaces::action::NaviWayPoint;

    using GoalHandleNaviForward = rclcpp_action::ServerGoalHandle<NaviForward>;
    using GoalHandleNaviRotate = rclcpp_action::ServerGoalHandle<NaviRotate>;
    using GoalHandleNaviWayPoint = rclcpp_action::ServerGoalHandle<NaviWayPoint>;

    std::shared_ptr<unitree::robot::slam::SlamClient> slamClient_;
    std::shared_ptr<unitree::robot::g1::LocoClient> locoClient_;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr goalSub_;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr cmdSub_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr statusPub_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr posePub_;
    rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr lioPosePub_;
    rclcpp::Publisher<std_msgs::msg::UInt8>::SharedPtr kuavoNavStatePub_;
    rclcpp::TimerBase::SharedPtr statusTimer_;
    rclcpp::TimerBase::SharedPtr poseTimer_;

    rclcpp_action::Server<NaviForward>::SharedPtr naviForwardServer_;
    rclcpp_action::Server<NaviRotate>::SharedPtr naviRotateServer_;
    rclcpp_action::Server<NaviWayPoint>::SharedPtr naviWayPointServer_;

    std::atomic<bool> isNavigating_{false};
    std::atomic<bool> isRelocated_{false};
    std::atomic<bool> restartPromptActive_{false};
    std::atomic<uint64_t> restartPromptGeneration_{0};
    std::atomic<uint64_t> relocationGeneration_{0};

    std::string networkInterface_;
    std::string armActionExecutable_;
    std::mutex armProcessMutex_;
    std::mutex relocationThreadMutex_;
    std::thread relocationThread_;
    std::atomic<int> armActionPid_{-1};
    float defaultNavSpeed_ = -1.0f;

    bool switchToFsmId(int fsmId);
    void startRelocationLoop(const std::string& pcdPath, const std::string& logPrefix);
    bool startArmActionNode();
    void stopArmActionNode();
    bool stopArmActionNodeSafe();
    void promptRestartAfterArrival();

    void goalCallback(const std_msgs::msg::String::SharedPtr msg);
    void cmdCallback(const std_msgs::msg::String::SharedPtr msg);
    void statusTimerCallback();
    void poseTimerCallback();

public:
    HybridNavigationNode(const std::string& networkInterface, const std::string& pcdPath = "", float defaultNavSpeed = -1.0f);
    ~HybridNavigationNode() override;
};
