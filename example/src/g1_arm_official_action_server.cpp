#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <deque>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>

#include <custom_action_interfaces/action/navi_arm.hpp>

#include <unitree/idl/ros2/String_.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>
#include <unitree/robot/g1/arm/g1_arm_action_client.hpp>
#include <unitree/robot/g1/arm/g1_arm_action_error.hpp>
#include <unitree/robot/g1/loco/g1_loco_client.hpp>

namespace {

using NaviArm = custom_action_interfaces::action::NaviArm;
using GoalHandleNaviArm = rclcpp_action::ServerGoalHandle<NaviArm>;
using unitree::robot::g1::G1ArmActionClient;
using unitree::robot::g1::LocoClient;

static const std::string kTopicArmActionState = "rt/arm/action/state";
static const std::string kDefaultActionServerName = "navi_arm";

std::string NormalizeKey(const std::string &input) {
  std::string normalized;
  normalized.reserve(input.size());
  for (unsigned char ch : input) {
    if (std::isalnum(ch)) {
      normalized.push_back(static_cast<char>(std::tolower(ch)));
    }
  }
  return normalized;
}

enum class CommandKind {
  kArmPreset,
  kLocoWaveHand,
  kLocoWaveHandWithTurn,
  kLocoShakeHandStart,
  kLocoShakeHandEnd,
  kLocoShakeHandToggle,
};

struct OfficialCommand {
  std::string canonical_name;
  CommandKind kind;
  int value;
  std::string description;
};

const std::unordered_map<std::string, OfficialCommand> &CommandMap() {
  static const std::unordered_map<std::string, OfficialCommand> map = [] {
    std::unordered_map<std::string, OfficialCommand> result;

    const auto add_aliases =
        [&](const OfficialCommand &command,
            std::initializer_list<const char *> aliases) {
          for (const char *alias : aliases) {
            result.emplace(NormalizeKey(alias), command);
          }
        };

    const auto add_arm_preset =
        [&](int action_id, const std::string &canonical_name,
            const std::string &description,
            std::initializer_list<const char *> aliases) {
          OfficialCommand command{canonical_name, CommandKind::kArmPreset,
                                  action_id, description};
          add_aliases(command, aliases);
          result.emplace(std::to_string(action_id), command);
        };

    add_arm_preset(99, "release_arm", "release current arm holding state",
                   {"release", "release_arm", "release arm"});
    add_arm_preset(11, "two_hand_kiss", "official preset: two-hand kiss",
                   {"two_hand_kiss", "two-hand kiss", "two hand kiss"});
    add_arm_preset(12, "left_kiss", "official preset: left/right kiss",
                   {"left_kiss", "left kiss", "right_kiss", "right kiss", "kiss"});
    add_arm_preset(15, "hands_up", "official preset: hands up",
                   {"hands_up", "hands up"});
    add_arm_preset(17, "clap", "official preset: clap", {"clap"});
    add_arm_preset(18, "high_five", "official preset: high five",
                   {"high_five", "high five"});
    add_arm_preset(19, "hug", "official preset: hug", {"hug"});
    add_arm_preset(20, "heart", "official preset: heart", {"heart"});
    add_arm_preset(21, "right_heart", "official preset: right heart",
                   {"right_heart", "right heart"});
    add_arm_preset(22, "reject", "official preset: reject", {"reject"});
    add_arm_preset(23, "right_hand_up", "official preset: right hand up",
                   {"right_hand_up", "right hand up"});
    add_arm_preset(24, "x_ray", "official preset: x-ray",
                   {"x_ray", "x-ray", "x ray"});
    add_arm_preset(25, "face_wave", "official preset: face wave",
                   {"face_wave", "face wave"});
    add_arm_preset(26, "high_wave", "official preset: high wave",
                   {"high_wave", "high wave"});
    add_arm_preset(27, "shake_hand", "official preset: shake hand",
                   {"shake_hand", "shake hand"});

    add_aliases(OfficialCommand{"wave_hand", CommandKind::kLocoWaveHand, 0,
                                "loco task: wave hand"},
                {"wave_hand_loco", "wave hand loco", "wave_hand_task", "wave hand task"});
    add_aliases(OfficialCommand{"wave_hand_with_turn",
                                CommandKind::kLocoWaveHandWithTurn, 1,
                                "loco task: wave hand with turn"},
                {"wave_hand_with_turn", "wave hand with turn"});
    add_aliases(OfficialCommand{"shake_hand_start",
                                CommandKind::kLocoShakeHandStart, 0,
                                "loco task: shake hand start"},
                {"shake_hand_start", "shake hand start"});
    add_aliases(OfficialCommand{"shake_hand_end",
                                CommandKind::kLocoShakeHandEnd, 1,
                                "loco task: shake hand end"},
                {"shake_hand_end", "shake hand end"});
    add_aliases(OfficialCommand{"shake_hand_toggle",
                                CommandKind::kLocoShakeHandToggle, -1,
                                "loco task: shake hand toggle"},
                {"shake_hand_toggle", "shake hand toggle"});

    return result;
  }();

  return map;
}

std::optional<OfficialCommand> ParseOfficialCommand(const std::string &input) {
  const auto normalized = NormalizeKey(input);
  if (normalized.empty()) {
    return std::nullopt;
  }

  const auto &command_map = CommandMap();
  const auto it = command_map.find(normalized);
  if (it == command_map.end()) {
    return std::nullopt;
  }
  return it->second;
}

std::string ActionHelpText() {
  std::ostringstream oss;
  oss << "Supported official action_name values:\n"
      << "  release_arm | 99\n"
      << "  two_hand_kiss | 11\n"
      << "  left_kiss | right_kiss | 12\n"
      << "  hands_up | 15\n"
      << "  clap | 17\n"
      << "  high_five | 18\n"
      << "  hug | 19\n"
      << "  heart | 20\n"
      << "  right_heart | 21\n"
      << "  reject | 22\n"
      << "  right_hand_up | 23\n"
      << "  x_ray | 24\n"
      << "  face_wave | 25\n"
      << "  high_wave | 26\n"
      << "  shake_hand | 27\n"
      << "  wave_hand_loco\n"
      << "  wave_hand_with_turn\n"
      << "  shake_hand_start\n"
      << "  shake_hand_end\n"
      << "  shake_hand_toggle";
  return oss.str();
}

std::string DescribeArmActionError(const int32_t ret) {
  switch (ret) {
  case unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_ARMSDK:
    return unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_ARMSDK_DESC;
  case unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_HOLDING:
    return std::string(unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_HOLDING_DESC) +
           " Send release_arm (99) or the same last action id first.";
  case unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_INVALID_ACTION_ID:
    return unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_INVALID_ACTION_ID_DESC;
  case unitree::robot::g1::UT_ROBOT_ARM_ACTION_ERR_INVALID_FSM_ID:
    return "Invalid FSM for arm action. Expected fsm_id in {500, 501, 801}; "
           "when fsm_id=801, expected fsm_mode in {0, 3}.";
  default:
    return "unknown error code: " + std::to_string(ret);
  }
}

std::string SnapshotWithLatestState(const std::string &prefix,
                                    const std::string &latest_state) {
  if (latest_state.empty()) {
    return prefix;
  }
  return prefix + " latest_state=" + latest_state;
}

struct PendingActionRequest {
  OfficialCommand command;
  std::string action_name;
  std::shared_ptr<GoalHandleNaviArm> goal_handle;
  std::chrono::steady_clock::time_point accepted_at;
};

} // namespace

