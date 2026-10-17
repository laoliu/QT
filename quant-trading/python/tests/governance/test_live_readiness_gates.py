from aipha.governance import live_readiness


def test_qmt_gate_uses_current_package_and_execution_control_tests():
    result = live_readiness._gate_qmt_integration("sample")

    assert result.passed
    assert "current QMT gateway" in result.evidence


def test_manual_override_gate_checks_api_and_cross_process_control():
    result = live_readiness._gate_manual_override("sample")

    assert result.passed
    assert "cross-process broker control" in result.evidence


def test_manual_override_gate_fails_closed_when_artifacts_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(live_readiness, "ROOT", tmp_path)

    result = live_readiness._gate_manual_override("sample")

    assert not result.passed
    assert "missing:" in result.evidence
