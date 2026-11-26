#!/usr/bin/env python3
"""sketch-to-cad CLI entry point."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

LOCK = SKILL_DIR / "requirements-lock.txt"
READY_MARKER = ".sketchcad-ready"  # written by setup after a complete install: the lock's sha256
MIN_PYTHON = (3, 12)  # the lock file is resolved for 3.12


class EnvNotReady(Exception):
    """The selected venv is missing, half built or older than the lock file."""


def default_venv():
    return SKILL_DIR / ".venv"


def lock_digest():
    return hashlib.sha256(LOCK.read_bytes()).hexdigest()


def venv_python(venv):
    """The venv's interpreter (POSIX or Windows layout), or None."""
    for rel in ("bin/python", "Scripts/python.exe"):
        if (Path(venv) / rel).is_file():
            return Path(venv) / rel
    return None


def docker_hint():
    """How to run the CLI in the repo's Docker image instead of a local venv."""
    repo = SKILL_DIR.parent.parent
    root = str(repo) if (repo / "Dockerfile").is_file() else "<sketch-to-cad repo clone>"
    return (
        "Alternative: use Docker (the image has Python 3.12 and every dependency).\n"
        f'  docker build -t sketch-to-cad-dev "{root}"\n'
        f'  docker run --rm -u "$(id -u)" -v "{root}":/skill -v "$PWD":/work -w /work sketch-to-cad-dev \\\n'
        "    python /skill/skills/sketch-to-cad/scripts/run.py <command> ...\n"
        "  (run it from your working directory; paths are relative to it)"
    )


def python_version_error(version_info):
    """Message when this Python is too old for the pinned dependencies, else None."""
    if tuple(version_info[:2]) >= MIN_PYTHON:
        return None
    have = ".".join(str(v) for v in version_info[:3])
    return (
        f"sketch-to-cad needs Python 3.12 or newer (tested on 3.12); this is Python {have}.\n"
        "Run setup with a newer interpreter (for example `python3.12 run.py setup`), or:\n"
        + docker_hint()
    )


def _argv_venv(argv):
    """The --venv value from raw argv (`--venv DIR` or `--venv=DIR`), or None."""
    for i, a in enumerate(argv):
        if a == "--venv":
            return argv[i + 1] if i + 1 < len(argv) else None
        if a.startswith("--venv="):
            return a[len("--venv="):]
    return None


def _argv_command(argv):
    """First non-option token, skipping the value of --venv."""
    skip = False
    for a in argv:
        if skip:
            skip = False
        elif a == "--venv":
            skip = True
        elif not a.startswith("-"):
            return a
    return None


def reexec_target(argv, env, prefix, lock_hash):
    """Interpreter to re-run under, or None to stay in this one.

    The venv is --venv, else $SKETCHCAD_VENV, else skills/sketch-to-cad/.venv.
    Stays for `setup` (it may be repairing a broken venv), for no command
    (--help), when already running inside the venv, and when the default venv
    does not exist (dependencies installed some other way, e.g. Docker).
    Raises EnvNotReady when a venv named by --venv / $SKETCHCAD_VENV is missing,
    or when the chosen venv has no interpreter or no ready marker for this lock.
    """
    command = _argv_command(argv)
    if command in (None, "setup"):
        return None
    flag = _argv_venv(argv)
    explicit = flag or env.get("SKETCHCAD_VENV")
    venv = Path(explicit or default_venv())
    if Path(prefix).resolve() == venv.resolve():
        return None
    if not venv.exists() and not explicit:
        return None
    rerun = f"run.py setup --venv {venv}" if flag else "run.py setup"
    py = venv_python(venv)
    marker = venv / READY_MARKER
    if py is None:
        why = "has no Python interpreter" if venv.exists() else "does not exist"
    elif not marker.is_file():
        why = "was not completely set up"
    elif marker.read_text(encoding="utf-8").strip() != lock_hash:
        why = "was set up for a different requirements-lock.txt (the skill was updated)"
    else:
        return str(py)
    raise EnvNotReady(f"environment not ready: venv {venv} {why}. Run `{rerun}` (again).")


def _maybe_reexec():
    try:
        py = reexec_target(sys.argv[1:], os.environ, sys.prefix, lock_digest())
    except EnvNotReady as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if py:
        os.execv(py, [py, *sys.argv])


RUNTIME_PROBE = "import OCP.OCP"  # OpenCascade loads the system OpenGL library (libGL.so.1 on Linux)


def runtime_error(py):
    """Why the installed packages cannot load (a missing system library), or None."""
    r = subprocess.run([str(py), "-c", RUNTIME_PROBE], capture_output=True, text=True)
    if r.returncode == 0:
        return None
    lines = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else f"exit code {r.returncode}"
    msg = f"the CAD kernel (OpenCascade) does not load: {last}"
    if "libGL" in last:
        msg += (
            "\nInstall the system OpenGL library, then run setup again:\n"
            "  Debian/Ubuntu/WSL: sudo apt install libgl1\n"
            "  Fedora/RHEL:       sudo dnf install mesa-libGL"
        )
    return msg


