import os
from pathlib import Path

import pytest

from rawclean import core
from rawclean.cli import main


def touch(base: Path, *names: str, size: int = 10) -> None:
    for name in names:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)


def names(paths) -> set[str]:
    return {os.path.basename(p if isinstance(p, str) else p[0]) for p in paths}


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    touch(
        tmp_path,
        "IMG_1.CR2", "IMG_1.JPG", "IMG_1.xmp",
        "IMG_2.nef", "IMG_2.jpeg", "IMG_2.nef.pp3",
        "IMG_3.ARW",
        "notes.txt", "IMG_4.jpg",
        "2020/DSC_5.raf", "2020/DSC_5.heic",
        "2020/deep/P6.ORF", "2020/deep/P6.JPG",
        "@eaDir/IMG_7.CR2", "@eaDir/IMG_7.JPG",
        "$RECYCLE.BIN/IMG_8.CR2", "$RECYCLE.BIN/IMG_8.JPG",
    )
    return tmp_path


def test_scan_top_level_only(photos):
    plan = core.scan(str(photos), recursive=False)
    assert names(plan.raws) == {"IMG_1.CR2", "IMG_2.nef", "IMG_3.ARW"}
    assert names(plan.sidecars) == {"IMG_1.xmp", "IMG_2.nef.pp3"}
    assert plan.size == 50


def test_scan_recursive_skips_system_dirs(photos):
    plan = core.scan(str(photos), recursive=True)
    assert names(plan.raws) == {"IMG_1.CR2", "IMG_2.nef", "IMG_3.ARW", "DSC_5.raf", "P6.ORF"}


def test_scan_reports_progress_per_folder(photos):
    folders = []
    core.scan(str(photos), recursive=True, on_dir=lambda p: folders.append(p.dirs))
    assert folders == [1, 2, 3]


def test_dry_run_changes_nothing(photos):
    before = sorted(p for p in photos.rglob("*"))
    assert main([str(photos), "-r"]) == 0
    assert sorted(p for p in photos.rglob("*")) == before


def test_delete_moves_to_quarantine_preserving_structure(photos):
    assert main([str(photos), "-r", "--delete", "--yes"]) == 0
    assert not (photos / "IMG_1.CR2").exists()
    assert not (photos / "IMG_1.xmp").exists()
    assert not (photos / "IMG_3.ARW").exists()
    assert (photos / "IMG_1.JPG").exists()
    [stamp] = (photos / core.QUARANTINE_DIR).iterdir()
    assert (stamp / "IMG_1.CR2").exists()
    assert (stamp / "2020" / "deep" / "P6.ORF").exists()


def test_second_run_ignores_quarantine(photos):
    main([str(photos), "-r", "--delete", "--yes"])
    plan = core.scan(str(photos), recursive=True)
    assert plan.raws == []


def test_purge(photos):
    main([str(photos), "-r", "--delete", "--yes"])
    assert main([str(photos), "--purge"]) == 0
    assert (photos / core.QUARANTINE_DIR).exists()
    assert main([str(photos), "--purge", "--delete", "--yes"]) == 0
    assert not (photos / core.QUARANTINE_DIR).exists()


def test_purge_read_only_file(photos):
    main([str(photos), "--delete", "--yes"])
    [stamp] = (photos / core.QUARANTINE_DIR).iterdir()
    os.chmod(stamp / "IMG_1.CR2", 0o444)
    assert main([str(photos), "--purge", "--delete", "--yes"]) == 0
    assert not (photos / core.QUARANTINE_DIR).exists()


def test_missing_path(tmp_path):
    assert main([str(tmp_path / "nope")]) == 2


def test_long_paths(tmp_path):
    deep = tmp_path.joinpath(*["a" * 50] * 6)
    long_root = core.to_long_path(str(tmp_path))
    long_deep = core.to_long_path(str(deep))
    os.makedirs(long_deep)
    for name in ("IMG.CR2", "IMG.JPG"):
        with open(os.path.join(long_deep, name), "wb") as f:
            f.write(b"x")
    assert len(long_deep) > 260
    assert main([str(tmp_path), "-r", "--delete", "--yes"]) == 0
    assert not os.path.exists(os.path.join(long_deep, "IMG.CR2"))
    assert os.path.exists(os.path.join(long_root, core.QUARANTINE_DIR))


@pytest.mark.skipif(os.name != "nt", reason="Windows path prefixes")
def test_long_path_round_trip():
    assert core.to_long_path("C:\\Photos") == "\\\\?\\C:\\Photos"
    assert core.to_long_path("\\\\nas\\photos") == "\\\\?\\UNC\\nas\\photos"
    assert core.display_path("\\\\?\\UNC\\nas\\photos") == "\\\\nas\\photos"
    assert core.display_path("\\\\?\\C:\\Photos") == "C:\\Photos"
