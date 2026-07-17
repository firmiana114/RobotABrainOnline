#include <unitree/idl/go2/MotorCmds_.hpp>
#include <unitree/idl/go2/MotorStates_.hpp>
#include <unitree/robot/channel/channel_publisher.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

#include <eigen3/Eigen/Dense>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>

#include "gesture_library.h"

class GestureBridge
{
public:
  GestureBridge()
  {
    handcmd_ = std::make_shared<unitree::robot::ChannelPublisher<unitree_go::msg::dds_::MotorCmds_>>("rt/inspire/cmd");
    handcmd_->InitChannel();

    handstate_ = std::make_shared<unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::MotorStates_>>("rt/inspire/state");
    handstate_->InitChannel([this](const void *message) {
      state_ = *reinterpret_cast<const unitree_go::msg::dds_::MotorStates_ *>(message);
      has_state_ = true;
    });

    cmd_.cmds().resize(12);
    state_.states().resize(12);
  }

  void SubmitCommand(const std::string &text) { OnGestureCommand(text); }

  void SpinOnce()
  {
    ConsumePendingCommand();
    CheckHoldTimeout();
  }

private:
  struct GestureCommand
  {
    std::string gesture;
    std::string hand;
    int duration_ms;
    int hold_ms;
    uint64_t token;
  };

  void OnGestureCommand(const std::string &text)
  {
    std::istringstream iss(text);
    GestureCommand cmd;
    cmd.hand = "both";
    cmd.duration_ms = 800;
    cmd.hold_ms = 0;

    iss >> cmd.gesture;
    if (cmd.gesture.empty())
    {
      std::cerr << "[gesture_bridge] empty command" << std::endl;
      return;
    }

    if (!(iss >> cmd.hand))
    {
      cmd.hand = "both";
    }

    if (!(iss >> cmd.duration_ms))
    {
      cmd.duration_ms = 800;
    }

    if (!(iss >> cmd.hold_ms))
    {
      cmd.hold_ms = 0;
    }

    if (cmd.hand != "left" && cmd.hand != "right" && cmd.hand != "both")
    {
      std::cerr << "[gesture_bridge] invalid hand: " << cmd.hand << std::endl;
      return;
    }

    if (!inspire::GestureLibrary::Exists(cmd.gesture))
    {
      std::cerr << "[gesture_bridge] unknown gesture: " << cmd.gesture << std::endl;
      PrintSupported();
      return;
    }

    cmd.duration_ms = std::max(20, cmd.duration_ms);
    cmd.hold_ms = std::max(0, cmd.hold_ms);
    cmd.token = ++command_token_;

    {
      std::lock_guard<std::mutex> lock(mtx_);
      pending_command_ = cmd;
    }
  }

  void ConsumePendingCommand()
  {
    std::optional<GestureCommand> cmd;
    {
      std::lock_guard<std::mutex> lock(mtx_);
      if (pending_command_.has_value())
      {
        cmd = pending_command_;
        pending_command_.reset();
      }
    }

    if (!cmd.has_value())
    {
      return;
    }

    if (!PlayGesture(cmd->gesture, cmd->hand, cmd->duration_ms, cmd->token))
    {
      return;
    }

    active_hand_ = cmd->hand;
    if (cmd->hold_ms > 0)
    {
      hold_deadline_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(cmd->hold_ms);
      waiting_hold_timeout_ = true;
      std::cout << "[gesture_bridge] played: " << cmd->gesture
                << " hand=" << cmd->hand
                << " duration_ms=" << cmd->duration_ms
                << " hold_ms=" << cmd->hold_ms
                << " (will restore default if no new command)" << std::endl;
    }
    else
    {
      waiting_hold_timeout_ = false;
      std::cout << "[gesture_bridge] played: " << cmd->gesture
                << " hand=" << cmd->hand
                << " duration_ms=" << cmd->duration_ms
                << " hold_ms=0" << std::endl;
    }
  }

  void CheckHoldTimeout()
  {
    if (!waiting_hold_timeout_)
    {
      return;
    }

    auto now = std::chrono::steady_clock::now();
    if (now < hold_deadline_)
    {
      return;
    }

    waiting_hold_timeout_ = false;
    const uint64_t token = ++command_token_;
    PlayGesture(default_gesture_, active_hand_, default_duration_ms_, token);

    std::cout << "[gesture_bridge] hold timeout, restore default: "
              << default_gesture_ << " hand=" << active_hand_ << std::endl;
  }

