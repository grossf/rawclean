import os
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field

RAW_EXTS = frozenset({
    ".cr2", ".cr3", ".crw",  # Canon
    ".nef", ".nrw",  # Nikon
    ".arw", ".srf", ".sr2",  # Sony
    ".raf",  # Fujifilm
    ".orf",  # Olympus / OM System
    ".rw2",  # Panasonic
    ".pef",  # Pentax
    ".srw",  # Samsung
    ".3fr",  # Hasselblad
    ".iiq",  # Phase One
})
PARTNER_EXTS = frozenset({".jpg", ".jpeg", ".heic", ".heif"})
SIDECAR_EXTS = frozenset({".xmp", ".pp3", ".dop"})
QUARANTINE_DIR = "_rawclean_trash"
SKIP_DIRS = frozenset({
    "$recycle.bin",
    "system volume information",
    "#recycle",
    "@recycle",
    "#snapshot",
    ".snapshot",
    "@recently-snapshot",
    "@eadir",
    QUARANTINE_DIR,
})

Problem = tuple[str, str]


@dataclass
class Plan:
    root: str
    raws: list[tuple[str, int]] = field(default_factory=list)
    sidecars: list[tuple[str, int]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def files(self) -> list[tuple[str, int]]:
        return self.raws + self.sidecars


@dataclass
class Result:
    count: int = 0
    size: int = 0
    problems: list[Problem] = field(default_factory=list)


def to_long_path(path: str) -> str:
    path = os.path.abspath(path)
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def display_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _reason(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _walk(top: str, recursive: bool, problems: list[Problem]) -> Iterator[tuple[str, list[os.DirEntry], list[os.DirEntry]]]:
    stack = [top]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError as exc:
            problems.append((current, _reason(exc)))
            continue
        files, dirs = [], []
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir():
                    dirs.append(entry)
                elif entry.is_file():
                    files.append(entry)
            except OSError as exc:
                problems.append((entry.path, _reason(exc)))
        yield current, files, dirs
        if recursive:
            stack.extend(d.path for d in reversed(dirs) if d.name.lower() not in SKIP_DIRS)


def _size(entry: os.DirEntry, problems: list[Problem]) -> int | None:
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError as exc:
        problems.append((entry.path, _reason(exc)))
        return None


def scan(root: str, recursive: bool, include_orphans: bool, seen: set[str] | None = None) -> Plan:
    plan = Plan(root)
    seen = set() if seen is None else seen
    for _, files, _ in _walk(root, recursive, plan.problems):
        by_name = {f.name.lower(): f for f in files}
        picked_sidecars: set[str] = set()
        for entry in files:
            stem, ext = os.path.splitext(entry.name.lower())
            if ext not in RAW_EXTS or entry.path in seen:
                continue
            seen.add(entry.path)
            bases = (stem, entry.name.lower())
            if not include_orphans and not any(b + e in by_name for b in bases for e in PARTNER_EXTS):
                plan.orphans.append(entry.path)
                continue
            size = _size(entry, plan.problems)
            if size is None:
                continue
            plan.raws.append((entry.path, size))
            for name in (b + e for b in bases for e in SIDECAR_EXTS):
                sidecar = by_name.get(name)
                if sidecar is None or name in picked_sidecars or sidecar.path in seen:
                    continue
                picked_sidecars.add(name)
                seen.add(sidecar.path)
                size = _size(sidecar, plan.problems)
                if size is not None:
                    plan.sidecars.append((sidecar.path, size))
    return plan


def quarantine(plan: Plan, stamp: str) -> tuple[str, Result]:
    dest_root = os.path.join(plan.root, QUARANTINE_DIR, stamp)
    result = Result()
    for path, size in plan.files:
        dest = os.path.join(dest_root, path[len(plan.root):].lstrip("\\/"))
        try:
            if os.path.lexists(dest):
                raise FileExistsError(0, "Already exists in quarantine")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.rename(path, dest)
        except OSError as exc:
            result.problems.append((path, _reason(exc)))
            continue
        result.count += 1
        result.size += size
    return dest_root, result


def find_quarantines(root: str, recursive: bool) -> tuple[list[tuple[str, int, int]], list[Problem]]:
    problems: list[Problem] = []
    found = []
    for _, _, dirs in _walk(root, recursive, problems):
        for d in dirs:
            if d.name.lower() != QUARANTINE_DIR:
                continue
            count = size = 0
            for dirpath, _, filenames in os.walk(d.path, onerror=lambda e: problems.append((e.filename, _reason(e)))):
                for name in filenames:
                    try:
                        size += os.lstat(os.path.join(dirpath, name)).st_size
                        count += 1
                    except OSError as exc:
                        problems.append((os.path.join(dirpath, name), _reason(exc)))
            found.append((d.path, count, size))
    return found, problems


def purge(path: str) -> list[Problem]:
    problems: list[Problem] = []

    def on_error(func, failed_path, exc):
        if isinstance(exc, PermissionError):
            try:
                os.chmod(failed_path, stat.S_IWRITE)
                func(failed_path)
                return
            except OSError as retry_exc:
                exc = retry_exc
        problems.append((failed_path, _reason(exc)))

    shutil.rmtree(path, onexc=on_error)
    return problems
