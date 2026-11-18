"""Updates a folder unpacked from a release archive to the latest release
on GitHub, when the person agrees to it.

A git checkout updates with git pull instead. The new archive is installed
only when its SHA-256 matches the one GitHub publishes for it, and it
replaces the program files by renames, so a failure part-way puts the old
ones back. Imported without GTK (scripts/check-ui.py)."""
from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "Elliot9/line-desktop"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
# Sent instead of anything about the person, their computer or account.
AGENT = "line-desktop-updater"
TOP = "line-desktop"
ARCHIVE_LIMIT = 256 * 1024 * 1024
UNPACKED_LIMIT = 1024 * 1024 * 1024


class UpdateError(Exception):
    """Its message is shown to the person as it is."""


class Cancelled(Exception):
    pass


@dataclass
class Release:
    version: str
    notes: str
    page: str
    url: str
    size: int
    sha256: str
    sha256_url: str


def version_tuple(text):
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)(?:\.(\d+))?", str(text or "").strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def current_version(root=ROOT):
    try:
        return (Path(root) / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def architecture():
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), "")


def archive_name(arch=None):
    return f"line-desktop-linux-{arch or architecture()}.tar.gz"


def install_kind(root=ROOT):
    """"release" when this folder can update itself; "source" for a git
    checkout, "readonly" when the folder cannot be written and
    "unsupported" on a processor without a release archive."""
    root = Path(root)
    if (root / ".git").exists():
        return "source"
    if not architecture():
        return "unsupported"
    if not os.access(root, os.W_OK):
        return "readonly"
    return "release"


def testing():
    """LINE_APP_UPDATE_URL points the updater at a stand-in for GitHub."""
    return bool(os.environ.get("LINE_APP_UPDATE_URL"))


def fetch(url, timeout, limit):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("GitHub 的回應大小異常。")
    return data


def check(root=ROOT, timeout=10):
    """The latest release when it is newer than this folder's VERSION, or
    None. Raises OSError or ValueError when GitHub cannot be reached."""
    current = version_tuple(current_version(root))
    data = json.loads(fetch(os.environ.get("LINE_APP_UPDATE_URL") or LATEST, timeout, 4 * 1024 * 1024))
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    latest = version_tuple(tag)
    if current is None or latest is None or latest <= current:
        return None
    assets = {asset.get("name"): asset for asset in data.get("assets") or [] if isinstance(asset, dict)}
    asset = assets.get(archive_name())
    if not asset or not asset.get("browser_download_url"):
        return None  # no archive for this processor: nothing to install
    digest = str(asset.get("digest") or "")
    return Release(version=tag.lstrip("vV"), notes=str(data.get("body") or ""), page=str(data.get("html_url") or RELEASES),
                   url=str(asset["browser_download_url"]), size=int(asset.get("size") or 0),
                   sha256=digest[7:].lower() if digest.startswith("sha256:") else "",
                   sha256_url=str((assets.get(archive_name() + ".sha256") or {}).get("browser_download_url") or ""))


def published_sha256(release, timeout):
    if release.sha256:
        return release.sha256
    if not release.sha256_url:
        return ""
    words = fetch(release.sha256_url, timeout, 4096).decode("ascii", "replace").split()
    return words[0].lower() if words else ""


