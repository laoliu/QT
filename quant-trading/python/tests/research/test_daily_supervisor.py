"""A native crash must remain visible outside the Python exception handler."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell supervisor")
@pytest.mark.parametrize("exit_code", [0, 7, -1073741819])
def test_supervisor_records_real_exit_even_with_stderr(tmp_path, exit_code):
    stub = tmp_path / "fake-python.cmd"
    stub.write_text(f"@echo off\necho diagnostic 1>&2\nexit /b {exit_code}\n", encoding="ascii")
    script = Path(__file__).resolve().parents[4] / "scripts" / "run-research-paper.ps1"
    logs = tmp_path / "logs"
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                    "-Deployment", str(tmp_path / "unused.yaml"), "-Python", str(stub), "-LogRoot", str(logs)],
                   capture_output=True, timeout=30)
    result = json.loads((logs / "latest_process_result.json").read_text(encoding="utf-8-sig"))
    assert result["exit_code"] == exit_code
    log = Path(result["log_path"]).read_text(encoding="utf-8-sig")
    assert "diagnostic" in log
    assert "NativeCommandError" not in log
    assert "CategoryInfo" not in log
    assert not result["broker_contacted"]
