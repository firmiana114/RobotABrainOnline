#pragma once

#include <array>
#include <optional>
#include <string>

namespace g1_actions {

constexpr int kJointCount = 17;
using Pose = std::array<float, kJointCount>;
using JointMask = std::array<bool, kJointCount>;

enum class ActionType {
  kRightHandshakeReady,
  kLeftSelfIntroduction,
  kLeftHandshakeReady,
  kRightSelfIntroduction,
  kRightHandshakeWrist,
  kBothHandshakeReady,
  kLeftArmHorizontal,
  kRightArmHorizontal,
  kLeftWristOutside,
  kRightWristOutside,
  kBothArmsHorizontal,
  kBothArmsLowerHorizontal,
  kBothHandSelfIntroduction,
  kLeftHandPointing,
  kRightHandPointing,
  kLeftHandLowStroking,
  kRightHandLowStroking,
  kBothHandWelcome,
  kRightHandWave,
  kLeftHandWave,
  kBothHandWave,
  kBothHandsRaise,
  kBothHandsStable,
  kLeftArmHorizontalInward,
  kRightArmHorizontalInward,
  kRightHandLowWave,
  kReleaseInitPose,
};

struct ActionFrame {
  std::string name;
  Pose target_pose;
  JointMask active_mask;
  float to_init_time;
  float move_time;
};

const ActionFrame& GetAction(ActionType type);
std::optional<ActionType> ParseActionInput(const std::string& input);
std::string ActionMenuText();
std::string ActionTopicHelpText();

}  // namespace g1_actions