  bool PlayGesture(const std::string &gesture, const std::string &hand, int duration_ms, uint64_t token)
  {
    Eigen::Matrix<float, 6, 1> target = inspire::GestureLibrary::Get(gesture);

    Eigen::Matrix<float, 6, 1> right_start = Eigen::Matrix<float, 6, 1>::Ones();
    Eigen::Matrix<float, 6, 1> left_start = Eigen::Matrix<float, 6, 1>::Ones();
    if (has_state_)
    {
      for (int i = 0; i < 6; ++i)
      {
        right_start(i) = static_cast<float>(state_.states()[i].q());
        left_start(i) = static_cast<float>(state_.states()[i + 6].q());
      }
    }

    constexpr int kStepMs = 20;
    int steps = std::max(1, duration_ms / kStepMs);

    for (int s = 1; s <= steps; ++s)
    {
      if (token != command_token_.load())
      {
        return false;
      }

      float alpha = static_cast<float>(s) / static_cast<float>(steps);
      Eigen::Matrix<float, 6, 1> right_q = right_start;
      Eigen::Matrix<float, 6, 1> left_q = left_start;

      if (hand == "right" || hand == "both")
      {
        right_q = right_start + alpha * (target - right_start);
      }
      if (hand == "left" || hand == "both")
      {
        left_q = left_start + alpha * (target - left_start);
      }

      Publish(right_q, left_q);
      std::this_thread::sleep_for(std::chrono::milliseconds(kStepMs));
    }

    return true;
  }

  void Publish(const Eigen::Matrix<float, 6, 1> &right, const Eigen::Matrix<float, 6, 1> &left)
  {
    for (int i = 0; i < 6; ++i)
    {
      cmd_.cmds()[i].q() = std::clamp(right(i), 0.0f, 1.0f);
      cmd_.cmds()[i + 6].q() = std::clamp(left(i), 0.0f, 1.0f);
    }
    handcmd_->Write(cmd_);
  }

  void PrintSupported() const
  {
    std::cout << "[gesture_bridge] supported gestures:";
    for (const auto &name : inspire::GestureLibrary::Names())
    {
      std::cout << " " << name;
    }
    std::cout << std::endl;
  }

private:
  std::mutex mtx_;
  std::atomic<uint64_t> command_token_{0};
  std::optional<GestureCommand> pending_command_;

  bool has_state_ = false;
  bool waiting_hold_timeout_ = false;
  std::string active_hand_ = "both";
  std::chrono::steady_clock::time_point hold_deadline_{};

  const std::string default_gesture_ = "half";
  const int default_duration_ms_ = 400;

  unitree_go::msg::dds_::MotorCmds_ cmd_;
  unitree_go::msg::dds_::MotorStates_ state_;

  unitree::robot::ChannelPublisherPtr<unitree_go::msg::dds_::MotorCmds_> handcmd_;
  unitree::robot::ChannelSubscriberPtr<unitree_go::msg::dds_::MotorStates_> handstate_;
};

class GestureBridgeRosNode : public rclcpp::Node
{
public:
  GestureBridgeRosNode() : Node("gesture_topic_bridge_node")
  {
    gesture_sub_ = this->create_subscription<std_msgs::msg::String>(
        "/gesture_cmd",
        10,
        [this](const std_msgs::msg::String::SharedPtr msg) {
          if (msg->data.empty())
          {
            RCLCPP_WARN(get_logger(), "Received empty gesture command");
            return;
          }

          RCLCPP_INFO(get_logger(), "Received gesture command: %s", msg->data.c_str());
          bridge_.SubmitCommand(msg->data);
        });

    spin_timer_ = this->create_wall_timer(std::chrono::milliseconds(20), [this]() { bridge_.SpinOnce(); });

    RCLCPP_INFO(get_logger(), "===========================================");
    RCLCPP_INFO(get_logger(), "Gesture Topic Bridge (ROS2)");
    RCLCPP_INFO(get_logger(), "===========================================");
    RCLCPP_INFO(get_logger(), "ROS2 Topic:");
    RCLCPP_INFO(get_logger(), "  /gesture_cmd std_msgs/msg/String");
    RCLCPP_INFO(get_logger(), "Command format: <gesture> [left|right|both] [duration_ms] [hold_ms]");
    RCLCPP_INFO(get_logger(), "Example: ros2 topic pub --once /gesture_cmd std_msgs/msg/String \"{data: 'num3 right 800 2000'}\"");
    RCLCPP_INFO(get_logger(), "===========================================");
    PrintSupported();
  }

private:
  void PrintSupported() const
  {
    std::ostringstream oss;
    oss << "Supported gestures:";
    for (const auto &name : inspire::GestureLibrary::Names())
    {
      oss << " " << name;
    }
    RCLCPP_INFO(get_logger(), "%s", oss.str().c_str());
  }

private:
  GestureBridge bridge_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr gesture_sub_;
  rclcpp::TimerBase::SharedPtr spin_timer_;
};

int main(int argc, char **argv)
{
  std::cout << " --- Unitree Robotics ---\n";
  std::cout << "  Inspire Gesture Topic Bridge (ROS2 Topic) \n\n";

  std::string networkInterface = argc > 1 ? argv[1] : "";
  rclcpp::init(argc, argv);
  unitree::robot::ChannelFactory::Instance()->Init(0, networkInterface);

  auto node = std::make_shared<GestureBridgeRosNode>();
  rclcpp::spin(node);
  rclcpp::shutdown();
  return 0;
}
