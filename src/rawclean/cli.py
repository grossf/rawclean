import argparse
import os
import sys
from datetime import datetime
from importlib.metadata import version

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from . import core


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def counter_progress() -> Progress:
    return Progress(SpinnerColumn(), TextColumn("{task.description}", markup=False), TimeElapsedColumn())


def bar_progress() -> Progress:
    return Progress(
        TextColumn("{task.description}", markup=False),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("No interactive terminal to confirm in. Use --yes to proceed anyway.")
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def print_problems(problems: list[core.Problem]) -> None:
    if not problems:
        return
    print(f"\nProblems ({len(problems):,}):")
    for path, reason in problems:
        print(f"  {core.display_path(path)}: {reason}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rawclean",
        description=(
            f"Find camera RAW files and move them into a '{core.QUARANTINE_DIR}' folder. "
            "Without --delete nothing is changed (dry run)."
        ),
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="folder(s) to clean, e.g. D:\\Photos or \\\\nas\\photos")
    parser.add_argument("-r", "--recursive", action="store_true", help="include all subfolders")
    parser.add_argument("--delete", action="store_true", help="actually move the files (or with --purge: delete the quarantine)")
    parser.add_argument("--purge", action="store_true", help=f"permanently delete '{core.QUARANTINE_DIR}' folders")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('rawclean')}")
    return parser.parse_args(argv)


def resolve_roots(paths: list[str]) -> list[str] | None:
    roots, missing = [], []
    for p in paths:
        root = core.to_long_path(p)
        if os.path.isdir(root):
            if root not in roots:
                roots.append(root)
        else:
            missing.append(p)
    for p in missing:
        print(f"Folder not found or not reachable: {p}")
    return None if missing else roots


def scan_status(name: str, plan: core.Plan) -> str:
    return f"{name}: {plan.dirs:,} folders, {len(plan.raws):,} RAW files, {human_size(plan.size)}"


def run_clean(args: argparse.Namespace, roots: list[str]) -> int:
    seen: set[str] = set()
    plans = []
    with counter_progress() as progress:
        for root in roots:
            name = core.display_path(root)
            task = progress.add_task(f"Scanning {name} ...")
            plan = core.scan(
                root, args.recursive, seen,
                lambda p, task=task, name=name: progress.update(task, description=f"Scanning {scan_status(name, p)}"),
            )
            progress.update(task, description=f"Scanned {scan_status(name, plan)}", total=1, completed=1)
            plans.append(plan)

    files = [f for p in plans for f in p.files]
    problems = [x for p in plans for x in p.problems]
    total = sum(p.size for p in plans)

    if not args.delete and files:
        print("\nWould move to quarantine:")
        for path, size in sorted(files, key=lambda f: f[0].lower()):
            print(f"  {core.display_path(path)}  ({human_size(size)})")

    print("\nSummary")
    print(f"  RAW files:      {sum(len(p.raws) for p in plans):,}")
    print(f"  Sidecar files:  {sum(len(p.sidecars) for p in plans):,}")
    print(f"  Total size:     {human_size(total)}")

    if not args.delete:
        print_problems(problems)
        if files:
            print("\nDry run: nothing was changed. Run again with --delete to move these files to quarantine.")
        return 1 if problems else 0

    if not files:
        print("\nNothing to do.")
        print_problems(problems)
        return 1 if problems else 0

    if not confirm(f"\nMove {len(files):,} files ({human_size(total)}) to quarantine?", args.yes):
        print("Cancelled, nothing was changed.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    moved = core.Result()
    destinations = []
    with bar_progress() as progress:
        task = progress.add_task("Moving to quarantine", total=len(files))
        for plan in plans:
            if not plan.files:
                continue
            dest, result = core.quarantine(plan, stamp, lambda: progress.advance(task))
            destinations.append(dest)
            moved.count += result.count
            moved.size += result.size
            problems.extend(result.problems)

    print(f"\nMoved {moved.count:,} files ({human_size(moved.size)}) to:")
    for dest in destinations:
        print(f"  {core.display_path(dest)}")
    print_problems(problems)
    quoted = " ".join(f'"{core.display_path(r)}"' for r in roots)
    print(f"\nCheck your photos. To free the space, run: rawclean {quoted} --purge --delete")
    return 1 if problems else 0


def run_purge(args: argparse.Namespace, roots: list[str]) -> int:
    found: list[core.Quarantine] = []
    problems: list[core.Problem] = []
    count = total = 0

    with counter_progress() as progress:
        task = progress.add_task("Looking for quarantine folders ...")

        def on_file(size: int) -> None:
            nonlocal count, total
            count += 1
            total += size
            progress.update(task, description=f"Looking for quarantine folders: {count:,} files, {human_size(total)}")

        for root in roots:
            f, p = core.find_quarantines(root, args.recursive, on_file)
            found.extend(f)
            problems.extend(p)
        progress.update(task, total=1, completed=1)

    if found:
        print("\nQuarantine folders:")
        for q in found:
            print(f"  {core.display_path(q.path)}  ({len(q.files):,} files, {human_size(q.size)})")
    else:
        print("\nNo quarantine folders found." + ("" if args.recursive else " Use -r to search subfolders."))

    if not args.delete or not found:
        print_problems(problems)
        if found:
            print("\nDry run: nothing was changed. Run again with --purge --delete to delete these permanently.")
        return 1 if problems else 0

    if not confirm(f"\nPermanently delete {count:,} files ({human_size(total)})? This cannot be undone.", args.yes):
        print("Cancelled, nothing was changed.")
        return 0

    with bar_progress() as progress:
        task = progress.add_task("Deleting", total=count)
        for q in found:
            problems.extend(core.purge(q, lambda: progress.advance(task)))
    print(f"\nDeleted {len(found):,} quarantine folder(s).")
    print_problems(problems)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    roots = resolve_roots(args.paths)
    if roots is None:
        return 2
    try:
        return run_purge(args, roots) if args.purge else run_clean(args, roots)
    except KeyboardInterrupt:
        print("\nAborted.")
        return 130
