#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <deque>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <optional>
#include <thread>

#include <unitree/idl/hg/LowCmd_.hpp>
#include <unitree/idl/hg/LowState_.hpp>
#include <unitree/idl/ros2/String_.hpp>
#include <unitree/robot/channel/channel_publisher.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include <custom_action_interfaces/action/navi_arm.hpp>

#include "actions/g1_actions.hpp"

static const std::string kTopicArmSDK = "rt/arm_sdk";
static const std::string kTopicState = "rt/lowstate";
static const std::string kTopicActionCmd = "rt/g1/action_cmd";

enum JointIndex {
    // Left leg
    kLeftHipPitch,
    kLeftHipRoll,
    kLeftHipYaw,
    kLeftKnee,
    kLeftAnkle,
    kLeftAnkleRoll,

    // Right leg
    kRightHipPitch,
    kRightHipRoll,
    kRightHipYaw,
    kRightKnee,
    kRightAnkle,
    kRightAnkleRoll,

    kWaistYaw,
    kWaistRoll,
    kWaistPitch,

    // Left arm
    kLeftShoulderPitch,
    kLeftShoulderRoll,
    kLeftShoulderYaw,
    kLeftElbow,
    kLeftWristRoll,
    kLeftWristPitch,
    kLeftWristYaw,
    // Right arm
    kRightShoulderPitch,
    kRightShoulderRoll,
    kRightShoulderYaw,
    kRightElbow,
    kRightWristRoll,
    kRightWristPitch,
    kRightWristYaw,

    kNotUsedJoint,
    kNotUsedJoint1,
    kNotUsedJoint2,
    kNotUsedJoint3,
    kNotUsedJoint4,
    kNotUsedJoint5
};

