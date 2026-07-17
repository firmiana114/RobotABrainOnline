/**
 * @file goGoalNavigation_ros2.cc
 * @brief 混合架构导航节点实现 - ROS2 话题订阅 + Unitree SLAM 控制
 *
 * 基于 keyDemo.cpp 改造，支持通过 ROS2 话题发送目标位置
 */

#include "goGoalNavigation_ros2.h"

#include <chrono>
#include <cmath>
#include <csignal>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <memory>
#include <poll.h>
#include <sys/wait.h>
#include <unistd.h>

using namespace unitree::robot;
using namespace unitree::common;

namespace {
std::atomic<bool> g_current_goal_is_midpoint{false};


std::string getSiblingExecutablePath(const std::string& executableName)
{
    char exePathBuffer[4096] = {};
    const ssize_t len = readlink("/proc/self/exe", exePathBuffer, sizeof(exePathBuffer) - 1);
    if (len <= 0) {
        return executableName;
    }

    std::filesystem::path exePath(std::string(exePathBuffer, static_cast<size_t>(len)));
    return (exePath.parent_path() / executableName).string();
}

bool isProcessAlive(int pid)
{
    return pid > 0 && kill(static_cast<pid_t>(pid), 0) == 0;
}

bool waitForProcessExit(pid_t pid, std::chrono::milliseconds timeout)
{
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        int status = 0;
        const pid_t ret = waitpid(pid, &status, WNOHANG);
        if (ret == pid) {
            return true;
        }
        if (ret < 0) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    return false;
}

bool isNear(double a, double b, double eps = 0.05)
{
    return std::fabs(a - b) <= eps;
}

bool isMidTransitionPoint(const PoseData& goal)
{
    // 三个中间过渡点（允许少量浮点误差）
    return (isNear(goal.x, 2.9386) && isNear(goal.y, -4.9274)) ||
           (isNear(goal.x, 16.8283) && isNear(goal.y, 7.5128)) ||
           (isNear(goal.x, 11.7036) && isNear(goal.y, 16.2018));
}

double yawFromQuat(const PoseData& p)
{
    const double siny_cosp = 2.0 * (static_cast<double>(p.q_w) * static_cast<double>(p.q_z) +
                                    static_cast<double>(p.q_x) * static_cast<double>(p.q_y));
    const double cosy_cosp = 1.0 - 2.0 * (static_cast<double>(p.q_y) * static_cast<double>(p.q_y) +
                                          static_cast<double>(p.q_z) * static_cast<double>(p.q_z));
    return std::atan2(siny_cosp, cosy_cosp);
}

void setQuatFromYaw(PoseData& p, double yaw)
{
    p.q_x = 0.0f;
    p.q_y = 0.0f;
    p.q_z = static_cast<float>(std::sin(yaw * 0.5));
    p.q_w = static_cast<float>(std::cos(yaw * 0.5));
}
} // namespace

std::string PoseData::toJsonStr() const
{
    nlohmann::json j;
    j["data"]["targetPose"]["x"] = x;
    j["data"]["targetPose"]["y"] = y;
    j["data"]["targetPose"]["z"] = z;
    j["data"]["targetPose"]["q_x"] = q_x;
    j["data"]["targetPose"]["q_y"] = q_y;
    j["data"]["targetPose"]["q_z"] = q_z;
    j["data"]["targetPose"]["q_w"] = q_w;
    j["data"]["mode"] = mode;
    if (speed > 0.0f) {
        j["data"]["speed"] = speed;
    }
    return j.dump(4);
}

void PoseData::printInfo(const std::string& prefix) const
{
    std::cout << prefix << "x:" << x << " y:" << y << " z:" << z
              << " q_x:" << q_x << " q_y:" << q_y << " q_z:" << q_z
              << " q_w:" << q_w;
    if (speed > 0.0f) {
        std::cout << " speed:" << speed;
    }
    std::cout << std::endl;
}

namespace unitree::robot::slam
{

const std::string TEST_SERVICE_NAME = "slam_operate";
const std::string TEST_API_VERSION = "1.0.0.1";

const int32_t ROBOT_API_ID_STOP_NODE = 1901;
const int32_t ROBOT_API_ID_START_MAPPING_PL = 1801;
const int32_t ROBOT_API_ID_END_MAPPING_PL = 1802;
const int32_t ROBOT_API_ID_START_RELOCATION_PL = 1804;
const int32_t ROBOT_API_ID_POSE_NAV_PL = 1102;
const int32_t ROBOT_API_ID_PAUSE_NAV = 1201;
const int32_t ROBOT_API_ID_RESUME_NAV = 1202;
const int32_t ROBOT_API_ID_JOY_CTRL = 1008;

SlamClient::SlamClient() : Client(TEST_SERVICE_NAME, false)
{
    subSlamInfo = ChannelSubscriberPtr<std_msgs::msg::dds_::String_>(
        new ChannelSubscriber<std_msgs::msg::dds_::String_>(SlamInfoTopic));
    subSlamInfo->InitChannel(std::bind(&SlamClient::slamInfoHandler, this, std::placeholders::_1), 1);

    subSlamKeyInfo = ChannelSubscriberPtr<std_msgs::msg::dds_::String_>(
        new ChannelSubscriber<std_msgs::msg::dds_::String_>(SlamKeyInfoTopic));
    subSlamKeyInfo->InitChannel(std::bind(&SlamClient::slamKeyInfoHandler, this, std::placeholders::_1), 1);
}

SlamClient::~SlamClient()
{
    taskThreadStop();
    stopNodeFun();
}

void SlamClient::Init()
{
    SetApiVersion(TEST_API_VERSION);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_POSE_NAV_PL);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_PAUSE_NAV);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_RESUME_NAV);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_STOP_NODE);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_START_MAPPING_PL);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_END_MAPPING_PL);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_START_RELOCATION_PL);
    UT_ROBOT_CLIENT_REG_API_NO_PROI(ROBOT_API_ID_JOY_CTRL);
}

