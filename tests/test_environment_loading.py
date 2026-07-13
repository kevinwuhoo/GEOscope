from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
TEST_KEYS = (
    "GEOSCOPE_TEST_GENERAL",
    "GEOSCOPE_TEST_ELASTIC",
    "GEOSCOPE_TEST_SHARED",
    "GEOSCOPE_TEST_PROCESS",
)


def _import_environment(cwd: Path, **environment: str) -> dict[str, str | None]:
    process_environment = os.environ.copy()
    for key in TEST_KEYS:
        process_environment.pop(key, None)
    process_environment.update(environment)
    process_environment["PYTHONPATH"] = str(ROOT / "src")
    script = (
        "import json, os; import geo_index; "
        f"print(json.dumps({{key: os.environ.get(key) for key in {TEST_KEYS!r}}}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=process_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_package_import_loads_dotenv_files_with_safe_precedence(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "GEOSCOPE_TEST_GENERAL=general\n"
        "GEOSCOPE_TEST_SHARED=general\n"
        "GEOSCOPE_TEST_PROCESS=general\n"
    )
    (tmp_path / ".env.elasticsearch").write_text(
        "GEOSCOPE_TEST_ELASTIC=elastic\n"
        "GEOSCOPE_TEST_SHARED=elastic\n"
        "GEOSCOPE_TEST_PROCESS=elastic\n"
    )

    loaded = _import_environment(
        tmp_path,
        GEOSCOPE_TEST_PROCESS="process",
    )

    assert loaded == {
        "GEOSCOPE_TEST_GENERAL": "general",
        "GEOSCOPE_TEST_ELASTIC": "elastic",
        "GEOSCOPE_TEST_SHARED": "elastic",
        "GEOSCOPE_TEST_PROCESS": "process",
    }


def test_package_import_ignores_missing_dotenv_files(tmp_path: Path) -> None:
    assert _import_environment(tmp_path) == {key: None for key in TEST_KEYS}