int main(int argc, char **argv) {
  rclcpp::init(argc, argv);

  if (argc < 2) {
    std::cout << "Usage: " << argv[0] << " networkInterface" << std::endl;
    return -1;
  }

  unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);

  auto ros_node = rclcpp::Node::make_shared("g1_arm_official_action_server");
  const auto action_server_name = ros_node->declare_parameter<std::string>(
      "action_server_name", kDefaultActionServerName);

  G1ArmActionClient arm_action_client;
  arm_action_client.Init();
  arm_action_client.SetTimeout(10.f);

  LocoClient loco_client;
  loco_client.Init();
  loco_client.SetTimeout(10.f);

  std::mutex latest_arm_state_mtx;
  std::string latest_arm_state;
  unitree::robot::ChannelSubscriberPtr<std_msgs::msg::dds_::String_>
      arm_action_state_subscriber;
  arm_action_state_subscriber.reset(
      new unitree::robot::ChannelSubscriber<std_msgs::msg::dds_::String_>(
          kTopicArmActionState));
  arm_action_state_subscriber->InitChannel(
      [&](const void *msg) {
        const auto *state =
            static_cast<const std_msgs::msg::dds_::String_ *>(msg);
        std::lock_guard<std::mutex> lock(latest_arm_state_mtx);
        latest_arm_state = state->data();
      },
      1);

  auto get_latest_arm_state = [&]() {
    std::lock_guard<std::mutex> lock(latest_arm_state_mtx);
    return latest_arm_state;
  };

  std::mutex action_cmd_mtx;
  std::deque<PendingActionRequest> pending_actions;

  auto navi_arm_action_server = rclcpp_action::create_server<NaviArm>(
      ros_node, action_server_name,
      [&](const rclcpp_action::GoalUUID &,
          std::shared_ptr<const NaviArm::Goal> goal) {
        const auto command = ParseOfficialCommand(goal->action_name);
        if (!command.has_value()) {
          RCLCPP_WARN(ros_node->get_logger(),
                      "Unknown official action_name on %s: %s",
                      action_server_name.c_str(), goal->action_name.c_str());
          return rclcpp_action::GoalResponse::REJECT;
        }
        return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
      },
      [&](const std::shared_ptr<GoalHandleNaviArm> /*goal_handle*/) {
        return rclcpp_action::CancelResponse::ACCEPT;
      },
      [&](const std::shared_ptr<GoalHandleNaviArm> goal_handle) {
        const auto action_name = goal_handle->get_goal()->action_name;
        const auto command = ParseOfficialCommand(action_name);

        if (!command.has_value()) {
          auto result = std::make_shared<NaviArm::Result>();
          result->success = false;
          result->message = "Invalid official action_name";
          goal_handle->abort(result);
          return;
        }

        const auto accepted_at = std::chrono::steady_clock::now();
        {
          std::lock_guard<std::mutex> lock(action_cmd_mtx);
          pending_actions.push_back(
              PendingActionRequest{command.value(), action_name, goal_handle,
                                   accepted_at});
        }

        RCLCPP_INFO(ros_node->get_logger(),
                    "Accepted official action request: %s -> %s",
                    action_name.c_str(),
                    command->canonical_name.c_str());
      });

  std::cout << "Action server: " << action_server_name
            << " (custom_action_interfaces/action/NaviArm)" << std::endl;
  std::cout << ActionHelpText() << std::endl;
  std::cout << "Waiting official navi_arm action goals... (Ctrl+C to quit)"
            << std::endl;

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

    if (!request_to_run.has_value()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
      continue;
    }

    auto &req = request_to_run.value();
    auto goal_handle = req.goal_handle;
    const auto execution_start = std::chrono::steady_clock::now();
    const double receive_to_start_ms =
        std::chrono::duration<double, std::milli>(execution_start -
                                                  req.accepted_at)
            .count();

    RCLCPP_INFO(ros_node->get_logger(),
                "Official action timing [%s]: receive_to_start=%.2f ms",
                req.action_name.c_str(), receive_to_start_ms);

    if (goal_handle->is_canceling()) {
      auto result = std::make_shared<NaviArm::Result>();
      result->success = false;
      result->message = "Canceled before execution";
      goal_handle->canceled(result);
      continue;
    }

    int current_fsm_id = -1;
    int current_fsm_mode = -1;
    loco_client.GetFsmId(current_fsm_id);
    loco_client.GetFsmMode(current_fsm_mode);
    RCLCPP_INFO(ros_node->get_logger(),
                "Executing official action [%s] at fsm_id=%d fsm_mode=%d",
                req.command.canonical_name.c_str(), current_fsm_id,
                current_fsm_mode);

    int32_t ret = 0;
    std::string success_prefix =
        "Official action accepted by Unitree service: " +
        req.command.canonical_name;

    switch (req.command.kind) {
    case CommandKind::kArmPreset:
      ret = arm_action_client.ExecuteAction(req.command.value);
      break;
    case CommandKind::kLocoWaveHand:
      ret = loco_client.WaveHand(false);
      break;
    case CommandKind::kLocoWaveHandWithTurn:
      ret = loco_client.WaveHand(true);
      break;
    case CommandKind::kLocoShakeHandStart:
      ret = loco_client.ShakeHand(0);
      break;
    case CommandKind::kLocoShakeHandEnd:
      ret = loco_client.ShakeHand(1);
      break;
    case CommandKind::kLocoShakeHandToggle:
      ret = loco_client.ShakeHand(-1);
      break;
    }

    if (ret == 0) {
      auto result = std::make_shared<NaviArm::Result>();
      result->success = true;
      result->message =
          SnapshotWithLatestState(success_prefix, get_latest_arm_state());
      goal_handle->succeed(result);

      const auto finish_time = std::chrono::steady_clock::now();
      const double receive_to_success_ms =
          std::chrono::duration<double, std::milli>(finish_time -
                                                    req.accepted_at)
              .count();
      const double execute_duration_ms =
          std::chrono::duration<double, std::milli>(finish_time -
                                                    execution_start)
              .count();
      RCLCPP_INFO(
          ros_node->get_logger(),
          "Official action timing [%s]: receive_to_success=%.2f ms, "
          "execute_duration=%.2f ms",
          req.action_name.c_str(), receive_to_success_ms, execute_duration_ms);
      continue;
    }

    auto result = std::make_shared<NaviArm::Result>();
    result->success = false;
    if (req.command.kind == CommandKind::kArmPreset) {
      result->message = SnapshotWithLatestState(
          "Official arm action failed: " + DescribeArmActionError(ret),
          get_latest_arm_state());
    } else {
      result->message = SnapshotWithLatestState(
          "Official loco hand task failed, ret=" + std::to_string(ret),
          get_latest_arm_state());
    }
    goal_handle->abort(result);

    const auto finish_time = std::chrono::steady_clock::now();
    const double receive_to_abort_ms =
        std::chrono::duration<double, std::milli>(finish_time - req.accepted_at)
            .count();
    const double execute_duration_ms =
        std::chrono::duration<double, std::milli>(finish_time - execution_start)
            .count();
    RCLCPP_ERROR(ros_node->get_logger(),
                 "Official action failed [%s], ret=%d "
                 "(receive_to_abort=%.2f ms, execute_duration=%.2f ms)",
                 req.action_name.c_str(), ret, receive_to_abort_ms,
                 execute_duration_ms);
  }

  rclcpp::shutdown();
  return 0;
}
