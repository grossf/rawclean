# rawclean

Removes camera RAW files from local drives and network shares (NAS), with progress bars for every step.

Files are never deleted directly: they are first moved into a `_rawclean_trash` folder inside the folder you cleaned, keeping the original folder structure. Only `--purge --delete` deletes them for good.

## Install (Windows 11)

1. Install [uv](https://docs.astral.sh/uv/) once. Open PowerShell and run:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. Close and reopen PowerShell, then install rawclean:

   ```powershell
   uv tool install --reinstall "rawclean @ https://github.com/grossf/rawclean/archive/refs/tags/latest.zip"
   ```

Run the same command again to update to the newest release. uv downloads its own Python, so nothing else is needed (no Git either).

## Usage

```powershell
# 1. Preview: lists what would be moved, changes nothing
rawclean D:\Photos -r
rawclean \\nas\photos -r

# 2. Move the RAW files into D:\Photos\_rawclean_trash\<date-time>\
rawclean D:\Photos -r --delete

# 3. After checking the photos: show, then permanently delete the quarantine
rawclean D:\Photos -r --purge
rawclean D:\Photos -r --purge --delete
```

Several folders can be passed at once: `rawclean D:\Photos \\nas\photos -r`.

| Option | Meaning |
| --- | --- |
| `-r`, `--recursive` | include all subfolders |
| `--delete` | actually move files (with `--purge`: delete the quarantine) |
| `--purge` | work on `_rawclean_trash` folders instead |
| `-y`, `--yes` | skip the confirmation question |

### What gets removed

- RAW files: `cr2 cr3 crw nef nrw arw srf sr2 raf orf rw2 pef srw 3fr iiq` (any letter case). DNG is deliberately not included, because it is often the only copy of a photo.
- Their sidecar files: `IMG_1.xmp`, `IMG_1.CR2.xmp`, `.pp3`, `.dop`.

Skipped: symlinks and junctions, `$RECYCLE.BIN`, `System Volume Information`, NAS recycle bins and snapshots (`#recycle`, `@Recycle`, `#snapshot`, `.snapshot`, `@Recently-Snapshot`), Synology `@eaDir`, and `_rawclean_trash` itself.

## Development

```powershell
uv run pytest
uv run rawclean --help
```

### Releasing

1. Bump `version` in `pyproject.toml` and commit.
2. Tag and push: `git tag v0.2.0 && git push origin main v0.2.0`.

The release workflow runs the tests, checks the tag matches the version, builds the package, creates the GitHub release and moves the `latest` tag to it.
