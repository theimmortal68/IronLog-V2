"""Root conftest.py — ensures the repo root is on sys.path for `.venv/bin/pytest`.

Pytest's default "prepend" import mode inserts each test module's rootpath
(the nearest ancestor directory WITHOUT an __init__.py) into sys.path. Since
tests/ has no __init__.py, tests/test_*.py only gets tests/ prepended, not the
repo root — `ironlog` still imports fine (it's pip-installed / editable), but
`scripts` (a plain namespace package, never installed) does not resolve unless
the repo root itself is on sys.path. Having a conftest.py here (repo root, also
without an __init__.py) makes pytest prepend this directory too, so
`from scripts.golive_phase1 import ...` (tests/test_golive_phase1.py, Task 7)
works under both `.venv/bin/pytest` and `python -m pytest`.

No fixtures here — tests/conftest.py remains the home for shared fixtures.
"""
