def test_help_lists_commands(run_cli):
    r = run_cli("--help")
    for c in ["setup", "build", "drawing", "print-check", "viewer", "report", "all"]:
        assert c in r.stdout


def test_model_error_exit_code(run_cli, tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text('{"schemaVersion": 99}')
    r = run_cli("build", str(bad), "--out", str(tmp_path / "o"))
    assert r.returncode == 2 and "schemaVersion" in r.stderr
    assert not (tmp_path / "o").exists()


def test_missing_model_file_exit_code(run_cli, tmp_path):
    r = run_cli("build", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o"))
    assert r.returncode == 2 and "cannot read" in r.stderr and "Traceback" not in r.stderr


def test_invalid_json_exit_code(run_cli, tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text("{not json")
    r = run_cli("build", str(bad), "--out", str(tmp_path / "o"))
    assert r.returncode == 2 and "invalid JSON" in r.stderr and "Traceback" not in r.stderr


# ---- venv selection and re-exec decision ----------------------------------------

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import RUN_PY

LOCK_HASH = "ab" * 32


@pytest.fixture(scope="module")
def runmod():
    spec = importlib.util.spec_from_file_location("sketchcad_run_cli", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_venv(root, ready=LOCK_HASH):
    """A venv folder with an interpreter; `ready` is the lock hash setup recorded (None: not ready)."""
    py = root / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    if ready is not None:
        (root / ".sketchcad-ready").write_text(ready)
    return py


def _target(runmod, argv, env=None, prefix="/usr"):
    return runmod.reexec_target(argv, env or {}, prefix, LOCK_HASH)


def test_venv_flag_parses_on_every_command(runmod):
    p = runmod.make_parser()
    assert p.parse_args(["setup", "--venv", "/v"]).venv == "/v"
    assert p.parse_args(["build", "m.json", "--out", "o", "--venv", "/v"]).venv == "/v"
    assert p.parse_args(["--venv", "/v", "viewer", "o"]).venv == "/v"
    assert p.parse_args(["viewer", "o"]).venv is None


def test_reexec_uses_venv_flag(runmod, tmp_path):
    py = _fake_venv(tmp_path / "v")
    got = _target(runmod, ["build", "m.json", "--venv", str(tmp_path / "v")])
    assert got == str(py)
    got = _target(runmod, ["--venv", str(tmp_path / "v"), "build", "m.json"])
    assert got == str(py)
    got = _target(runmod, [f"--venv={tmp_path / 'v'}", "build", "m.json"])
    assert got == str(py)


def test_reexec_uses_env_var_and_flag_wins(runmod, tmp_path):
    env_py = _fake_venv(tmp_path / "env")
    flag_py = _fake_venv(tmp_path / "flag")
    env = {"SKETCHCAD_VENV": str(tmp_path / "env")}
    assert _target(runmod, ["build", "m"], env) == str(env_py)
    assert _target(runmod, ["build", "m", "--venv", str(tmp_path / "flag")], env) == str(flag_py)


def test_reexec_skipped_for_setup_help_or_inside_venv(runmod, tmp_path):
    _fake_venv(tmp_path / "v")
    v = str(tmp_path / "v")
    assert _target(runmod, ["setup", "--venv", v]) is None
    assert _target(runmod, ["build", "m", "--venv", v], prefix=v) is None
    # setup must run even when the venv is broken; --help needs no venv
    _fake_venv(tmp_path / "broken", ready=None)
    assert _target(runmod, ["setup", "--venv", str(tmp_path / "broken")]) is None
    assert _target(runmod, ["--venv", str(tmp_path / "broken"), "--help"]) is None


def test_missing_default_venv_stays_in_this_interpreter(runmod, tmp_path, monkeypatch):
    monkeypatch.setattr(runmod, "default_venv", lambda: tmp_path / "none")
    assert _target(runmod, ["build", "m"]) is None


@pytest.mark.parametrize("state", ["no-marker", "stale-marker", "no-interpreter", "missing"])
@pytest.mark.parametrize("how", ["flag", "env", "default"])
def test_unready_venv_is_refused(runmod, tmp_path, monkeypatch, state, how):
    venv = tmp_path / "v"
    if state == "no-marker":
        _fake_venv(venv, ready=None)
    elif state == "stale-marker":
        _fake_venv(venv, ready="cd" * 32)
    elif state == "no-interpreter":
        venv.mkdir()
        (venv / ".sketchcad-ready").write_text(LOCK_HASH)
    if how == "default" and state == "missing":
        pytest.skip("a missing default venv is allowed (covered above)")
    monkeypatch.setattr(runmod, "default_venv", lambda: venv)
    argv = ["build", "m"] + (["--venv", str(venv)] if how == "flag" else [])
    env = {"SKETCHCAD_VENV": str(venv)} if how == "env" else {}
    with pytest.raises(runmod.EnvNotReady) as e:
        _target(runmod, argv, env)
    msg = str(e.value)
    assert "environment not ready" in msg and "setup" in msg
    if how == "flag":
        assert f"--venv {venv}" in msg


def test_cli_refuses_a_venv_flag_without_interpreter(run_cli, tmp_path):
    r = run_cli("build", "m.json", "--out", tmp_path / "o", "--venv", tmp_path / "none")
    assert r.returncode == 2
    assert "environment not ready" in r.stderr and "Traceback" not in r.stderr


def test_python_version_check(runmod):
    assert runmod.python_version_error((3, 12, 0)) is None
    assert runmod.python_version_error((3, 13, 1)) is None
    msg = runmod.python_version_error((3, 11, 9))
    assert "3.12" in msg and "3.11" in msg and "docker run" in msg


def test_setup_refuses_old_python_before_touching_anything(runmod, tmp_path, monkeypatch, capsys):
    def no_run(*a, **k):
        raise AssertionError("setup ran a command on an unsupported Python")

    monkeypatch.setattr(runmod.subprocess, "run", no_run)
    monkeypatch.setattr(runmod.sys, "version_info", (3, 10, 4))
    args = runmod.make_parser().parse_args(["setup", "--venv", str(tmp_path / "v")])
    assert runmod.cmd_setup(args) == 2
    assert "3.12" in capsys.readouterr().err
    assert not (tmp_path / "v").exists()


def test_setup_writes_ready_marker_with_lock_hash(runmod, tmp_path, monkeypatch):
    venv = tmp_path / "v"

    def fake_run(cmd, check):
        if "venv" in cmd:
            _fake_venv(venv, ready=None)

    monkeypatch.setattr(runmod.subprocess, "run", fake_run)
    args = runmod.make_parser().parse_args(["setup", "--venv", str(venv)])
    assert runmod.cmd_setup(args) == 0
    assert (venv / ".sketchcad-ready").read_text().strip() == runmod.lock_digest()
    assert runmod.reexec_target(["build", "m", "--venv", str(venv)], {}, "/usr",
                                runmod.lock_digest()) == str(venv / "bin" / "python")


def test_docker_hint_mounts_skill_repo_and_working_dir(runmod):
    hint = runmod.docker_hint()
    assert ':/skill' in hint and '"$PWD":/work' in hint and "-w /work" in hint
    assert "python /skill/skills/sketch-to-cad/scripts/run.py" in hint


def test_default_venv_is_inside_skill_folder(runmod):
    assert runmod.default_venv() == runmod.SKILL_DIR / ".venv"


def test_failed_pip_removes_venv_that_setup_created(runmod, tmp_path, monkeypatch, capsys):
    venv = tmp_path / "v"

    def fake_run(cmd, check):
        if "venv" in cmd:
            _fake_venv(venv, ready=None)
            return
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(runmod.subprocess, "run", fake_run)
    args = runmod.make_parser().parse_args(["setup", "--venv", str(venv)])
    assert runmod.cmd_setup(args) == 1
    assert not venv.exists()
    assert "setup failed" in capsys.readouterr().err


def test_failed_pip_keeps_preexisting_venv(runmod, tmp_path, monkeypatch):
    venv = tmp_path / "v"
    _fake_venv(venv, ready=runmod.lock_digest())

    def fake_run(cmd, check):
        if "venv" in cmd:
            return
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(runmod.subprocess, "run", fake_run)
    args = runmod.make_parser().parse_args(["setup", "--venv", str(venv)])
    assert runmod.cmd_setup(args) == 1
    assert venv.exists()
    assert not (venv / ".sketchcad-ready").exists()  # an old marker must not survive a failed setup
