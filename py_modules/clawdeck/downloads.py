"""Fetch upstream releases ClawDeck installs on request: pinned in assets/versions.json, downloaded from the
project's own GitHub releases at the moment the user taps Install (ClawDeck never redistributes upstream binaries,
proprietary or not). Extraction refuses absolute paths and '..' so an archive cannot write outside its folder."""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import subprocess
import tarfile
import urllib.request

from . import tls
import zipfile

log = logging.getLogger("clawdeck.downloads")

API = "https://api.github.com/repos/{repo}/releases/{which}"
UNPINNED = ("", "pending-verification", None)


def load_versions(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_pinned(entry: dict) -> bool:
    return entry.get("tag") not in UNPINNED


def _get(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ClawDeck"})
    with tls.urlopen(req, timeout) as r:
        return r.read()


def release(repo: str, tag: str | None) -> dict:
    which = f"tags/{tag}" if tag not in UNPINNED else "latest"
    return json.loads(_get(API.format(repo=repo, which=which)))


def newest_with_asset(repo: str, pattern: str, prereleases: bool = True) -> dict | None:
    """The newest release (prereleases included when asked) that has an asset matching pattern. For projects that
    only publish prereleases, such as Moonlight VRR's daily builds, where releases/latest finds nothing."""
    rels = json.loads(_get(f"https://api.github.com/repos/{repo}/releases?per_page=10"))
    for rel in rels:
        if rel.get("draft") or (rel.get("prerelease") and not prereleases):
            continue
        if pick_asset(rel, pattern):
            return rel
    return None


def sha256_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


UNREACHABLE = "!unreachable"


def expected_sha256(rel: dict, asset_name: str) -> str | None:
    """The checksum a release publishes for one of its assets (SHA256SUMS / *.sha256 / sha256sum.txt), if any."""
    for a in rel.get("assets", []):
        n = a.get("name", "").lower()
        if n in ("sha256sums", "sha256sums.txt", "sha256sum.txt", "checksums.txt") or n == asset_name.lower() + ".sha256":
            try:
                text = _get(a["browser_download_url"]).decode("utf-8", errors="replace")
            except OSError:
                return UNREACHABLE      # the release publishes sums but they can't be read: don't install unchecked
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name:
                    return parts[0].lower()
                if len(parts) == 1 and n.endswith(".sha256"):
                    return parts[0].lower()
    return None


def pick_asset(rel: dict, pattern: str) -> dict | None:
    """First asset matching the glob, ignoring case (OptiScaler ships "Optiscaler_<v>.7z", lower-case s)."""
    for a in rel.get("assets", []):
        if fnmatch.fnmatch(a.get("name", "").lower(), pattern.lower()):
            return a
    return None


def download(url: str, dest: str, timeout: float = 60.0) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    req = urllib.request.Request(url, headers={"User-Agent": "ClawDeck", "Accept": "application/octet-stream"})
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with tls.urlopen(req, timeout) as r, os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)
    return dest


def _safe(name: str) -> bool:
    n = name.replace("\\", "/")
    return not (n.startswith("/") or n == ".." or n.startswith("../") or "/../" in n or n.endswith("/.."))


def extract(archive: str, dest: str) -> str:
    if os.path.islink(dest):
        raise ValueError(f"refusing to extract into a symlink: {dest}")
    os.makedirs(dest, exist_ok=True)
    low = archive.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for i in z.infolist():
                if not _safe(i.filename):
                    raise ValueError(f"unsafe path in archive: {i.filename}")
            for i in z.infolist():
                z.extract(i, dest)
                mode = (i.external_attr >> 16) & 0o777
                if mode and not i.is_dir():
                    os.chmod(os.path.join(dest, i.filename), mode)
    elif low.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.zst", ".tar")):
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                # links (symbolic or hard) may only point inside the archive; no device nodes
                if not _safe(m.name) or (m.issym() or m.islnk()) and not _safe(m.linkname) or m.isdev():
                    raise ValueError(f"unsafe member in archive: {m.name}")
            if hasattr(tarfile, "data_filter"):
                t.extractall(dest, filter="data")
            else:
                t.extractall(dest)
    elif low.endswith(".7z"):
        tool = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
        bsdtar = shutil.which("bsdtar")
        if tool:
            listing = subprocess.run([tool, "l", "-slt", archive], capture_output=True, text=True, check=True).stdout
            paths = [line[7:] for line in listing.splitlines() if line.startswith("Path = ")][1:]   # first is the archive
        elif bsdtar:      # libarchive reads 7z too
            paths = subprocess.run([bsdtar, "-tf", archive], capture_output=True, text=True, check=True).stdout.splitlines()
        else:
            raise FileNotFoundError("7z or bsdtar is needed to unpack OptiScaler (sudo rpm-ostree install p7zip)")
        for pth in paths:
            if not _safe(pth):
                raise ValueError(f"unsafe path in archive: {pth}")
        if tool:
            subprocess.run([tool, "x", "-y", f"-o{dest}", archive], capture_output=True, check=True)
        else:
            subprocess.run([bsdtar, "-xf", archive, "-C", dest], capture_output=True, check=True)
    else:
        raise ValueError(f"unknown archive type: {archive}")
    return dest


def fetch(entry: dict, workdir: str, allow_unpinned: bool = False) -> dict:
    """Download and unpack one versions.json entry. Returns {tag, archive, dir}."""
    if not is_pinned(entry) and not allow_unpinned:
        raise PermissionError(f"{entry['repo']} has no tested version pinned yet; allow 'latest (untested)' to continue")
    repo = (entry.get("fork") or entry["repo"]) if entry.get("use_fork") else entry["repo"]
    rel = release(repo, entry.get("tag"))
    asset = pick_asset(rel, entry.get("asset_glob", "*"))
    if asset is None:
        raise FileNotFoundError(f"no asset matching {entry.get('asset_glob')} in {repo} {rel.get('tag_name')}")
    tag = rel.get("tag_name", "unknown")
    archive = download(asset["browser_download_url"], os.path.join(workdir, "downloads", asset["name"]))
    out = extract(archive, os.path.join(workdir, f"{os.path.basename(repo)}-{tag}"))
    return {"tag": tag, "archive": archive, "dir": out, "repo": repo}