int main(int argc, char **argv) {
  rclcpp::init(argc, argv);

  if (argc < 2) {
    std::cout << "Usage: " << argv[0]
              << " networkInterface [--relative|--absolute]" << std::endl;
    exit(-1);
  }

  unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);

  unitree::robot::ChannelPublisherPtr<unitree_hg::msg::dds_::LowCmd_>
      arm_sdk_publisher;
  unitree_hg::msg::dds_::LowCmd_ msg;

  arm_sdk_publisher.reset(
      new unitree::robot::ChannelPublisher<unitree_hg::msg::dds_::LowCmd_>(
          kTopicArmSDK));
  arm_sdk_publisher->InitChannel();

  unitree::robot::ChannelSubscriberPtr<unitree_hg::msg::dds_::LowState_>
      low_state_subscriber;

  // create subscriber
  unitree_hg::msg::dds_::LowState_ state_msg;
  std::mutex state_msg_mtx;
  std::atomic<uint32_t> latest_state_tick{0};
  low_state_subscriber.reset(
      new unitree::robot::ChannelSubscriber<unitree_hg::msg::dds_::LowState_>(
          kTopicState));
  low_state_subscriber->InitChannel([&](const void *msg) {
        auto s = ( const unitree_hg::msg::dds_::LowState_* )msg;
        {
          std::lock_guard<std::mutex> lock(state_msg_mtx);
          state_msg = *s;
          latest_state_tick.store(state_msg.tick(), std::memory_order_relaxed);
        }
  }, 1);

  auto get_state_snapshot = [&]() {
    std::lock_guard<std::mutex> lock(state_msg_mtx);
    return state_msg;
  };

  using NaviArm = custom_action_interfaces::action::NaviArm;
  using GoalHandleNaviArm = rclcpp_action::ServerGoalHandle<NaviArm>;

  struct PendingActionRequest {
    g1_actions::ActionType action_type;
    std::string action_name;
    std::shared_ptr<GoalHandleNaviArm> goal_handle;
    std::chrono::steady_clock::time_point accepted_at;
  };

  std::mutex action_cmd_mtx;
  std::deque<PendingActionRequest> pending_actions;

  auto ros_node = rclcpp::Node::make_shared("g1_arm_action_server");
  auto navi_arm_action_server = rclcpp_action::create_server<NaviArm>(
      ros_node,
      "navi_arm",
      [&](const rclcpp_action::GoalUUID &, std::shared_ptr<const NaviArm::Goal> goal) {
        const auto action_type = g1_actions::ParseActionInput(goal->action_name);
        if (!action_type.has_value()) {
          RCLCPP_WARN(ros_node->get_logger(),
                      "Unknown action_name on navi_arm: %s",
                      goal->action_name.c_str());
          return rclcpp_action::GoalResponse::REJECT;
        }
        return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
      },
      [&](const std::shared_ptr<GoalHandleNaviArm> /*goal_handle*/) {
        return rclcpp_action::CancelResponse::ACCEPT;
      },
      [&](const std::shared_ptr<GoalHandleNaviArm> goal_handle) {
        const auto action_name = goal_handle->get_goal()->action_name;
        const auto action_type = g1_actions::ParseActionInput(action_name);

        if (!action_type.has_value()) {
          auto result = std::make_shared<NaviArm::Result>();
          result->success = false;
          result->message = "Invalid action_name";
          goal_handle->abort(result);
          return;
        }

        const auto accepted_at = std::chrono::steady_clock::now();
        {
          std::lock_guard<std::mutex> lock(action_cmd_mtx);
          pending_actions.push_back(PendingActionRequest{action_type.value(), action_name, goal_handle, accepted_at});
        }

        RCLCPP_INFO(ros_node->get_logger(), "Accepted action: %s", action_name.c_str());
      });

  std::cout << "Action server: navi_arm (custom_action_interfaces/action/NaviArm)" << std::endl;
  std::cout << "Action field action_name supports:" << std::endl;
  std::cout << g1_actions::ActionTopicHelpText() << std::endl;
  std::cout << "Release action_name: 0 | release | release_init_pose | release_init_pos1" << std::endl;

  // 去掉初始化动作后，仍需等待 lowstate 首帧有效，避免使用默认零值作为目标姿态。
  std::cout << "Waiting for valid lowstate ..." << std::endl;
  auto wait_start = std::chrono::steady_clock::now();
  while (latest_state_tick.load(std::memory_order_relaxed) == 0) {
    auto waited = std::chrono::steady_clock::now() - wait_start;
    if (waited > std::chrono::seconds(3)) {
      std::cerr << "Failed to receive valid lowstate within 3s, abort." << std::endl;
      rclcpp::shutdown();
      return -1;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  std::array<JointIndex, 17> arm_joints = {
      JointIndex::kLeftShoulderPitch,  JointIndex::kLeftShoulderRoll,
      JointIndex::kLeftShoulderYaw,    JointIndex::kLeftElbow,
      JointIndex::kLeftWristRoll,       JointIndex::kLeftWristPitch,
      JointIndex::kLeftWristYaw,
      JointIndex::kRightShoulderPitch, JointIndex::kRightShoulderRoll,
      JointIndex::kRightShoulderYaw,   JointIndex::kRightElbow,
      JointIndex::kRightWristRoll,      JointIndex::kRightWristPitch,
      JointIndex::kRightWristYaw,
      JointIndex::kWaistYaw,
      JointIndex::kWaistRoll,
      JointIndex::kWaistPitch};
  std::array<JointIndex, 7> left_arm_joints = {
      JointIndex::kLeftShoulderPitch, JointIndex::kLeftShoulderRoll,
      JointIndex::kLeftShoulderYaw,   JointIndex::kLeftElbow,
      JointIndex::kLeftWristRoll,     JointIndex::kLeftWristPitch,
      JointIndex::kLeftWristYaw};
  std::array<JointIndex, 7> right_arm_joints = {
      JointIndex::kRightShoulderPitch, JointIndex::kRightShoulderRoll,
      JointIndex::kRightShoulderYaw,   JointIndex::kRightElbow,
      JointIndex::kRightWristRoll,     JointIndex::kRightWristPitch,
      JointIndex::kRightWristYaw};

  float weight = 0.f;

  float kp_active = 60.f;
  float kd_active = 1.5f;
  float kp_hold = 60.f;   // 继续回撤：与主动关节一致
  float kd_hold = 1.5f;
  float dq = 0.f;
  float tau_ff = 0.f;

  float control_dt = 0.02f;
  float max_joint_velocity = 1.0f;

  float max_joint_delta = max_joint_velocity * control_dt;
  auto sleep_time =
      std::chrono::milliseconds(static_cast<int>(control_dt / 0.001f));

  std::array<float, 17> init_pos0{0, 0, 0, 0, 0, 0, 0,
                                  0, 0, 0, 0, 0, 0, 0,
                                  0, 0, 0};
  std::array<float, 17> init_pos1{};
  const auto boot_state = get_state_snapshot();
  for (int i = 0; i < init_pos1.size(); ++i) {
    init_pos1.at(i) = boot_state.motor_state().at(arm_joints.at(i)).q();
  }
  std::cout << "Recorded startup pose as init_pos1." << std::endl;
  std::cout << std::fixed << std::setprecision(4) << "init_pos1: [";
  for (int i = 0; i < init_pos1.size(); ++i) {
    std::cout << init_pos1.at(i);
    if (i + 1 != init_pos1.size()) {
      std::cout << ", ";
    }
  }
  std::cout << "]" << std::endl;

  const std::array<bool, 17> all_joint_mask = {
      true, true, true, true, true, true, true,
      true, true, true, true, true, true, true,
      true, true, true};

  // 直接接管控制，不执行初始化动作。
  std::cout << "Acquiring arm control ..." << std::endl;
  float acquire_time = 1.0f;
  int acquire_steps = std::max(1, static_cast<int>(acquire_time / control_dt));
  for (int i = 1; i <= acquire_steps; ++i) {
    float alpha = static_cast<float>(i) / static_cast<float>(acquire_steps);
    weight = alpha;
    msg.motor_cmd().at(JointIndex::kNotUsedJoint).q(weight);
    arm_sdk_publisher->Write(msg);
    std::this_thread::sleep_for(sleep_time);
  }

  // start control
  std::cout << "Start custom actions ctrl!" << std::endl;

  const auto start_state = get_state_snapshot();
  std::array<float, 17> current_jpos_des{};
  for (int j = 0; j < init_pos1.size(); ++j) {
    current_jpos_des.at(j) = start_state.motor_state().at(arm_joints.at(j)).q();
  }
  auto print_arm7_realtime = [&]() {
    const auto s = get_state_snapshot();
    std::cout << std::fixed << std::setprecision(4) << "L7:[";
    for (int i = 0; i < left_arm_joints.size(); ++i) {
      std::cout << s.motor_state().at(left_arm_joints.at(i)).q();
      if (i + 1 != left_arm_joints.size()) std::cout << ", ";
    }
    std::cout << "] R7:[";
    for (int i = 0; i < right_arm_joints.size(); ++i) {
      std::cout << s.motor_state().at(right_arm_joints.at(i)).q();
      if (i + 1 != right_arm_joints.size()) std::cout << ", ";
    }
    std::cout << "]" << std::endl;
  };
  int print_counter = 0;
  const int print_stride = 5;  // 50Hz control -> 10Hz print

  auto publish_with_target = [&](const std::array<float, 17>& target,
                                 const std::array<bool, 17>& active_mask,
                                 const std::array<float, 17>& hold_pose) {
    for (int j = 0; j < init_pos1.size(); ++j) {
      const bool is_active = active_mask.at(j);
      const float desired_target = is_active ? target.at(j) : hold_pose.at(j);
      current_jpos_des.at(j) +=
          std::clamp(desired_target - current_jpos_des.at(j),
                     -max_joint_delta, max_joint_delta);

      msg.motor_cmd().at(arm_joints.at(j)).q(current_jpos_des.at(j));
      msg.motor_cmd().at(arm_joints.at(j)).dq(dq);
      msg.motor_cmd().at(arm_joints.at(j)).kp(is_active ? kp_active : kp_hold);
      msg.motor_cmd().at(arm_joints.at(j)).kd(is_active ? kd_active : kd_hold);
      msg.motor_cmd().at(arm_joints.at(j)).tau(tau_ff);
    }

    arm_sdk_publisher->Write(msg);
    ++print_counter;
    if (print_counter % print_stride == 0) {
      print_arm7_realtime();
    }
    std::this_thread::sleep_for(sleep_time);
  };

  const float hold_reference_epsilon = 0.0015f;  // 未激活关节微量参考偏置
  auto calc_steps = [&](const std::array<float, 17>& from_pose,
                        const std::array<float, 17>& to_pose,
                        const std::array<bool, 17>& mask,
                        float min_time) {
    const int min_steps = std::max(1, static_cast<int>(min_time / control_dt));
    float max_delta = 0.0f;
    for (int j = 0; j < init_pos1.size(); ++j) {
      if (!mask.at(j)) {
        continue;
      }
      max_delta = std::max(max_delta, std::fabs(to_pose.at(j) - from_pose.at(j)));
    }
    const int required_steps =
        static_cast<int>(std::ceil(max_delta / std::max(max_joint_delta, 1e-6f)));
    return std::max(min_steps, required_steps);
  };

  auto run_action = [&](const g1_actions::ActionFrame& action) {
    std::cout << "Run action: " << action.name << std::endl;

    auto effective_target = action.target_pose;
    auto effective_mask = action.active_mask;

    bool has_left_active = false;
    bool has_right_active = false;
    for (int j = 0; j < 7; ++j) {
      has_left_active = has_left_active || effective_mask.at(j);
    }
    for (int j = 7; j < 14; ++j) {
      has_right_active = has_right_active || effective_mask.at(j);
    }

    const bool left_only = has_left_active && !has_right_active;
    const bool right_only = has_right_active && !has_left_active;

    if (left_only) {
      for (int j = 7; j < 14; ++j) {
        effective_target.at(j) = init_pos1.at(j);
        effective_mask.at(j) = true;
      }
      std::cout << "Left-arm action detected, releasing right arm to init_pos1." << std::endl;
    } else if (right_only) {
      for (int j = 0; j < 7; ++j) {
        effective_target.at(j) = init_pos1.at(j);
        effective_mask.at(j) = true;
      }
      std::cout << "Right-arm action detected, releasing left arm to init_pos1." << std::endl;
    }

    std::array<float, 17> hold_pose{};
    const auto action_state = get_state_snapshot();
    for (int j = 0; j < init_pos1.size(); ++j) {
      hold_pose.at(j) = action_state.motor_state().at(arm_joints.at(j)).q();
      if (!effective_mask.at(j)) {
        const float sign = hold_pose.at(j) >= 0.0f ? 1.0f : -1.0f;
        hold_pose.at(j) += sign * hold_reference_epsilon;
      }
      current_jpos_des.at(j) = hold_pose.at(j);
    }

    const int move_steps =
        calc_steps(current_jpos_des, effective_target, effective_mask, action.move_time);
    for (int i = 0; i < move_steps; ++i) {
      publish_with_target(effective_target, effective_mask, hold_pose);
    }

    std::cout << "Action reached, holding pose until next action/release command." << std::endl;
    print_arm7_realtime();
  };

  auto run_release_to_init = [&]() {
    std::cout << "Run release: move back to init_pos1" << std::endl;
    std::array<float, 17> hold_pose{};
    const auto release_state = get_state_snapshot();
    for (int j = 0; j < init_pos1.size(); ++j) {
      hold_pose.at(j) = release_state.motor_state().at(arm_joints.at(j)).q();
      current_jpos_des.at(j) = hold_pose.at(j);
    }

    const float release_time = 1.0f;
    const int release_steps =
        calc_steps(current_jpos_des, init_pos1, all_joint_mask, release_time);
    for (int i = 0; i < release_steps; ++i) {
      publish_with_target(init_pos1, all_joint_mask, hold_pose);
    }
    print_arm7_realtime();
  };

  auto run_right_hand_wave = [&]() {
    std::cout << "Run sequence action: right_hand_wave" << std::endl;
    auto wave_base = g1_actions::GetAction(g1_actions::ActionType::kRightHandWave);

    auto wave_up = wave_base;
    wave_up.name = "right_hand_wave_up";
    wave_up.target_pose.at(9) = 0.55f;    // RightShoulderYaw
    wave_up.move_time = 0.25f;

    auto wave_down = wave_base;
    wave_down.name = "right_hand_wave_down";
    wave_down.target_pose.at(9) = -0.25f; // RightShoulderYaw
    wave_down.move_time = 0.25f;

    run_action(wave_base);  // prepare pose
    for (int i = 0; i < 2; ++i) {
      run_action(wave_up);
      run_action(wave_down);
    }
    run_action(wave_base);  // keep at wave base pose
  };

  auto run_left_hand_wave = [&]() {
    std::cout << "Run sequence action: left_hand_wave" << std::endl;
    auto wave_base = g1_actions::GetAction(g1_actions::ActionType::kLeftHandWave);

    auto wave_up = wave_base;
    wave_up.name = "left_hand_wave_up";
    wave_up.target_pose.at(2) = -0.25f;   // LeftShoulderYaw
    wave_up.move_time = 0.25f;

    auto wave_down = wave_base;
    wave_down.name = "left_hand_wave_down";
    wave_down.target_pose.at(2) = 0.55f;  // LeftShoulderYaw
    wave_down.move_time = 0.25f;

    run_action(wave_base);
    for (int i = 0; i < 2; ++i) {
      run_action(wave_up);
      run_action(wave_down);
    }
    run_action(wave_base);
  };

  auto run_both_hand_wave = [&]() {
    std::cout << "Run sequence action: both_hand_wave" << std::endl;
    auto wave_base = g1_actions::GetAction(g1_actions::ActionType::kBothHandWave);

    auto wave_up = wave_base;
    wave_up.name = "both_hand_wave_up";
    wave_up.target_pose.at(2) = -0.2f;   // LeftShoulderYaw
    wave_up.target_pose.at(9) = 0.2f;    // RightShoulderYaw
    wave_up.move_time = 0.25f;

    auto wave_down = wave_base;
    wave_down.name = "both_hand_wave_down";
    wave_down.target_pose.at(2) = 0.2f;
    wave_down.target_pose.at(9) = -0.2f;
    wave_down.move_time = 0.25f;

    run_action(wave_base);
    for (int i = 0; i < 2; ++i) {
      run_action(wave_up);
      run_action(wave_down);
    }
    run_action(wave_base);
  };

  auto run_right_hand_low_wave = [&]() {
    std::cout << "Run sequence action: right_hand_low_wave" << std::endl;
    auto wave_base = g1_actions::GetAction(g1_actions::ActionType::kRightHandLowWave);

    auto wave_up = wave_base;
    wave_up.name = "right_hand_low_wave_up";
    wave_up.target_pose.at(9) = 0.25f;   // RightShoulderYaw
    wave_up.move_time = 0.25f;

    auto wave_down = wave_base;
    wave_down.name = "right_hand_low_wave_down";
    wave_down.target_pose.at(9) = -0.65f; // RightShoulderYaw
    wave_down.move_time = 0.25f;

    run_action(wave_base);
    for (int i = 0; i < 2; ++i) {
      run_action(wave_up);
      run_action(wave_down);
    }
    run_action(wave_base);
  };

  // 启动自动执行：先到 init_pos0(全零)，再回到 init_pos1(启动记录位姿)。
  std::cout << "Auto init sequence: init_pos0 -> init_pos1" << std::endl;
  std::array<float, 17> hold_pose_all{};
  const auto auto_init_state = get_state_snapshot();
  for (int j = 0; j < init_pos1.size(); ++j) {
    hold_pose_all.at(j) = auto_init_state.motor_state().at(arm_joints.at(j)).q();
    current_jpos_des.at(j) = hold_pose_all.at(j);
  }

  const int to_init0_steps =
      calc_steps(current_jpos_des, init_pos0, all_joint_mask, 1.5f);
  for (int i = 0; i < to_init0_steps; ++i) {
    publish_with_target(init_pos0, all_joint_mask, hold_pose_all);
  }

  const int to_init1_steps =
      calc_steps(current_jpos_des, init_pos1, all_joint_mask, 1.5f);
  for (int i = 0; i < to_init1_steps; ++i) {
    publish_with_target(init_pos1, all_joint_mask, hold_pose_all);
  }
  print_arm7_realtime();

  std::cout << "Waiting navi_arm action goals... (Ctrl+C to quit)" << std::endl;
  while (rclcpp::ok()) {
    rclcpp::spin_some(ros_node);

    std::optional<PendingActionRequest> request_to_run;
    {
      std::lock_guard<std::mutex> lock(action_cmd_mtx);
      if (!pending_actions.empty()) {
        request_to_run = pending_actions.front();
        pending_actions.pop_front();
      }
    }

    if (request_to_run.has_value()) {
      auto &req = request_to_run.value();
      auto goal_handle = req.goal_handle;
      const auto execution_start = std::chrono::steady_clock::now();
      const double receive_to_start_ms = std::chrono::duration<double, std::milli>(execution_start - req.accepted_at).count();

      RCLCPP_INFO(
          ros_node->get_logger(),
          "Action timing [%s]: receive_to_start=%.2f ms",
          req.action_name.c_str(),
          receive_to_start_ms);

      if (goal_handle->is_canceling()) {
        auto result = std::make_shared<NaviArm::Result>();
        result->success = false;
        result->message = "Canceled before execution";
        goal_handle->canceled(result);
        continue;
      }

      try {
        if (req.action_type == g1_actions::ActionType::kReleaseInitPose) {
          run_release_to_init();
        } else if (req.action_type == g1_actions::ActionType::kRightHandWave) {
          run_right_hand_wave();
        } else if (req.action_type == g1_actions::ActionType::kLeftHandWave) {
          run_left_hand_wave();
        } else if (req.action_type == g1_actions::ActionType::kBothHandWave) {
          run_both_hand_wave();
        } else if (req.action_type == g1_actions::ActionType::kRightHandLowWave) {
          run_right_hand_low_wave();
        } else {
          run_action(g1_actions::GetAction(req.action_type));
        }

        auto result = std::make_shared<NaviArm::Result>();
        result->success = true;
        result->message = "Action executed successfully";
        goal_handle->succeed(result);
        const auto finish_time = std::chrono::steady_clock::now();
        const double receive_to_success_ms = std::chrono::duration<double, std::milli>(finish_time - req.accepted_at).count();
        const double execute_duration_ms = std::chrono::duration<double, std::milli>(finish_time - execution_start).count();
        RCLCPP_INFO(
            ros_node->get_logger(),
            "Action timing [%s]: receive_to_success=%.2f ms, execute_duration=%.2f ms",
            req.action_name.c_str(),
            receive_to_success_ms,
            execute_duration_ms);
      } catch (const std::exception &e) {
        const auto finish_time = std::chrono::steady_clock::now();
        const double receive_to_abort_ms = std::chrono::duration<double, std::milli>(finish_time - req.accepted_at).count();
        const double execute_duration_ms = std::chrono::duration<double, std::milli>(finish_time - execution_start).count();
        RCLCPP_ERROR(
            ros_node->get_logger(),
            "Action execution exception [%s]: %s (receive_to_abort=%.2f ms, execute_duration=%.2f ms)",
            req.action_name.c_str(),
            e.what(),
            receive_to_abort_ms,
            execute_duration_ms);
        auto result = std::make_shared<NaviArm::Result>();
        result->success = false;
        result->message = std::string("Action execution failed: ") + e.what();
        goal_handle->abort(result);
      }
      continue;
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }

  // stop control
  std::cout << "Stoping arm ctrl ...";
  float stop_time = 2.0f;
  int stop_time_steps = std::max(1, static_cast<int>(stop_time / control_dt));
  float stop_start_weight = weight;

  for (int i = 1; i <= stop_time_steps; ++i) {
    float alpha = static_cast<float>(i) / static_cast<float>(stop_time_steps);
    weight = stop_start_weight * (1.0f - alpha);

    msg.motor_cmd().at(JointIndex::kNotUsedJoint).q(weight);
    arm_sdk_publisher->Write(msg);
    std::this_thread::sleep_for(sleep_time);
  }

  std::cout << "Done!" << std::endl;

  rclcpp::shutdown();
  return 0;
}
