from pathlib import Path
import tomllib


def test_ruff_per_file_ignore_paths_exist():
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    ignores = pyproject["tool"]["ruff"]["lint"]["per-file-ignores"]

    missing = [
        path
        for path in ignores
        if "*" not in path and not (root / path).exists()
    ]

    assert missing == []