void SlamClient::slamInfoHandler(const void* message)
{
    std_msgs::msg::dds_::String_ currentMsg = *(std_msgs::msg::dds_::String_*)message;
    try {
        nlohmann::json jsonData = nlohmann::json::parse(currentMsg.data());

        if (jsonData.contains("errorCode") && jsonData["errorCode"].get<int>() != 0) {
            std::cout << "\033[33m[SLAM Info] " << jsonData["info"] << "\033[0m" << std::endl;
            return;
        }

        if (jsonData.contains("type") && jsonData["type"] == "pos_info") {
            std::lock_guard<std::mutex> lock(poseMutex);
            curPose.x = jsonData["data"]["currentPose"]["x"];
            curPose.y = jsonData["data"]["currentPose"]["y"];
            curPose.z = jsonData["data"]["currentPose"]["z"];
            curPose.q_x = jsonData["data"]["currentPose"]["q_x"];
            curPose.q_y = jsonData["data"]["currentPose"]["q_y"];
            curPose.q_z = jsonData["data"]["currentPose"]["q_z"];
            curPose.q_w = jsonData["data"]["currentPose"]["q_w"];
        }
    } catch (const std::exception&) {
        // 忽略解析错误
    }
}

void SlamClient::slamKeyInfoHandler(const void* message)
{
    std_msgs::msg::dds_::String_ currentMsg = *(std_msgs::msg::dds_::String_*)message;
    try {
        nlohmann::json jsonData = nlohmann::json::parse(currentMsg.data());

        if (jsonData.contains("errorCode") && jsonData["errorCode"].get<int>() != 0) {
            return;
        }

        std::string typeStr;
        if (jsonData["type"].is_number()) {
            typeStr = std::to_string(jsonData["type"].get<int>());
        } else if (jsonData["type"].is_string()) {
            typeStr = jsonData["type"].get<std::string>();
        }

        if (typeStr == "1" || typeStr == "task_result") {
            if (jsonData.contains("data") && jsonData["data"].contains("is_arrived")) {
                is_arrived.store(jsonData["data"]["is_arrived"].get<bool>());
                if (is_arrived.load()) {
                    std::cout << "\033[32m[SLAM] Arrived at target!\033[0m" << std::endl;
                } else {
                    std::string targetName = jsonData["data"].value("targetNodeName", "unknown");
                    std::cout << "[SLAM] Navigating to: " << targetName << std::endl;
                }
            }
        }
    } catch (const std::exception&) {
        // 忽略解析错误
    }
}

void SlamClient::stopNodeFun()
{
    std::string parameter = R"({"data": {}})";
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_STOP_NODE, parameter, data);
    std::cout << "[Stop Node] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::startMappingPlFun()
{
    std::string parameter = R"({"data": {"slam_type": "indoor"}})";
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_START_MAPPING_PL, parameter, data);
    std::cout << "[Start Mapping] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::endMappingPlFun(const std::string& pcdPath)
{
    std::string parameter = R"({"data": {"address": ")" + pcdPath + R"("}})";
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_END_MAPPING_PL, parameter, data);
    std::cout << "[End Mapping] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::relocationPlFun(const std::string& pcdPath)
{
    std::string parameter = R"({
        "data": {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "q_x": 0.0,
            "q_y": 0.0,
            "q_z": 0.0,
            "q_w": 1.0,
            "address": ")" + pcdPath + R"("
        }
    })";
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_START_RELOCATION_PL, parameter, data);
    std::cout << "[Start Relocation] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::pauseNavFun()
{
    std::string parameter = R"({"data": {}})";
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_PAUSE_NAV, parameter, data);
    std::cout << "[Pause Nav] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::resumeNavFun()
{
    std::string parameter = R"({"data": {}})";
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_RESUME_NAV, parameter, data);
    std::cout << "[Resume Nav] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::joyCtrlFun(float lx, float ly, float rx)
{
    nlohmann::json j;
    j["data"]["ly"] = ly;
    j["data"]["lx"] = lx;
    j["data"]["rx"] = rx;

    std::string parameter = j.dump();
    std::string data;
    int32_t statusCode = Call(ROBOT_API_ID_JOY_CTRL, parameter, data);
    std::cout << "[Joy Ctrl] statusCode:" << statusCode << " data:" << data << std::endl;
}

void SlamClient::navigateToGoal(const PoseData& goal)
{
    taskThreadRun(goal);
}

