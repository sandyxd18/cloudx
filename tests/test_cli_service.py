import subprocess
import sys
from pathlib import Path

def test_cli_service_help():
    cli_main = Path(__file__).resolve().parents[1] / "cli" / "cloudx_cli" / "main.py"
    res = subprocess.run([sys.executable, str(cli_main), "service", "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "service" in res.stdout
    assert "status" in res.stdout
    assert "list" in res.stdout
