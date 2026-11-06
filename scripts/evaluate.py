"""Run submission gates and save concise machine-readable evidence."""

import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree


def main():
    Path("data").mkdir(exist_ok=True)
    checks = [
        ("lint", [sys.executable, "-m", "ruff", "check", "."]),
        ("format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
        ("dependencies", [sys.executable, "-m", "pip", "check"]),
        (
            "tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--cov=quilr_gateway",
                "--cov-report=term-missing",
                "--cov-report=json:data/coverage.json",
                "--cov-fail-under=80",
                "--junitxml=data/pytest.xml",
            ],
        ),
        ("live_http", [sys.executable, "scripts/smoke_test.py"]),
        ("redaction_memory", [sys.executable, "scripts/benchmark_redaction.py"]),
    ]
    report = {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "checks": {},
    }
    for name, command in checks:
        print(f"Running {name}...", flush=True)
        result = subprocess.run(command, check=False)
        report["checks"][name] = "passed" if result.returncode == 0 else "failed"
        if result.returncode:
            raise SystemExit(result.returncode)
    suites = ElementTree.parse("data/pytest.xml").getroot()
    report["tests"] = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    report["failures"] = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    coverage = json.loads(Path("data/coverage.json").read_text(encoding="utf-8"))
    report["line_coverage_percent"] = round(coverage["totals"]["percent_covered"], 2)
    report["not_executed"] = [
        "Docker image build/runtime",
        "Paid live model providers",
        "Linux/Python 3.11 CI matrix (runs on GitHub after publication)",
    ]
    destination = Path("docs/results/validation.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