void SlamClient::forward(float distance, float speed)
{
    if (!hasValidPose()) {
        std::cout << "[Forward] Invalid pose, skip" << std::endl;
        return;
    }

    if (speed > 0.0f) {
        std::cout << "[Forward] Requested speed=" << speed
                  << " m/s (currently advisory, underlying nav uses internal speed profile)" << std::endl;
    }

    const PoseData current = getCurrentPose();
    PoseData target = current;
    const double yaw = yawFromQuat(current);
    target.x = current.x + static_cast<float>(distance * std::cos(yaw));
    target.y = current.y + static_cast<float>(distance * std::sin(yaw));
    target.mode = 1;
    navigateToGoal(target);
}

void SlamClient::rotate(float theta_deg)
{
    if (!hasValidPose()) {
        std::cout << "[Rotate] Invalid pose, skip" << std::endl;
        return;
    }

    PoseData target = getCurrentPose();
    const double yaw = yawFromQuat(target);
    const double next_yaw = yaw + theta_deg * M_PI / 180.0;
    setQuatFromYaw(target, next_yaw);
    target.mode = 1;
    navigateToGoal(target);
}

void SlamClient::stopMove()
{
    taskThreadStop();
    pauseNavFun();
    joyCtrlFun(0.0f, 0.0f, 0.0f);
}

void SlamClient::taskThreadRun(const PoseData& goal)
{
    taskThreadStop();
    prom = std::promise<void>();
    futThread = prom.get_future();
    controlThread = std::thread(&SlamClient::taskLoopFun, this, std::ref(prom), goal);
    controlThread.detach();
}

void SlamClient::taskLoopFun(std::promise<void>& promise, const PoseData goal)
{
    std::string data;
    threadControl.store(true);
    is_arrived.store(false);

    std::cout << "\033[36m[Navigation] Starting navigation to goal:\033[0m" << std::endl;
    goal.printInfo("  Target: ");

    int32_t statusCode = Call(ROBOT_API_ID_POSE_NAV_PL, goal.toJsonStr(), data);
    std::cout << "[Navigation] parameter:" << goal.toJsonStr() << std::endl;
    std::cout << "[Navigation] statusCode:" << statusCode << " data:" << data << std::endl;

    while (!is_arrived.load() && threadControl.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    if (is_arrived.load()) {
        std::cout << "\033[32m[Navigation] Successfully arrived at goal!\033[0m" << std::endl;
    } else {
        std::cout << "\033[33m[Navigation] Navigation stopped\033[0m" << std::endl;
    }

    promise.set_value();
}

void SlamClient::taskThreadStop()
{
    threadControl.store(false);
    if (futThread.valid()) {
        auto status = futThread.wait_for(std::chrono::milliseconds(0));
        if (status != std::future_status::ready) {
            futThread.wait();
        }
    }
}

PoseData SlamClient::getCurrentPose()
{
    std::lock_guard<std::mutex> lock(poseMutex);
    return curPose;
}

bool SlamClient::hasValidPose()
{
    std::lock_guard<std::mutex> lock(poseMutex);
    return curPose.q_w != 1.0f || curPose.x != 0.0f;
}

bool SlamClient::isArrived() const
{
    return is_arrived.load();
}

} // namespace unitree::robot::slam

