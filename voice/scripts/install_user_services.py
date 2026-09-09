#!/usr/bin/env python3
"""Install relocatable user services; never starts robot nodes."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
voice = root / "voice"
runtime = voice / "runtime"
runtime.mkdir(mode=0o700, exist_ok=True)
units = Path.home() / ".config/systemd/user"
units.mkdir(parents=True, exist_ok=True)
wrapper = runtime / "systemctl-user"
wrapper.write_text('#!/bin/sh\nexec /usr/bin/systemctl --user "$@"\n')
wrapper.chmod(0o755)
# Keep host-specific dependencies outside the repository.
lib = Path(os.environ.get("VOICE_RUNTIME_LIB", str(root) + "-runtime/lib"))
for source in (voice / "deploy/systemd").glob("*.service"):
    content = source.read_text().replace("/home/unitree/RobotABrainOnline", str(root))
    content = content.replace("User=unitree\n", "").replace("Group=unitree\n", "")
    content = content.replace("WantedBy=multi-user.target", "WantedBy=default.target")
    content = content.replace("Environment=NAV_IFACE=eth0", "Environment=NAV_IFACE=eno1")
    content = content.replace("[Service]\n", "[Service]\n" +
        f"Environment=LD_LIBRARY_PATH={lib}:/usr/local/cuda/lib64\n" +
        f"Environment=VOICE_SYSTEMCTL_PATH={wrapper}\n" +
        "Environment=VOICE_CONSOLE_SUDO=\n" +
        f"EnvironmentFile=-{runtime}/deployment.env\n")
    (units / source.name).write_text(content)
config = runtime / "deployment.env"
if not config.exists():
    config.write_text("# Offline bridge only; set to the robot NIC when connected.\nROS_INTERFACE=lo\n")
    config.chmod(0o600)
secrets = runtime / "voice.env"
if not secrets.exists():
    secrets.write_text("# Supply credentials locally; never commit this file.\nDASHSCOPE_API_KEY=\nQWEN_MODEL=qwen-plus\nKOKORO_REPO_ID=hexgrad/Kokoro-82M\nKOKORO_VOICE=zm_yunxi\nALSA_INPUT_DEVICE=plughw:CARD=Rx,DEV=0\nSPEAKER_NAME='REDMI Speaker 2-6002'\n")
    secrets.chmod(0o600)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
print("Installed user services. No service was started. Robot NIC default: eno1.")