def cmd_setup(args):
    problem = python_version_error(sys.version_info)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    venv = Path(args.venv) if args.venv else Path(os.environ.get("SKETCHCAD_VENV") or default_venv())
    existed = venv.exists()
    try:
        (venv / READY_MARKER).unlink(missing_ok=True)  # not ready until this install completes
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        py = venv_python(venv)
        if py is None:
            raise OSError(f"{venv}: venv has no Python interpreter")
        subprocess.run([str(py), "-m", "pip", "install", "-r", str(LOCK)], check=True)
        problem = runtime_error(py)
        if problem:  # packages are fine; keep the venv so the rerun is quick, but not ready
            print(f"setup failed: {problem}", file=sys.stderr)
            print(docker_hint(), file=sys.stderr)
            return 1
        (venv / READY_MARKER).write_text(lock_digest() + "\n", encoding="utf-8")
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"setup failed: {e}", file=sys.stderr)
        if not existed:  # a half-built venv would be picked up by later runs
            shutil.rmtree(venv, ignore_errors=True)
        print(docker_hint(), file=sys.stderr)
        return 1
    print(f"setup complete: {venv}")
    return 0


def cmd_build(args):
    from sketchcad.build import run_build

    out = Path(args.out)
    manifest = run_build(Path(args.model), out)
    print(f"built {out} ({len(manifest['parts'])} parts)")
    return 0


def cmd_drawing(args):
    from sketchcad.drawing import run_drawing

    manifest = run_drawing(Path(args.out), projection=args.projection)
    for sheet in manifest["drawing"]["sheets"]:
        print(f"{args.out}/{sheet['svg']}  {args.out}/{sheet['pdf']}  scale {sheet['scale']}")
    return 0


def cmd_print_check(args):
    from sketchcad.printing import run_print_check

    printer = _load_printer(args.printer) if args.printer else None
    report = run_print_check(Path(args.out), printer)
    for pid, p in report["parts"].items():
        print(f"{pid}: {len(p['warnings'])} warning(s)")
        for w in p["warnings"]:
            print(f"  - {w}")
    print(f"{args.out}/print/print-report.md")
    return 0


def cmd_viewer(args):
    from sketchcad.viewer import run_viewer

    print(run_viewer(Path(args.out)))
    return 0


def cmd_report(args):
    from sketchcad.report import write_report

    print(write_report(Path(args.out)))
    return 0


def _load_printer(path):
    from sketchcad.errors import ModelError

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise ModelError(f"{path}: cannot read printer file ({e.strerror or e})") from e
    except ValueError as e:
        raise ModelError(f"{path}: invalid JSON ({e})") from e


def cmd_all(args):
    from sketchcad.build import run_build
    from sketchcad.drawing import PROJECTIONS, run_drawing
    from sketchcad.errors import ModelError
    from sketchcad.printing import run_print_check, validate_printer
    from sketchcad.report import write_report
    from sketchcad.viewer import run_viewer

    out = Path(args.out)
    if args.projection not in PROJECTIONS:  # fail fast, before build
        raise ModelError("projection: must be first or third")
    printer = None
    if args.printer:  # fail fast, before build
        printer = _load_printer(args.printer)
        validate_printer(printer)
    manifest = run_build(Path(args.model), out)
    run_drawing(out, projection=args.projection)
    report = run_print_check(out, printer)
    run_viewer(out)
    path = write_report(out)
    warnings = sum(len(p["warnings"]) for p in report["parts"].values())
    print(f"done: {len(manifest['parts'])} part(s), {warnings} print warning(s)")
    print(path)
    return 0


def make_parser():
    p = argparse.ArgumentParser(prog="run.py", description="Hand sketch to CAD to 3D print.")
    venv_help = "virtualenv folder (default: $SKETCHCAD_VENV, else skills/sketch-to-cad/.venv)"
    p.add_argument("--venv", metavar="DIR", default=None, help=venv_help)
    # also accepted after the command; SUPPRESS keeps a value given before it
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--venv", metavar="DIR", default=argparse.SUPPRESS, help=venv_help)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "setup", parents=[common], help="create the venv and install pinned dependencies"
    ).set_defaults(func=cmd_setup)

    b = sub.add_parser("build", parents=[common], help="build parts and assembly from model.json")
    b.add_argument("model", help="path to model.json")
    b.add_argument("--out", required=True, help="output folder (must not exist)")
    b.set_defaults(func=cmd_build)

    d = sub.add_parser("drawing", parents=[common], help="generate dimensioned drawing sheets (SVG + PDF)")
    d.add_argument("out", help="build output folder")
    d.add_argument("--projection", default="third", help="first or third (default third)")
    d.set_defaults(func=cmd_drawing)

    pc = sub.add_parser("print-check", parents=[common], help="run 3D print checks and export print files")
    pc.add_argument("out", help="build output folder")
    pc.add_argument("--printer", help="printer profile JSON (any subset of the default keys)")
    pc.set_defaults(func=cmd_print_check)

    v = sub.add_parser("viewer", parents=[common], help="generate the single-file viewer (viewer/index.html)")
    v.add_argument("out", help="build output folder")
    v.set_defaults(func=cmd_viewer)

    r = sub.add_parser("report", parents=[common], help="write report.md for a folder whose stages all ran")
    r.add_argument("out", help="build output folder")
    r.set_defaults(func=cmd_report)

    a = sub.add_parser("all", parents=[common], help="run every stage and write report.md")
    a.add_argument("model", help="path to model.json")
    a.add_argument("--out", required=True, help="output folder (must not exist)")
    a.add_argument("--printer", help="printer profile JSON (any subset of the default keys)")
    a.add_argument("--projection", default="third", help="first or third (default third)")
    a.set_defaults(func=cmd_all)
    return p


def main(argv=None):
    from sketchcad.errors import ModelError

    args = make_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    _maybe_reexec()
    sys.exit(main())
