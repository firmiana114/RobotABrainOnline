from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize(
    "script_name",
    ("start_console.sh", "start_navigation.sh", "start_tour.sh"),
)
def test_start_script_uses_relocatable_venv_python(script_name):
    script = (SCRIPT_DIR / script_name).read_text(encoding="utf-8")

    assert 'PYTHON_BIN="${VENV_ROOT}/bin/python"' in script
    assert 'source "${VENV_ROOT}/bin/activate"' not in script
    assert 'python=${PYTHON_BIN}' in script
