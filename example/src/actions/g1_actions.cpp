#include "actions/g1_actions.hpp"

#include <algorithm>
#include <cctype>
#include <stdexcept>

namespace g1_actions {
namespace {

constexpr JointMask kLeftArmOnlyMask = {
    true, true, true, true, true, true, true,
    false, false, false, false, false, false, false,
    false, false, false};

constexpr JointMask kRightArmOnlyMask = {
    false, false, false, false, false, false, false,
    true, true, true, true, true, true, true,
    false, false, false};

constexpr JointMask kBothArmsMask = {
    true, true, true, true, true, true, true,
    true, true, true, true, true, true, true,
    false, false, false};

const ActionFrame kRightHandshakeReady{
    "right_hand_handshake_ready",                     // 肩关节，肘关节，腕关节
    {0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,   // 负->前，负->内，负->内，负->上，负->外， ，正->外
     -0.35f, -0.00f, 0.00f, -0.10f, 0.0f, 0.0f, 0.0f, // 负->前，负->外，负->外，负->上，负-内，  ，负->外
     0.0f,  0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kLeftSelfIntroduction{
    "left_hand_self_introduction",
    {-0.82f, 0.52f, -1.00f, -0.5f, -1.00f, 0.00f, 0.00f,
     0.0f,  0.0f,  0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,  0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kLeftHandshakeReady{
    "left_hand_handshake_ready",
    {-0.35f, 0.00f, -0.00f, -0.10f, 0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightSelfIntroduction{
    "right_hand_self_introduction",
    {0.0f,   0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     -0.82f, -0.52f, 1.00f, -0.5f, 1.00f, 0.00f, 0.00f,
     0.0f,   0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightHandshakeWrist{
    "right_hand_handshake_wrist",
    {0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     -0.35f, -0.00f, 0.00f, -0.80f, 0.00f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kBothHandshakeReady{
    "both_hand_handshake_ready",
    {-0.35f, 0.50f, -0.00f, -0.10f, -1.2f, 0.0f, 0.0f,
     -0.35f, -0.50f, 0.00f, -0.10f, 1.2f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    2.0f};

const ActionFrame kLeftArmHorizontal{
    "left_arm_horizontal",
    {-0.35f, 0.50f, -0.40f, -0.10f, -2.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightArmHorizontal{
    "right_arm_horizontal",
    {0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     -0.35f, -0.50f, 0.40f, -0.10f, 2.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kLeftWristOutside{
    "left_wrist_outside",
    {-0.35f, 0.250f, 0.50f, 0.10f, -1.3f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightWristOutside{
    "right_wrist_outside",
    {0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     -0.35f, -0.250f, -0.50f, 0.10f, 1.3f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kBothArmsHorizontal{
    "both_arms_horizontal",
    {-0.35f, 0.50f, -0.00f, -0.10f, -1.8f, 0.0f, 0.0f,
     -0.35f, -0.50f, 0.00f, -0.10f, 1.8f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    2.0f};

const ActionFrame kBothArmsLowerHorizontal{
    "both_arms_lower_horizontal",
    {-0.35f, 0.50f, -0.00f, 1.00f, -1.8f, 0.0f, 0.0f,
     -0.35f, -0.50f, 0.00f, 1.00f, 1.8f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    2.0f};

const ActionFrame kBothHandSelfIntroduction{
    "both_hand_self_introduction",
    {-0.12f, 1.22f, -0.80f, -0.6f, -1.50f, 0.00f, 0.00f,
     -0.12f, -1.22f, 0.80f, -0.6f, 1.50f, 0.00f, 0.00f,
     0.0f,   0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    2.0f};

const ActionFrame kLeftHandPointing{
    "left_hand_pointing",
    {-0.85f, 1.40f, -0.5f, 0.7f, -0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightHandPointing{
    "right_hand_pointing",
    {0.0f,   0.0f,   0.0f, 0.0f, 0.0f, 0.0f, 0.0f,
     -0.85f, -1.40f, 0.5f, 0.7f, -0.0f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kLeftHandLowStroking{
    "left_hand_low_stroking",
    {-1.0f, 0.150f, -1.3f, 1.4f, -0.0f, 0.0f, 0.3f,
     0.0f,   0.0f,   0.0f, 0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightHandLowStroking{
    "right_hand_low_stroking",
    {0.0f,  0.0f,    0.0f, 0.0f,  0.0f, 0.0f, 0.0f,
     -1.0f, -0.150f, 1.3f, 1.4f, -0.0f, 0.0f, -0.3f,
     0.0f,  0.0f,    0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kBothHandWelcome{
    "both_hand_welcome",
    {-0.85f, 1.10f, -0.5f, 0.7f, -1.3f, 0.0f, 0.0f,
     -0.85f, -1.10f, 0.5f, 0.7f, 1.3f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    2.0f};

const ActionFrame kRightHandWave{
    "right_hand_wave",
    {0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     -0.75f, -0.30f, 0.00f, -0.80f, -1.25f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    1.2f};

const ActionFrame kLeftHandWave{
    "left_hand_wave",
    {-0.75f, 0.30f, -0.00f, -0.80f, 1.25f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    1.2f};

const ActionFrame kBothHandWave{
    "both_hand_wave",
    {-0.65f, 0.60f, 0.60f, -0.80f, 1.05f, 0.0f, 0.0f,
     -0.65f, -0.60f, -0.60f, -0.80f, -1.05f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    1.2f};

const ActionFrame kBothHandsRaise{
    "both_hands_raise",
    {-1.55f, 1.55f, -0.20f, 0.95f, -1.50f, 0.0f, 0.0f,
     -1.55f, -1.55f, 0.20f, 0.95f, 1.50f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    1.6f};

const ActionFrame kBothHandsStable{
    "both_hands_stable",
    {-0.82f, 0.52f, -1.00f, -0.5f, -1.00f, 0.00f, 0.00f,
     -0.82f, -0.52f, 1.00f, -0.5f, 0.00f, 0.00f, 0.00f,
     0.0f,   0.0f,   0.0f},
    kBothArmsMask,
    1.0f,
    2.0f};

const ActionFrame kLeftArmHorizontalInward{
    "left_arm_horizontal_inward",
    {-0.35f, 0.80f, -1.10f, -0.50f, -2.0f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kLeftArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightArmHorizontalInward{
    "right_arm_horizontal_inward",
    {0.0f,   0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
     -0.35f, -0.80f, 1.10f, -0.50f, 2.0f, 0.0f, 0.0f,
     0.0f,   0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    2.0f};

const ActionFrame kRightHandLowWave{
    "right_hand_low_wave",
    {0.0f,  0.0f,   0.0f,  0.0f,  0.0f, 0.0f, 0.0f,
    -0.75f, -0.80f, 0.00f, -0.80f, -1.25f, 0.0f, 0.0f,
     0.0f,  0.0f,   0.0f},
    kRightArmOnlyMask,
    1.0f,
    1.2f};
}  // namespace

const ActionFrame& GetAction(ActionType type) {
  switch (type) {
    case ActionType::kRightHandshakeReady:
      return kRightHandshakeReady;
    case ActionType::kLeftSelfIntroduction:
      return kLeftSelfIntroduction;
    case ActionType::kLeftHandshakeReady:
      return kLeftHandshakeReady;
    case ActionType::kRightSelfIntroduction:
      return kRightSelfIntroduction;
    case ActionType::kRightHandshakeWrist:
      return kRightHandshakeWrist;
    case ActionType::kBothHandshakeReady:
      return kBothHandshakeReady;
    case ActionType::kLeftArmHorizontal:
      return kLeftArmHorizontal;
    case ActionType::kRightArmHorizontal:
      return kRightArmHorizontal;
    case ActionType::kLeftWristOutside:
      return kLeftWristOutside;
    case ActionType::kRightWristOutside:
      return kRightWristOutside;
    case ActionType::kBothArmsHorizontal:
      return kBothArmsHorizontal;
    case ActionType::kBothArmsLowerHorizontal:
      return kBothArmsLowerHorizontal;
    case ActionType::kBothHandSelfIntroduction:
      return kBothHandSelfIntroduction;
    case ActionType::kLeftHandPointing:
      return kLeftHandPointing;
    case ActionType::kRightHandPointing:
      return kRightHandPointing;
    case ActionType::kLeftHandLowStroking:
      return kLeftHandLowStroking;
    case ActionType::kRightHandLowStroking:
      return kRightHandLowStroking;
    case ActionType::kBothHandWelcome:
      return kBothHandWelcome;
    case ActionType::kRightHandWave:
      return kRightHandWave;
    case ActionType::kLeftHandWave:
      return kLeftHandWave;
    case ActionType::kBothHandWave:
      return kBothHandWave;
    case ActionType::kBothHandsRaise:
      return kBothHandsRaise;
    case ActionType::kBothHandsStable:
      return kBothHandsStable;
    case ActionType::kLeftArmHorizontalInward:
      return kLeftArmHorizontalInward;
    case ActionType::kRightArmHorizontalInward:
      return kRightArmHorizontalInward;
    case ActionType::kRightHandLowWave:
      return kRightHandLowWave;
  }
  throw std::invalid_argument("Unknown action type");
}

std::optional<ActionType> ParseActionInput(const std::string& input) {
  std::string normalized = input;
  normalized.erase(std::remove_if(normalized.begin(), normalized.end(),
                                  [](unsigned char c) { return std::isspace(c); }),
                   normalized.end());
  std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });

  if (normalized == "1" || normalized == "right_hand_handshake_ready" ||
      normalized == "握手") {
    return ActionType::kRightHandshakeReady;
  }
  if (normalized == "2" || normalized == "left_hand_self_introduction") {
    return ActionType::kLeftSelfIntroduction;
  }
  if (normalized == "3" || normalized == "left_hand_handshake_ready") {
    return ActionType::kLeftHandshakeReady;
  }
  if (normalized == "4" || normalized == "right_hand_self_introduction") {
    return ActionType::kRightSelfIntroduction;
  }
  if (normalized == "5" || normalized == "right_hand_handshake_wrist") {
    return ActionType::kRightHandshakeWrist;
  }
  if (normalized == "6" || normalized == "both_hand_handshake_ready" ||
      normalized == "碰拳+弹指") {
    return ActionType::kBothHandshakeReady;
  }
  if (normalized == "7" || normalized == "left_arm_horizontal") {
    return ActionType::kLeftArmHorizontal;
  }
  if (normalized == "8" || normalized == "right_arm_horizontal") {
    return ActionType::kRightArmHorizontal;
  }
  if (normalized == "9" || normalized == "left_wrist_outside") {
    return ActionType::kLeftWristOutside;
  }
  if (normalized == "10" || normalized == "right_wrist_outside") {
    return ActionType::kRightWristOutside;
  }
  if (normalized == "11" || normalized == "both_arms_horizontal") {
    return ActionType::kBothArmsHorizontal;
  }
  if (normalized == "12" || normalized == "both_arms_lower_horizontal") {
    return ActionType::kBothArmsLowerHorizontal;
  }
  if (normalized == "13" || normalized == "both_hand_self_introduction") {
    return ActionType::kBothHandSelfIntroduction;
  }
  if (normalized == "14" || normalized == "left_hand_pointing") {
    return ActionType::kLeftHandPointing;
  }
  if (normalized == "15" || normalized == "right_hand_pointing" ||
      normalized == "点赞" || normalized == "ok手势" ||
      normalized == "指尖轻点") {
    return ActionType::kRightHandPointing;
  }
  if (normalized == "16" || normalized == "left_hand_low_stroking") {
    return ActionType::kLeftHandLowStroking;
  }
  if (normalized == "17" || normalized == "right_hand_low_stroking") {
    return ActionType::kRightHandLowStroking;
  }
  if (normalized == "18" || normalized == "both_hand_welcome" ||
      normalized == "欢迎") {
    return ActionType::kBothHandWelcome;
  }
  if (normalized == "19" || normalized == "right_hand_wave" ||
      normalized == "打招呼" || normalized == "再见") {
    return ActionType::kRightHandWave;
  }
  if (normalized == "20" || normalized == "left_hand_wave") {
    return ActionType::kLeftHandWave;
  }
  if (normalized == "21" || normalized == "both_hand_wave" ||
      normalized == "击掌") {
    return ActionType::kBothHandWave;
  }
  if (normalized == "22" || normalized == "both_hands_raise" ||
      normalized == "比耶") {
    return ActionType::kBothHandsRaise;
  }
  if (normalized == "23" || normalized == "both_hands_stable" ||
      normalized == "双手平摊开掌心向上" || normalized == "否定拒绝") {
    return ActionType::kBothHandsStable;
  }
  if (normalized == "24" || normalized == "left_arm_horizontal_inward") {
    return ActionType::kLeftArmHorizontalInward;
  }
  if (normalized == "25" || normalized == "right_arm_horizontal_inward") {
    return ActionType::kRightArmHorizontalInward;
  }
  if (normalized == "26" || normalized == "right_hand_low_wave" ||
      normalized == "敬礼") {
    return ActionType::kRightHandLowWave;
  }
  if (normalized == "0" || normalized == "release" ||
      normalized == "release_init_pose" || normalized == "release_init_pos1") {
    return ActionType::kReleaseInitPose;
  }
  return std::nullopt;
}

std::string ActionMenuText() {
  return "\nSelect action: [1] right_hand_handshake_ready, "
         "[2] left_hand_self_introduction, "
         "[3] left_hand_handshake_ready, "
         "[4] right_hand_self_introduction, "
         "[5] right_hand_handshake_wrist, "
         "[6] both_hand_handshake_ready, "
         "[7] left_arm_horizontal, "
         "[8] right_arm_horizontal, "
         "[9] left_wrist_outside, "
         "[10] right_wrist_outside, "
         "[11] both_arms_horizontal, "
         "[12] both_arms_lower_horizontal, "
         "[13] both_hand_self_introduction, "
         "[14] left_hand_pointing, "
         "[15] right_hand_pointing, "
         "[16] left_hand_low_stroking, "
         "[17] right_hand_low_stroking, "
         "[18] both_hand_welcome, "
         "[19] right_hand_wave, "
         "[20] left_hand_wave, "
         "[21] both_hand_wave, "
         "[22] both_hands_raise, "
         "[23] both_hands_stable, "
         "[24] left_arm_horizontal_inward, "
         "[25] right_arm_horizontal_inward, "
         "[26] right_hand_low_wave, [q] quit: ";
}

std::string ActionTopicHelpText() {
  return "Supported payload: 1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26 or action name: "
         "right_hand_handshake_ready, left_hand_self_introduction, "
         "left_hand_handshake_ready, right_hand_self_introduction, "
         "right_hand_handshake_wrist, both_hand_handshake_ready, "
         "left_arm_horizontal, right_arm_horizontal, "
         "left_wrist_outside, right_wrist_outside, "
         "both_arms_horizontal, both_arms_lower_horizontal, "
         "both_hand_self_introduction, left_hand_pointing, right_hand_pointing, "
         "left_hand_low_stroking, right_hand_low_stroking, both_hand_welcome, "
         "right_hand_wave, left_hand_wave, both_hand_wave, both_hands_raise, both_hands_stable, "
         "left_arm_horizontal_inward, right_arm_horizontal_inward, right_hand_low_wave; "
         "release payload: 0|release|release_init_pose|release_init_pos1";
}

}  // namespace g1_actions
