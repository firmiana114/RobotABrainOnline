#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo 执行：sudo /home/unitree/RobotABrainOnline/voice/scripts/install_services.sh" >&2
  exit 1
fi

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
SYSTEMD_DIR=/etc/systemd/system
SUDOERS_FILE=/etc/sudoers.d/voice-control-console

for unit in voice-control-console.service voice-navigation.service voice-arm-control.service voice-arm-bridge.service voice-tour-guide.service; do
  install -m 0644 "$PROJECT_ROOT/deploy/systemd/$unit" "$SYSTEMD_DIR/$unit"
done
install -m 0440 "$PROJECT_ROOT/deploy/voice-control-console.sudoers" "$SUDOERS_FILE"
/usr/sbin/visudo -cf "$SUDOERS_FILE"

systemctl daemon-reload
systemctl disable voice-navigation.service voice-arm-control.service voice-tour-guide.service >/dev/null 2>&1 || true
systemctl enable --now voice-control-console.service voice-arm-bridge.service

echo "[INFO] Voice 控制台已安装：http://$(hostname -I | awk '{print $1}'):8081"
echo "导航、手臂和导览服务保持手动启动。"
