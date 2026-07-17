#!/usr/bin/env bash
set -Eeo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
VENV_ROOT="${VOICE_VENV_ROOT:-${PROJECT_ROOT}/.venv}"
TORCH_URL="${TORCH_URL:-https://developer.download.nvidia.com/compute/redist/jp/v511/pytorch/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl}"
SPACY_MODEL_WHEEL="${SPACY_MODEL_WHEEL:-}"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

log_info "creating NX host venv root=${VENV_ROOT}"
python3 -m venv --system-site-packages "$VENV_ROOT"
"${VENV_ROOT}/bin/pip" install --upgrade "pip<25.1" setuptools wheel
"${VENV_ROOT}/bin/pip" install --ignore-installed "importlib-metadata==8.5.0" "packaging==24.2"
"${VENV_ROOT}/bin/pip" install \
  -r "${PROJECT_ROOT}/requirements-nx-console.txt" \
  -r "${PROJECT_ROOT}/requirements-nx-tour.txt"

log_info "installing NVIDIA JetPack PyTorch"
"${VENV_ROOT}/bin/pip" install --no-cache-dir "$TORCH_URL"

wheel_dir="$(mktemp -d)"
log_info "downloading Python 3.10-tagged pure Python Kokoro package for NX Python 3.8"
"${VENV_ROOT}/bin/pip" download --no-deps --only-binary=:all: \
  --python-version 310 --dest "$wheel_dir" kokoro==0.9.4 misaki==0.9.4
"${VENV_ROOT}/bin/pip" install --no-deps --ignore-requires-python "$wheel_dir"/*.whl
"${VENV_ROOT}/bin/pip" install \
  "huggingface-hub<1" loguru "transformers<4.47" addict regex \
  espeakng-loader num2words phonemizer-fork "spacy<3.8" \
  "spacy-curated-transformers<0.3" cn2an jieba ordered-set pypinyin pypinyin-dict

kokoro_model="${VENV_ROOT}/lib/python3.8/site-packages/kokoro/model.py"
if ! grep -q '^from __future__ import annotations' "$kokoro_model"; then
  sed -i '1i from __future__ import annotations' "$kokoro_model"
fi

if [[ -n "$SPACY_MODEL_WHEEL" ]]; then
  log_info "installing supplied spaCy English model wheel"
  "${VENV_ROOT}/bin/pip" install "$SPACY_MODEL_WHEEL"
elif ! "${VENV_ROOT}/bin/python" -c 'import spacy; raise SystemExit(0 if spacy.util.is_package("en_core_web_sm") else 1)'; then
  log_info "spaCy English model is absent; attempting network installation"
  "${VENV_ROOT}/bin/python" -m spacy download en_core_web_sm
fi

log_info "validating host venv imports"
source "${VENV_ROOT}/bin/activate"
python -c 'import dashscope, kokoro, misaki, openai, torch; print("NX venv ready; cuda=", torch.cuda.is_available())'
