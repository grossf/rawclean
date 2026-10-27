import os
import stat
from collections.abc import Callable, Iterator
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
    problems: list[Problem] = field(default_factory=list)
    dirs: int = 0
    size: int = 0

    @property
    def files(self) -> list[tuple[str, int]]:
        return self.raws + self.sidecars


@dataclass
class Quarantine:
    path: str
    files: list[tuple[str, int]] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(size for _, size in self.files)


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


def scan(root: str, recursive: bool, seen: set[str] | None = None, on_dir: Callable[[Plan], None] | None = None) -> Plan:
    plan = Plan(root)
    seen = set() if seen is None else seen
    for _, files, _ in _walk(root, recursive, plan.problems):
        plan.dirs += 1
        by_name = {f.name.lower(): f for f in files}
        for entry in files:
            stem, ext = os.path.splitext(entry.name.lower())
            if ext not in RAW_EXTS or entry.path in seen:
                continue
            seen.add(entry.path)
            size = _size(entry, plan.problems)
            if size is None:
                continue
            plan.raws.append((entry.path, size))
            plan.size += size
            for name in (b + e for b in (stem, entry.name.lower()) for e in SIDECAR_EXTS):
                sidecar = by_name.get(name)
                if sidecar is None or sidecar.path in seen:
                    continue
                seen.add(sidecar.path)
                size = _size(sidecar, plan.problems)
                if size is not None:
                    plan.sidecars.append((sidecar.path, size))
                    plan.size += size
        if on_dir:
            on_dir(plan)
    return plan


def quarantine(plan: Plan, stamp: str, on_file: Callable[[], None] | None = None) -> tuple[str, Result]:
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
        else:
            result.count += 1
            result.size += size
        if on_file:
            on_file()
    return dest_root, result


def find_quarantines(root: str, recursive: bool, on_file: Callable[[int], None] | None = None) -> tuple[list[Quarantine], list[Problem]]:
    problems: list[Problem] = []
    found = []
    for _, _, dirs in _walk(root, recursive, problems):
        for d in dirs:
            if d.name.lower() != QUARANTINE_DIR:
                continue
            q = Quarantine(d.path)
            for dirpath, _, filenames in os.walk(d.path, topdown=False, onerror=lambda e: problems.append((e.filename, _reason(e)))):
                for name in filenames:
                    path = os.path.join(dirpath, name)
                    try:
                        size = os.lstat(path).st_size
                    except OSError as exc:
                        problems.append((path, _reason(exc)))
                        continue
                    q.files.append((path, size))
                    if on_file:
                        on_file(size)
                q.dirs.append(dirpath)
            found.append(q)
    return found, problems


def _remove(func: Callable[[str], None], path: str) -> None:
    try:
        func(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        func(path)


def purge(q: Quarantine, on_file: Callable[[], None] | None = None) -> list[Problem]:
    problems: list[Problem] = []
    for path, _ in q.files:
        try:
            _remove(os.unlink, path)
        except OSError as exc:
            problems.append((path, _reason(exc)))
        if on_file:
            on_file()
    for path in q.dirs:
        try:
            _remove(os.rmdir, path)
        except OSError as exc:
            problems.append((path, _reason(exc)))
    return problems