HybridNavigationNode::HybridNavigationNode(const std::string& networkInterface, const std::string& pcdPath, float defaultNavSpeed)
    : Node("hybrid_navigation_node"),
      networkInterface_(networkInterface),
      armActionExecutable_(getSiblingExecutablePath("g1Arm7DIYActionExample")),
      defaultNavSpeed_(defaultNavSpeed)
{
    unitree::robot::ChannelFactory::Instance()->Init(0, networkInterface);

    slamClient_ = std::make_shared<unitree::robot::slam::SlamClient>();
    slamClient_->Init();
    slamClient_->SetTimeout(10.0f);

    locoClient_ = std::make_shared<unitree::robot::g1::LocoClient>();
    locoClient_->Init();
    locoClient_->SetTimeout(10.0f);

    goalSub_ = this->create_subscription<std_msgs::msg::String>(
        "/go_goal_pose", 10,
        std::bind(&HybridNavigationNode::goalCallback, this, std::placeholders::_1));

    cmdSub_ = this->create_subscription<std_msgs::msg::String>(
        "/slam_cmd", 10,
        std::bind(&HybridNavigationNode::cmdCallback, this, std::placeholders::_1));

    statusPub_ = this->create_publisher<std_msgs::msg::String>("/nav_status", 10);
    posePub_ = this->create_publisher<std_msgs::msg::String>("/current_pose", 10);
    lioPosePub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>("/lio_pose", 10);
    kuavoNavStatePub_ = this->create_publisher<std_msgs::msg::UInt8>("/kuavo_navigation_state", 10);

    naviForwardServer_ = rclcpp_action::create_server<NaviForward>(
        this,
        "navi_forward",
        [this](const rclcpp_action::GoalUUID&, std::shared_ptr<const NaviForward::Goal>) {
            return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
        },
        [this](const std::shared_ptr<GoalHandleNaviForward>) {
            slamClient_->stopMove();
            return rclcpp_action::CancelResponse::ACCEPT;
        },
        [this](const std::shared_ptr<GoalHandleNaviForward> goal_handle) {
            slamClient_->forward(goal_handle->get_goal()->distance);
            auto result = std::make_shared<NaviForward::Result>();
            result->delta = goal_handle->get_goal()->distance;
            goal_handle->succeed(result);
        });

    naviRotateServer_ = rclcpp_action::create_server<NaviRotate>(
        this,
        "navi_rotate",
        [this](const rclcpp_action::GoalUUID&, std::shared_ptr<const NaviRotate::Goal>) {
            return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
        },
        [this](const std::shared_ptr<GoalHandleNaviRotate>) {
            slamClient_->stopMove();
            return rclcpp_action::CancelResponse::ACCEPT;
        },
        [this](const std::shared_ptr<GoalHandleNaviRotate> goal_handle) {
            slamClient_->rotate(goal_handle->get_goal()->theta);
            auto result = std::make_shared<NaviRotate::Result>();
            result->delta = goal_handle->get_goal()->theta;
            goal_handle->succeed(result);
        });

    naviWayPointServer_ = rclcpp_action::create_server<NaviWayPoint>(
        this,
        "navi_way_point",
        [this](const rclcpp_action::GoalUUID&, std::shared_ptr<const NaviWayPoint::Goal>) {
            return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
        },
        [this](const std::shared_ptr<GoalHandleNaviWayPoint>) {
            slamClient_->stopMove();
            isNavigating_.store(false);

            auto statusMsg = std_msgs::msg::String();
            statusMsg.data = "preempted";
            statusPub_->publish(statusMsg);

            auto navStateMsg = std_msgs::msg::UInt8();
            navStateMsg.data = 2;
            kuavoNavStatePub_->publish(navStateMsg);
            return rclcpp_action::CancelResponse::ACCEPT;
        },
        [this](const std::shared_ptr<GoalHandleNaviWayPoint> goal_handle) {
            PoseData goal;
            goal.x = goal_handle->get_goal()->position_x;
            goal.y = goal_handle->get_goal()->position_y;
            goal.z = goal_handle->get_goal()->position_z;
            goal.q_x = goal_handle->get_goal()->orientation_x;
            goal.q_y = goal_handle->get_goal()->orientation_y;
            goal.q_z = goal_handle->get_goal()->orientation_z;
            goal.q_w = goal_handle->get_goal()->orientation_w;
            goal.mode = 1;
            goal.speed = defaultNavSpeed_;

            if (!slamClient_->hasValidPose()) {
                RCLCPP_WARN(get_logger(), "[ActionGoal] No valid pose. Please start relocation first.");
                auto result = std::make_shared<NaviWayPoint::Result>();
                goal_handle->abort(result);
                return;
            }

            if (!stopArmActionNodeSafe()) {
                RCLCPP_ERROR(get_logger(), "[ActionGoal] Failed to stop arm action node before fsm_id=801");
                auto result = std::make_shared<NaviWayPoint::Result>();
                goal_handle->abort(result);
                return;
            }

            if (!switchToFsmId(801)) {
                RCLCPP_ERROR(get_logger(), "[ActionGoal] Failed to switch to walk locomotion mode (fsm_id=801)");
                auto result = std::make_shared<NaviWayPoint::Result>();
                goal_handle->abort(result);
                return;
            }

            const bool isMidGoal = isMidTransitionPoint(goal);
            g_current_goal_is_midpoint.store(isMidGoal);
            if (isMidGoal) {
                RCLCPP_INFO(get_logger(), "[ActionGoal] This target is configured as a midpoint transition");
            }

            isNavigating_.store(true);
            slamClient_->navigateToGoal(goal);

            auto statusMsg = std_msgs::msg::String();
            statusMsg.data = "navigating";
            statusPub_->publish(statusMsg);

            auto navStateMsg = std_msgs::msg::UInt8();
            navStateMsg.data = 1;
            kuavoNavStatePub_->publish(navStateMsg);

            auto result = std::make_shared<NaviWayPoint::Result>();
            result->position_x_delta = 0.0f;
            result->position_y_delta = 0.0f;
            result->position_z_delta = 0.0f;
            result->orientation_x_delta = 0.0f;
            result->orientation_y_delta = 0.0f;
            result->orientation_z_delta = 0.0f;
            result->orientation_w_delta = 0.0f;
            goal_handle->succeed(result);
        });

    statusTimer_ = this->create_wall_timer(
        std::chrono::milliseconds(200),
        std::bind(&HybridNavigationNode::statusTimerCallback, this));

    poseTimer_ = this->create_wall_timer(
        std::chrono::seconds(10),
        std::bind(&HybridNavigationNode::poseTimerCallback, this));

    RCLCPP_INFO(get_logger(), "===========================================");
    RCLCPP_INFO(get_logger(), "Hybrid Navigation Node (G1-Edu)");
    RCLCPP_INFO(get_logger(), "===========================================");
    RCLCPP_INFO(get_logger(), "ROS2 Topics:");
    RCLCPP_INFO(get_logger(), "  /go_goal_pose      - Send goal pose");
    RCLCPP_INFO(get_logger(), "  /slam_cmd          - SLAM commands");
    RCLCPP_INFO(get_logger(), "  /current_pose      - Current pose (published)");
    RCLCPP_INFO(get_logger(), "  /lio_pose          - Current pose for kuavo high-level (PoseStamped)");
    if (defaultNavSpeed_ > 0.0f) {
        RCLCPP_INFO(get_logger(), "  default nav speed  - %.3f m/s", defaultNavSpeed_);
    }
    RCLCPP_INFO(get_logger(), "===========================================");
    RCLCPP_INFO(get_logger(), "SLAM Commands (publish to /slam_cmd):");
    RCLCPP_INFO(get_logger(), "  {\"cmd\":\"start_mapping\"}");
    RCLCPP_INFO(get_logger(), "  {\"cmd\":\"end_mapping\",\"pcd\":\"/path/map.pcd\"}");
    RCLCPP_INFO(get_logger(), "  {\"cmd\":\"start_relocation\",\"pcd\":\"/path/map.pcd\"}");
    RCLCPP_INFO(get_logger(), "  {\"cmd\":\"pause_nav\"}");
    RCLCPP_INFO(get_logger(), "  {\"cmd\":\"resume_nav\"}");
    RCLCPP_INFO(get_logger(), "  {\"cmd\":\"stop_nav\"}");
    RCLCPP_INFO(get_logger(), "===========================================");

    if (!pcdPath.empty()) {
        RCLCPP_INFO(get_logger(), "\033[36m[Auto-Relocation] Loading map: %s\033[0m", pcdPath.c_str());
        startRelocationLoop(pcdPath, "Auto-Relocation");
    } else {
        RCLCPP_INFO(get_logger(), "\033[33m[Info] No PCD path specified. Use /slam_cmd to start relocation.\033[0m");
    }
}

