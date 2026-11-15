from cursor_adapter import RealCursorAdapter, _decode_subprocess_stream, run_agent_command


def test_decode_subprocess_stream_handles_invalid_utf8():
    raw = b"hello \x98 world"
    assert _decode_subprocess_stream(raw) == "hello \ufffd world"
    assert _decode_subprocess_stream(None) == ""


def test_extract_output_handles_none_streams():
    assert RealCursorAdapter._extract_output(None, None) == ""
    assert RealCursorAdapter._extract_output(None, "stderr only") == "stderr only"


def test_run_agent_command_version_does_not_raise_on_windows_encoding():
    proc = run_agent_command(["--version"], timeout=30)
    assert proc.returncode == 0
    assert isinstance(proc.stdout, str)
    assert proc.stdout.strip() != ""
