"""Quick smoke test for the discovery module."""
import sys, json, tempfile, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.test_runner.discovery import discover_test_commands


def test_frontend():
    print("=== Frontend (Vite scaffold) ===")
    r = discover_test_commands("./frontend")
    print(json.dumps(r.to_dict(), indent=2))
    print()


def test_pytest():
    tmp = tempfile.mkdtemp(prefix="mock_pytest_")
    os.makedirs(f"{tmp}/tests")
    open(f"{tmp}/requirements.txt", "w").write("pytest\nflask\n")
    open(f"{tmp}/conftest.py", "w").write("import pytest\n")
    open(f"{tmp}/tests/test_app.py", "w").write("def test_hello(): pass\n")
    print("=== Mock pytest repo ===")
    r = discover_test_commands(tmp)
    print(json.dumps(r.to_dict(), indent=2))
    print()


def test_jest():
    tmp = tempfile.mkdtemp(prefix="mock_jest_")
    pkg = {
        "name": "test",
        "scripts": {"test": "jest --coverage"},
        "devDependencies": {"jest": "^29.0.0"},
    }
    json.dump(pkg, open(f"{tmp}/package.json", "w"))
    os.makedirs(f"{tmp}/__tests__")
    open(f"{tmp}/__tests__/app.test.js", "w").write('test("works", () => {});')
    print("=== Mock Jest repo ===")
    r = discover_test_commands(tmp)
    print(json.dumps(r.to_dict(), indent=2))
    print()


def test_vitest():
    tmp = tempfile.mkdtemp(prefix="mock_vitest_")
    pkg = {
        "name": "test",
        "scripts": {"test": "vitest run"},
        "devDependencies": {"vitest": "^1.0.0"},
    }
    json.dump(pkg, open(f"{tmp}/package.json", "w"))
    open(f"{tmp}/vite.config.ts", "w").write(
        '/// <reference types="vitest" />\nexport default {}'
    )
    open(f"{tmp}/src.test.ts", "w").write('it("works", () => {})')
    print("=== Mock Vitest repo ===")
    r = discover_test_commands(tmp)
    print(json.dumps(r.to_dict(), indent=2))
    print()


def test_unittest():
    tmp = tempfile.mkdtemp(prefix="mock_unittest_")
    os.makedirs(f"{tmp}/tests")
    open(f"{tmp}/tests/test_core.py", "w").write(
        "import unittest\nclass TestCore(unittest.TestCase):\n  def test_a(self): pass\n"
    )
    print("=== Mock unittest repo ===")
    r = discover_test_commands(tmp)
    print(json.dumps(r.to_dict(), indent=2))
    print()


if __name__ == "__main__":
    test_frontend()
    test_pytest()
    test_jest()
    test_vitest()
    test_unittest()
    print("All discovery tests passed!")