HybridNavigationNode::~HybridNavigationNode()
{
    restartPromptGeneration_.fetch_add(1);
    relocationGeneration_.fetch_add(1);
    {
        std::lock_guard<std::mutex> lock(relocationThreadMutex_);
        if (relocationThread_.joinable()) {
            relocationThread_.join();
        }
    }
    stopArmActionNode();
    slamClient_->taskThreadStop();
}

void HybridNavigationNode::startRelocationLoop(const std::string& pcdPath, const std::string& logPrefix)
{
    relocationGeneration_.fetch_add(1);

    {
        std::lock_guard<std::mutex> lock(relocationThreadMutex_);
        if (relocationThread_.joinable()) {
            relocationThread_.join();
        }

        const uint64_t generation = relocationGeneration_.load();
        relocationThread_ = std::thread([this, pcdPath, logPrefix, generation]() {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            isRelocated_.store(false);

            int attempt = 0;
            while (rclcpp::ok() && relocationGeneration_.load() == generation) {
                ++attempt;
                RCLCPP_INFO(get_logger(), "[%s] Attempt %d: start relocation with map %s",
                            logPrefix.c_str(), attempt, pcdPath.c_str());
                slamClient_->relocationPlFun(pcdPath);
                RCLCPP_INFO(get_logger(), "[%s] Waiting for localization...", logPrefix.c_str());
                std::this_thread::sleep_for(std::chrono::seconds(2));

                int retry = 0;
                while (rclcpp::ok() && relocationGeneration_.load() == generation && retry < 15) {
                    if (slamClient_->hasValidPose()) {
                        PoseData currentPose = slamClient_->getCurrentPose();
                        isRelocated_.store(true);
                        RCLCPP_INFO(get_logger(), "\033[32m[%s] Success! Current pose:\033[0m", logPrefix.c_str());
                        RCLCPP_INFO(get_logger(), "  x: %.4f  y: %.4f  z: %.4f",
                                    currentPose.x, currentPose.y, currentPose.z);
                        RCLCPP_INFO(get_logger(), "  ox: %.4f  oy: %.4f  oz: %.4f  ow: %.4f",
                                    currentPose.q_x, currentPose.q_y, currentPose.q_z, currentPose.q_w);
                        RCLCPP_INFO(get_logger(), "\033[32m[Ready] Navigation system ready for commands!\033[0m");
                        return;
                    }
                    std::this_thread::sleep_for(std::chrono::milliseconds(500));
                    ++retry;
                }

                if (!rclcpp::ok() || relocationGeneration_.load() != generation) {
                    return;
                }

                RCLCPP_WARN(get_logger(), "\033[33m[%s] Attempt %d failed: no valid pose received\033[0m",
                            logPrefix.c_str(), attempt);
                RCLCPP_WARN(get_logger(), "[%s] Move the robot to a better position. Retrying in 1 second...",
                            logPrefix.c_str());
                std::this_thread::sleep_for(std::chrono::seconds(1));
            }
        });
    }
}

