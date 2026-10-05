import inspect
import os
import sys
import time
from pathlib import Path

import pytest
from shared.proc import DEFAULT_TIMEOUT, NOT_FOUND, TIMED_OUT, CommandError, output, run

PY = sys.executable

def py(code, *args):
    return [PY, "-c", code, *args]

def test_returns_captured_output():
    res = run(py("print('hello')"))
    assert res.returncode == 0 and res.stdout.strip() == "hello"
    assert output(py("print('  padded  ')")) == "padded"

@pytest.mark.parametrize("bad", ["echo hi", b"echo hi", [], ()])
def test_a_string_or_empty_command_is_rejected(bad):
    with pytest.raises(TypeError):
        run(bad)

def test_arguments_are_never_interpreted_by_a_shell():
    payload = "a; echo injected && exit 9 $(whoami) `id`"
    assert output(py("import sys; print(sys.argv[1])", payload)) == payload

def test_failure_raises_command_error_with_context():
    with pytest.raises(CommandError) as exc:
        run(py("import sys; sys.stderr.write('disk on fire'); sys.exit(3)"))
    assert exc.value.returncode == 3 and "disk on fire" in str(exc.value) and "exit 3" in str(exc.value)
    assert isinstance(exc.value, RuntimeError)  # callers that caught RuntimeError keep working

def test_check_false_returns_the_failure_instead_of_raising():
    res = run(py("import sys; sys.exit(5)"), check=False)
    assert res.returncode == 5

def test_missing_binary_is_reported_like_a_failing_command():
    res = run(["cloudx-no-such-binary", "--x"], check=False)
    assert res.returncode == NOT_FOUND and "command not found" in res.stderr
    with pytest.raises(CommandError, match="command not found"):
        run(["cloudx-no-such-binary"])

def test_timeout_kills_the_command_and_is_reported():
    started = time.monotonic()
    res = run(py("import time; time.sleep(30)"), check=False, timeout=0.4)
    assert res.returncode == TIMED_OUT and time.monotonic() - started < 10
    with pytest.raises(CommandError, match="timed out after 0.4s"):
        run(py("import time; time.sleep(30)"), timeout=0.4)

def test_every_command_has_a_timeout_unless_the_caller_opts_out():
    assert inspect.signature(run).parameters["timeout"].default == DEFAULT_TIMEOUT == 60
    assert run(py("print(1)"), timeout=None).returncode == 0  # explicit, e.g. for a long volume copy

def test_secrets_are_masked_in_errors_and_logs(caplog):
    code = "import sys; sys.stderr.write('bad key hunter2'); sys.exit(1)"
    with caplog.at_level("DEBUG", logger="cloudx.proc"):
        with pytest.raises(CommandError) as exc:
            run(py(code, "--secret=hunter2"), secrets=["hunter2"])
    assert "hunter2" not in str(exc.value) and "hunter2" not in " ".join(exc.value.cmd) and "hunter2" not in exc.value.stderr
    assert "hunter2" not in caplog.text

def test_input_cwd_and_env(tmp_path):
    assert output(py("import sys; print(sys.stdin.read().upper())"), input="abc") == "ABC"
    assert Path(output(py("import os; print(os.getcwd())"), cwd=tmp_path)).resolve() == tmp_path.resolve()
    assert output(py("import os; print(os.environ['CLOUDX_T'])"), env={**os.environ, "CLOUDX_T": "v1"}) == "v1"

def test_services_do_not_call_subprocess_directly():
    """All command execution goes through shared.proc (the dashboard's interactive shell is the one exception)."""
    import re

    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"\bsubprocess\.(run|Popen|call|check_call|check_output)\b|\bos\.(system|popen)\(")
    offenders = []
    for path in (root / "services").rglob("*.py"):
        parts = set(path.parts)
        if {"node_modules", "__pycache__", "versions"} & parts or path.name == "proc.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], "use shared.proc.run instead of subprocess/os.system in:\n" + "\n".join(offenders)

def test_succeeds_is_three_valued_for_probes():
    from shared.proc import succeeds

    assert succeeds(py("pass")) is True
    assert succeeds(py("import sys; sys.exit(1)")) is False
    with pytest.raises(CommandError, match="command not found"):
        succeeds(["cloudx-no-such-binary"])
    with pytest.raises(CommandError, match="timed out"):
        succeeds(py("import time; time.sleep(30)"), timeout=0.3)
