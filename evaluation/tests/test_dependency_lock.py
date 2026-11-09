"""SEC-09: Python dependencies are pinned and the lock agrees with the top-level file."""
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO = Path(__file__).resolve().parents[2]


def _entries(path: Path) -> list[Requirement]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.append(Requirement(line))
    return out


def _lock() -> dict[str, str]:
    pins = {}
    for req in _entries(REPO / "requirements.lock"):
        spec = list(req.specifier)
        assert len(spec) == 1 and spec[0].operator == "==", f"lock entry is not an exact pin: {req}"
        pins[canonicalize_name(req.name)] = spec[0].version
    return pins


def test_top_level_requirements_are_exact_pins_matching_the_lock():
    lock = _lock()
    for path in (REPO / "requirements.txt", REPO / "backend" / "requirements.txt"):
        for req in _entries(path):
            spec = list(req.specifier)
            assert len(spec) == 1 and spec[0].operator == "==", f"{path.name}: {req} is not pinned"
            assert lock.get(canonicalize_name(req.name)) == spec[0].version, f"{req} disagrees with requirements.lock"


def test_lock_is_not_empty_and_covers_transitive_dependencies():
    lock = _lock()
    # A few transitive dependencies the app relies on indirectly.
    for name in ("starlette", "anyio", "pydantic-core", "httpcore", "thrift"):
        assert canonicalize_name(name) in lock
