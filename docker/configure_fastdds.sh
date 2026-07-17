configure_fastdds() {
  local requested_interface="${1:-}"
  local ros_interface
  local ros_interface_ip
  ros_interface="${requested_interface:-$(ip -4 -o addr | awk '$4 ~ /^192\.168\.123\./ {print $2; exit}')}"
  ros_interface="${ros_interface:-eth0}"
  ros_interface_ip="$(ip -4 -o addr show "$ros_interface" | awk '{split($4, parts, "/"); print parts[1]; exit}')"
  if [ -z "$ros_interface_ip" ]; then
    echo "Unable to resolve IPv4 address for ROS interface: ${ros_interface}" >&2
    return 1
  fi
  mkdir -p runtime
  sed "s/__ROS_INTERFACE_IP__/${ros_interface_ip}/g" docker/fastdds-eth0.xml > runtime/fastdds.xml
  printf '[%s] [INFO] configured Fast DDS interface=%s address=%s\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$ros_interface" "$ros_interface_ip"
}