bool HybridNavigationNode::startArmActionNode()
{
    std::lock_guard<std::mutex> lock(armProcessMutex_);

    int existingPid = armActionPid_.load();
    if (isProcessAlive(existingPid)) {
        return true;
    }
    if (existingPid > 0) {
        int status = 0;
        waitpid(static_cast<pid_t>(existingPid), &status, WNOHANG);
        armActionPid_.store(-1);
    }

    if (access(armActionExecutable_.c_str(), X_OK) != 0) {
        RCLCPP_ERROR(get_logger(), "[ArmAction] Executable not found or not executable: %s", armActionExecutable_.c_str());
        return false;
    }

    const pid_t pid = fork();
    if (pid < 0) {
        RCLCPP_ERROR(get_logger(), "[ArmAction] fork() failed while starting g1Arm7DIYActionExample");
        return false;
    }

    if (pid == 0) {
        setsid();
        const int logFd = open("/tmp/g1Arm7DIYActionExample.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (logFd >= 0) {
            dup2(logFd, STDOUT_FILENO);
            dup2(logFd, STDERR_FILENO);
            close(logFd);
        }
        execl(armActionExecutable_.c_str(),
              armActionExecutable_.c_str(),
              networkInterface_.c_str(),
              static_cast<char*>(nullptr));
        _exit(127);
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    int status = 0;
    const pid_t exited = waitpid(pid, &status, WNOHANG);
    if (exited == pid) {
        RCLCPP_ERROR(get_logger(), "[ArmAction] g1Arm7DIYActionExample exited immediately, status=%d", status);
        return false;
    }

    armActionPid_.store(static_cast<int>(pid));
    RCLCPP_INFO(get_logger(), "[ArmAction] Started g1Arm7DIYActionExample pid=%d", static_cast<int>(pid));
    return true;
}

void HybridNavigationNode::stopArmActionNode()
{
    std::lock_guard<std::mutex> lock(armProcessMutex_);

    const int pidValue = armActionPid_.exchange(-1);
    const pid_t pid = static_cast<pid_t>(pidValue);
    if (pid > 0 && isProcessAlive(pidValue)) {
        kill(pid, SIGINT);
        if (!waitForProcessExit(pid, std::chrono::seconds(3))) {
            RCLCPP_WARN(get_logger(), "[ArmAction] pid=%d did not stop after SIGINT, sending SIGTERM", pidValue);
            kill(pid, SIGTERM);
            if (!waitForProcessExit(pid, std::chrono::seconds(1))) {
                RCLCPP_WARN(get_logger(), "[ArmAction] pid=%d did not stop after SIGTERM, sending SIGKILL", pidValue);
                kill(pid, SIGKILL);
                waitForProcessExit(pid, std::chrono::seconds(1));
            }
        }
        RCLCPP_INFO(get_logger(), "[ArmAction] Stopped g1Arm7DIYActionExample pid=%d", pidValue);
        return;
    }

    const int ret = std::system("pkill -INT -f '[g]1Arm7DIYActionExample' >/dev/null 2>&1");
    if (ret == 0) {
        RCLCPP_INFO(get_logger(), "[ArmAction] Stopped external g1Arm7DIYActionExample process");
    }
}

bool HybridNavigationNode::stopArmActionNodeSafe()
{
    stopArmActionNode();
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    return true;
}

bool HybridNavigationNode::switchToFsmId(int fsmId)
{
    if (!locoClient_) {
        RCLCPP_ERROR(get_logger(), "[FSM] Loco client not initialized");
        return false;
    }

    try {
        locoClient_->SetFsmId(fsmId);
        RCLCPP_INFO(get_logger(), "[FSM] Switched to fsm_id=%d", fsmId);
        return true;
    } catch (const std::exception& e) {
        RCLCPP_ERROR(get_logger(), "[FSM] Failed to switch to fsm_id=%d: %s", fsmId, e.what());
        return false;
    }
}

void HybridNavigationNode::promptRestartAfterArrival()
{
    bool expected = false;
    if (!restartPromptActive_.compare_exchange_strong(expected, true)) {
        return;
    }

    const uint64_t generation = restartPromptGeneration_.load();
    std::thread([this, generation]() {
        while (restartPromptGeneration_.load() == generation) {
            std::cout << "[Navigation] restart? [y/N] (auto no in 30s): " << std::flush;

            pollfd stdinPoll{};
            stdinPoll.fd = STDIN_FILENO;
            stdinPoll.events = POLLIN;
            const int pollRet = poll(&stdinPoll, 1, 30000);
            if (pollRet < 0) {
                RCLCPP_WARN(get_logger(), "[Navigation] Restart prompt input poll failed, stopping restart prompt loop");
                restartPromptActive_.store(false);
                return;
            }

            std::string input;
            if (pollRet > 0 && (stdinPoll.revents & POLLIN)) {
                if (!std::getline(std::cin, input)) {
                    RCLCPP_WARN(get_logger(), "[Navigation] Restart prompt input unavailable, stopping restart prompt loop");
                    restartPromptActive_.store(false);
                    return;
                }
            } else {
                std::cout << std::endl;
            }

            if (restartPromptGeneration_.load() != generation) {
                break;
            }

            if (input == "y" || input == "Y") {
                RCLCPP_INFO(get_logger(), "[Navigation] Restart requested: stopping arm action process and switching to fsm_id=801");
                restartPromptGeneration_.fetch_add(1);
                stopArmActionNode();
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                if (!switchToFsmId(801)) {
                    RCLCPP_ERROR(get_logger(), "[Navigation] Restart requested but failed to switch to fsm_id=801");
                    restartPromptActive_.store(false);
                    return;
                }

                auto statusMsg = std_msgs::msg::String();
                statusMsg.data = "restarted";
                statusPub_->publish(statusMsg);
                restartPromptActive_.store(false);
                return;
            }

            RCLCPP_INFO(get_logger(), "[Navigation] Restart skipped; asking again in 30s");
        }

        restartPromptActive_.store(false);
    }).detach();
}

void HybridNavigationNode::goalCallback(const std_msgs::msg::String::SharedPtr msg)
{
    restartPromptGeneration_.fetch_add(1);

    if (isNavigating_.load()) {
        RCLCPP_INFO(get_logger(), "[Goal] New goal received while navigating. Switching to latest goal...");
        slamClient_->taskThreadStop();
        isNavigating_.store(false);

        auto statusMsg = std_msgs::msg::String();
        statusMsg.data = "preempted";
        statusPub_->publish(statusMsg);

        auto navStateMsg = std_msgs::msg::UInt8();
        navStateMsg.data = 2;
        kuavoNavStatePub_->publish(navStateMsg);
    }

    RCLCPP_INFO(get_logger(), "\033[35m[Goal Received] %s\033[0m", msg->data.c_str());

    try {
        nlohmann::json jsonData = nlohmann::json::parse(msg->data);

        PoseData goal;

        if (jsonData.contains("data")) {
            auto& data = jsonData["data"];
            goal.x = data.value("x", 0.0f);
            goal.y = data.value("y", 0.0f);
            goal.z = data.value("z", 0.0f);
            goal.q_x = data.value("ox", 0.0f);
            goal.q_y = data.value("oy", 0.0f);
            goal.q_z = data.value("oz", 0.0f);
            goal.q_w = data.value("ow", 1.0f);
            goal.mode = data.value("mode", 1);
            goal.speed = data.value("speed", defaultNavSpeed_);
        } else {
            goal.x = jsonData.value("x", 0.0f);
            goal.y = jsonData.value("y", 0.0f);
            goal.z = jsonData.value("z", 0.0f);
            goal.q_x = jsonData.value("ox", 0.0f);
            goal.q_y = jsonData.value("oy", 0.0f);
            goal.q_z = jsonData.value("oz", 0.0f);
            goal.q_w = jsonData.value("ow", 1.0f);
            goal.mode = jsonData.value("mode", 1);
            goal.speed = jsonData.value("speed", defaultNavSpeed_);
        }

        if (slamClient_->hasValidPose()) {
            PoseData current = slamClient_->getCurrentPose();
            current.printInfo("  Current: ");
            RCLCPP_INFO(get_logger(), "\033[32m[OK] System localized, starting navigation...\033[0m");
        } else {
            RCLCPP_WARN(get_logger(), "\033[33m[Warn] No valid pose! Please start relocation first.\033[0m");
            return;
        }

        goal.printInfo("  Target: ");

        if (!stopArmActionNodeSafe()) {
            RCLCPP_ERROR(get_logger(), "[Goal] Failed to stop arm action node before fsm_id=801, skip this goal");
            return;
        }

        if (!switchToFsmId(801)) {
            RCLCPP_ERROR(get_logger(), "[Goal] Failed to switch to walk locomotion mode (fsm_id=801), skip this goal");
            return;
        }

        const bool isMidGoal = isMidTransitionPoint(goal);
        g_current_goal_is_midpoint.store(isMidGoal);
        if (isMidGoal) {
            RCLCPP_INFO(get_logger(), "[Goal] This target is configured as a midpoint transition");
        }

        isNavigating_.store(true);
        slamClient_->navigateToGoal(goal);

        auto statusMsg = std_msgs::msg::String();
        statusMsg.data = "navigating";
        statusPub_->publish(statusMsg);

        auto navStateMsg = std_msgs::msg::UInt8();
        navStateMsg.data = 1;
        kuavoNavStatePub_->publish(navStateMsg);

    } catch (const std::exception& e) {
        RCLCPP_ERROR(get_logger(), "[JSON Error] %s", e.what());
    }
}

void HybridNavigationNode::cmdCallback(const std_msgs::msg::String::SharedPtr msg)
{
    RCLCPP_INFO(get_logger(), "[CMD] %s", msg->data.c_str());

    try {
        nlohmann::json cmd = nlohmann::json::parse(msg->data);
        std::string cmdType = cmd["cmd"];

        if (cmdType == "start_mapping") {
            slamClient_->startMappingPlFun();
        } else if (cmdType == "end_mapping") {
            std::string pcd = cmd.value("pcd", "/home/unitree/test1.pcd");
            slamClient_->endMappingPlFun(pcd);
        } else if (cmdType == "start_relocation") {
            std::string pcd = cmd.value("pcd", "/home/unitree/test.pcd");
            isNavigating_.store(false);
            startRelocationLoop(pcd, "Relocation");
        } else if (cmdType == "pause_nav") {
            slamClient_->pauseNavFun();
        } else if (cmdType == "resume_nav") {
            slamClient_->resumeNavFun();
        } else if (cmdType == "stop_nav") {
            slamClient_->taskThreadStop();
            isNavigating_.store(false);
        } else if (cmdType == "stop_node") {
            slamClient_->stopNodeFun();
        } else {
            RCLCPP_WARN(get_logger(), "[CMD] Unknown command: %s", cmdType.c_str());
        }

    } catch (const std::exception& e) {
        RCLCPP_ERROR(get_logger(), "[CMD Error] %s", e.what());
    }
}


void HybridNavigationNode::statusTimerCallback()
{
    if (isNavigating_.load() && slamClient_->isArrived()) {
        isNavigating_.store(false);
        RCLCPP_INFO(get_logger(), "\033[32m[Navigation] Completed, ready for new goal\033[0m");

        const bool isMidArrived = g_current_goal_is_midpoint.load();
        if (!isMidArrived) {
            if (!switchToFsmId(500)) {
                RCLCPP_WARN(get_logger(), "[Navigation] Arrived but failed to switch back to arm locomotion mode (fsm_id=500)");
            } else {
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
                if (!startArmActionNode()) {
                    RCLCPP_WARN(get_logger(), "[Navigation] fsm_id=500 switched, but failed to start arm action node");
                } else {
                    RCLCPP_INFO(get_logger(), "[Navigation] Arm action process started, waiting 1.5s before publishing arm_ready");
                    std::this_thread::sleep_for(std::chrono::milliseconds(1500));
                    auto readyMsg = std_msgs::msg::String();
                    readyMsg.data = "arm_ready";
                    statusPub_->publish(readyMsg);
                    RCLCPP_INFO(get_logger(), "[Navigation] Arm action process ready");
                }
                promptRestartAfterArrival();
            }
        } else {
            RCLCPP_INFO(get_logger(), "[Navigation] Midpoint arrived, keep current locomotion mode");
        }

        auto statusMsg = std_msgs::msg::String();
        statusMsg.data = isMidArrived ? "mid_arrived" : "arrived";
        statusPub_->publish(statusMsg);

        auto navStateMsg = std_msgs::msg::UInt8();
        navStateMsg.data = 2;
        kuavoNavStatePub_->publish(navStateMsg);

        g_current_goal_is_midpoint.store(false);
    }
}

void HybridNavigationNode::poseTimerCallback()
{
    if (!isRelocated_.load() || !slamClient_->hasValidPose()) {
        return;
    }

    PoseData pose = slamClient_->getCurrentPose();

    RCLCPP_INFO(get_logger(),
                "\033[36m[Pose]\033[0m x: %.4f  y: %.4f  z: %.4f  ox: %.4f  oy: %.4f  oz: %.4f  ow: %.4f",
                pose.x, pose.y, pose.z,
                pose.q_x, pose.q_y, pose.q_z, pose.q_w);

    nlohmann::json poseJson;
    poseJson["x"] = pose.x;
    poseJson["y"] = pose.y;
    poseJson["z"] = pose.z;
    poseJson["ox"] = pose.q_x;
    poseJson["oy"] = pose.q_y;
    poseJson["oz"] = pose.q_z;
    poseJson["ow"] = pose.q_w;

    auto poseMsg = std_msgs::msg::String();
    poseMsg.data = poseJson.dump();
    posePub_->publish(poseMsg);

    auto lioPoseMsg = geometry_msgs::msg::PoseStamped();
    lioPoseMsg.header.stamp = this->now();
    lioPoseMsg.header.frame_id = "map";
    lioPoseMsg.pose.position.x = pose.x;
    lioPoseMsg.pose.position.y = pose.y;
    lioPoseMsg.pose.position.z = pose.z;
    lioPoseMsg.pose.orientation.x = pose.q_x;
    lioPoseMsg.pose.orientation.y = pose.q_y;
    lioPoseMsg.pose.orientation.z = pose.q_z;
    lioPoseMsg.pose.orientation.w = pose.q_w;
    lioPosePub_->publish(lioPoseMsg);
}

int main(int argc, char* argv[])
{
    if (argc < 2) {
        std::cout << "Usage: " << argv[0] << " <network_interface> [pcd_path] [nav_speed]" << std::endl;
        std::cout << "  network_interface: Network interface (e.g., eth0, enxe04e7a83bb1a)" << std::endl;
        std::cout << "  pcd_path: Optional PCD map file for auto-relocation" << std::endl;
        std::cout << "  nav_speed: Optional SLAM navigation speed in m/s" << std::endl;
        std::cout << std::endl;
        std::cout << "Examples:" << std::endl;
        std::cout << "  " << argv[0] << " eth0" << std::endl;
        std::cout << "  " << argv[0] << " eth0 /home/unitree/test.pcd" << std::endl;
        std::cout << "  " << argv[0] << " eth0 /home/unitree/test.pcd 0.3" << std::endl;
        std::cout << "  " << argv[0] << " eth0 --nav_speed=0.3" << std::endl;
        return -1;
    }

    std::string networkInterface = argv[1];
    std::string pcdPath;
    float navSpeed = -1.0f;

    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        const std::string navSpeedPrefix = "--nav_speed=";
        const std::string speedPrefix = "--speed=";

        try {
            if (arg.rfind(navSpeedPrefix, 0) == 0) {
                navSpeed = std::stof(arg.substr(navSpeedPrefix.size()));
            } else if (arg.rfind(speedPrefix, 0) == 0) {
                navSpeed = std::stof(arg.substr(speedPrefix.size()));
            } else if (pcdPath.empty()) {
                pcdPath = arg;
            } else {
                navSpeed = std::stof(arg);
            }
        } catch (const std::exception& e) {
            std::cerr << "Invalid argument: " << arg << " (" << e.what() << ")" << std::endl;
            return -1;
        }
    }

    if (navSpeed > 0.0f) {
        std::cout << "Default navigation speed: " << navSpeed << " m/s" << std::endl;
    }

    rclcpp::init(argc, argv);
    auto node = std::make_shared<HybridNavigationNode>(networkInterface, pcdPath, navSpeed);
    rclcpp::spin(node);
    rclcpp::shutdown();
    return 0;
}
