#!/usr/bin/env python3
"""Fail closed when a PLAT dashboard or package contains upstream branding."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path


ASSETS = (
    "favico.svg",
    "apple-touch-icon.png",
    "pwa-192.png",
    "pwa-512.png",
    "logo_horizontal_dark.png",
    "logo_horizontal_white.png",
    "logo_vertical_dark.svg",
    "logo_vertical_white.svg",
)


def check_dashboard(dashboard: Path) -> list[str]:
    errors: list[str] = []
    index = dashboard / "index.html"
    manifest = dashboard / "manifest.json"
    if not index.is_file():
        return [f"missing {index}"]
    if not manifest.is_file():
        return [f"missing {manifest}"]

    index_text = index.read_text(encoding="utf-8")
    manifest_text = manifest.read_text(encoding="utf-8")
    errors.extend(
        f"{index}: missing {marker}"
        for marker in ("<title>PLAT 助手</title>", 'content="PLAT 助手"')
        if marker not in index_text
    )
    errors.extend(
        f"{manifest}: missing {marker}"
        for marker in ('"name": "PLAT 助手"', '"short_name": "PLAT 助手"')
        if marker not in manifest_text
    )
    if "<title>Octop</title>" in index_text or 'content="Octop"' in index_text:
        errors.append(f"{index}: upstream Octop title remains")
    if '"name": "Octop"' in manifest_text or '"short_name": "Octop"' in manifest_text:
        errors.append(f"{manifest}: upstream Octop name remains")
    errors.extend(
        f"{dashboard}: missing brand asset {asset}"
        for asset in ASSETS
        if not (dashboard / asset).is_file()
    )
    js_files = tuple((dashboard / "assets").glob("*.js"))
    if not any("PLAT 助手" in js.read_text(encoding="utf-8", errors="ignore") for js in js_files):
        errors.append(f"{dashboard}: no branded page title found in JavaScript assets")
    return errors


def archive_members(path: Path) -> dict[str, bytes]:
    if path.suffix == ".whl" or path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    if path.name.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(path) as archive:
            members: dict[str, bytes] = {}
            for member in archive.getmembers():
                if member.isfile():
                    stream = archive.extractfile(member)
                    if stream is not None:
                        members[member.name] = stream.read()
            return members
    raise ValueError(f"unsupported artifact type: {path}")


def check_artifact(path: Path) -> list[str]:
    members = archive_members(path)
    indexes = [name for name in members if name.endswith("/dashboard/index.html")]
    if not indexes:
        # The custom octop-memory wheel has no dashboard and is checked separately.
        return []
    errors: list[str] = []
    for index_name in indexes:
        prefix = index_name.removesuffix("index.html")
        index_text = members[index_name].decode("utf-8")
        if "<title>PLAT 助手</title>" not in index_text:
            errors.append(f"{path}:{index_name}: missing PLAT title")
        if 'content="PLAT 助手"' not in index_text:
            errors.append(f"{path}:{index_name}: missing PLAT mobile title")
        manifest = members.get(f"{prefix}manifest.json", b"").decode("utf-8")
        if '"name": "PLAT 助手"' not in manifest or '"short_name": "PLAT 助手"' not in manifest:
            errors.append(f"{path}:{prefix}manifest.json: missing PLAT manifest name")
        js_files = [
            content.decode("utf-8", errors="ignore")
            for name, content in members.items()
            if name.startswith(f"{prefix}assets/") and name.endswith(".js")
        ]
        if not any("PLAT 助手" in content for content in js_files):
            errors.append(f"{path}:{prefix}assets: missing branded page title")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.directory is None and not args.artifact:
        parser.error("provide --directory and/or --artifact")

    errors: list[str] = []
    if args.directory is not None:
        errors.extend(check_dashboard(args.directory / "src/octop/dashboard"))
    for artifact in args.artifact:
        errors.extend(check_artifact(artifact))
    if errors:
        print("\n".join(f"❌ {error}" for error in errors), file=sys.stderr)
        return 1
    print("✓ PLAT brand verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