def download(release, directory, progress=None, cancelled=lambda: False, timeout=30):
    """Downloads the archive into directory and checks its SHA-256 against
    the published one; returns its path."""
    if not release.url.startswith("https://") and not testing():
        raise UpdateError("下載位址不安全，已取消更新。")
    expected = published_sha256(release, timeout)
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError("找不到這個版本的 SHA256 驗證碼，已取消更新。")
    target = Path(directory) / archive_name()
    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(release.url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(target, "wb") as stream:
        total = release.size or int(response.headers.get("Content-Length") or 0)
        while True:
            if cancelled():
                raise Cancelled()
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > ARCHIVE_LIMIT:
                raise UpdateError("安裝包大小異常，已取消更新。")
            digest.update(chunk)
            stream.write(chunk)
            if progress:
                progress(received, total)
    if digest.hexdigest() != expected:
        raise UpdateError("下載的安裝包驗證失敗（SHA256 不符），已取消更新。")
    return target


def unpack(archive, directory, version, arch=None):
    """Unpacks the archive after checking every entry stays inside its
    line-desktop folder and is a plain file or folder; returns that folder."""
    arch = arch or architecture()
    broken = UpdateError("安裝包內容異常，已取消更新。")
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != TOP or member.name.startswith("/") or ".." in parts:
                raise broken
            if not (member.isfile() or member.isdir()):
                raise broken
            total += member.size
            if total > UNPACKED_LIMIT:
                raise broken
        if hasattr(tarfile, "data_filter"):
            tar.extractall(directory, members, filter="data")
        else:
            tar.extractall(directory, members)
    tree = Path(directory) / TOP
    core = tree / "bin" / f"line-core-linux-{arch}"
    if not ((tree / "line-app").is_file() and (tree / "desktop" / "__main__.py").is_file() and core.is_file() and os.access(core, os.X_OK)):
        raise broken
    if current_version(tree) != version:
        raise UpdateError("安裝包的版本與發布的版本不符，已取消更新。")
    return tree


def prepare(release, root=ROOT, progress=None, cancelled=lambda: False):
    """Downloads, checks and unpacks the release inside this folder's .build
    (the same file system, so installing is only renames). Returns the
    staging folder and the unpacked line-desktop folder in it."""
    build = Path(root) / ".build"
    build.mkdir(mode=0o700, exist_ok=True)
    for old in build.glob("update.*"):
        shutil.rmtree(old, ignore_errors=True)
    staging = Path(tempfile.mkdtemp(prefix="update.", dir=build))
    try:
        archive = download(release, staging, progress, cancelled)
        if cancelled():
            raise Cancelled()
        tree = unpack(archive, staging / "unpacked", release.version)
        archive.unlink()
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging, tree


def install(tree, root=ROOT):
    """Puts each top-level entry of the unpacked release in place of the
    one in this folder. Anything the release does not contain (.build, the
    person's own files) stays. On failure the old entries are put back."""
    root, tree = Path(root), Path(tree)
    backup = Path(tempfile.mkdtemp(prefix="previous.", dir=tree.parent))
    saved, placed = [], []
    try:
        for entry in sorted(tree.iterdir()):
            current = root / entry.name
            if current.exists() or current.is_symlink():
                current.rename(backup / entry.name)
                saved.append(entry.name)
            entry.rename(current)
            placed.append(entry.name)
    except OSError as error:
        for name in reversed(placed):
            try:
                (root / name).rename(tree / name)
            except OSError:
                pass
        for name in reversed(saved):
            try:
                (backup / name).rename(root / name)
            except OSError:
                pass
        raise UpdateError(f"無法替換程式檔案（{error.strerror or error}），已保留原本的版本。") from error
    shutil.rmtree(backup, ignore_errors=True)


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(r"`([^`]+)`", r"\1", text)


def notes_markup(text, limit=1200):
    """The release notes as Pango markup: headings and **bold** in bold,
    list items with bullets. Stops at the installation section (the
    updater does that part) and leaves out code blocks."""
    lines, fenced, length = [], False, 0
    for raw in str(text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        if heading:
            title = heading.group(2).strip()
            if "安裝" in title or "install" in title.lower():
                break
            if len(heading.group(1)) == 1:
                continue  # the release name; the dialog says the version
            line = "<b>" + inline(title) + "</b>"
        else:
            bullet = re.match(r"\s*[-*]\s+(.*)", line)
            line = "• " + inline(bullet.group(1)) if bullet else inline(line)
        if not line and (not lines or not lines[-1] or lines[-1].startswith("<b>")):
            continue  # no blank line at the top, twice or under a heading
        if length + len(line) > limit:
            lines.append("…")
            break
        lines.append(line)
        length += len(line)
    return "\n".join(lines).strip()
