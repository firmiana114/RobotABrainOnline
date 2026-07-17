#ifndef GESTURE_LIBRARY_H
#define GESTURE_LIBRARY_H

#include <eigen3/Eigen/Dense>
#include <string>
#include <unordered_map>
#include <vector>

namespace inspire
{

using HandQ = Eigen::Matrix<float, 6, 1>; // [pinky, ring, middle, index, thumb_bend, thumb_rotation]

struct GestureLibrary
{
  static const std::unordered_map<std::string, HandQ> &Map()
  {
    static const std::unordered_map<std::string, HandQ> kGestures = {
      {"open",      (HandQ() << 1.00f, 1.00f, 1.00f, 1.00f, 1.00f, 0.50f).finished()},
      {"close",     (HandQ() << 0.00f, 0.00f, 0.00f, 0.00f, 0.00f, 0.50f).finished()},
      {"half",      (HandQ() << 0.50f, 0.50f, 0.50f, 0.50f, 0.50f, 0.50f).finished()},

      // 常见手势（可按实际硬件效果再微调）
      {"ok",        (HandQ() << 1.00f, 1.00f, 1.00f, 0.10f, 0.10f, 0.80f).finished()},
      {"victory",   (HandQ() << 0.10f, 0.10f, 1.00f, 1.00f, 0.20f, 0.80f).finished()}, // 比耶
      {"point",     (HandQ() << 0.10f, 0.10f, 0.10f, 1.00f, 0.20f, 0.60f).finished()},
      {"thumbs_up", (HandQ() << 0.10f, 0.10f, 0.10f, 0.10f, 1.00f, 1.00f).finished()},

      // 数字手势（按常见单手表达，可按实际效果微调）
      {"num1",      (HandQ() << 0.10f, 0.10f, 0.10f, 1.00f, 0.10f, 0.55f).finished()}, // 食指伸直
      {"num2",      (HandQ() << 0.10f, 0.10f, 1.00f, 1.00f, 0.10f, 0.55f).finished()}, // 食指+中指
      {"num3",      (HandQ() << 0.10f, 1.00f, 1.00f, 1.00f, 0.10f, 0.55f).finished()}, // 食指+中指+无名指
      {"num4",      (HandQ() << 1.00f, 1.00f, 1.00f, 1.00f, 0.10f, 0.55f).finished()}, // 除拇指外四指伸直
    };
    return kGestures;
  }

  static bool Exists(const std::string &name)
  {
    return Map().find(name) != Map().end();
  }

  static const HandQ &Get(const std::string &name)
  {
    return Map().at(name);
  }

  static std::vector<std::string> Names()
  {
    std::vector<std::string> names;
    names.reserve(Map().size());
    for (const auto &kv : Map())
    {
      names.push_back(kv.first);
    }
    return names;
  }
};

} // namespace inspire

#endif // GESTURE_LIBRARY_H
