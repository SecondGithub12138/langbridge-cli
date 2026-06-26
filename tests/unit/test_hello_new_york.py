from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "demo" / "hello_new_york.py"
SPEC = importlib.util.spec_from_file_location("hello_new_york_module", MODULE_PATH)
hello_new_york = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(hello_new_york)  # type: ignore[attr-defined]


def test_main_prints_hello_new_york(capsys):
    hello_new_york.main()

    captured = capsys.readouterr()

    assert captured.out == "Hello New York!\n"
    assert captured.err == ""
