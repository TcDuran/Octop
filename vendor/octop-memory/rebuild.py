"""Rebuild the pinned source fork without editing site-packages.

Run: python vendor/octop-memory/rebuild.py
Downloads only the hash-verified official source archive, then applies the
small factory changes and sibling openGauss files. Output is reproducible.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "https://files.pythonhosted.org/packages/9b/e5/c0c9d2fcf6271bcd0711899b0d3cde1a5eceb8b2272e3a5e434ae5b49405/octop_memory-1.0.2.tar.gz"
SHA256 = "6234797fff4ea340cad2fc51b73f1e4e9931a8680ed448c1fe95d37c1e8ae961"
VERSION = "1.0.2+opengauss.2"


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError("Upstream factory anchor changed; review the patch")
    return text.replace(old, new, 1)


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise RuntimeError("Upstream source hash mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target, filter="data")
        tree = target / "octop_memory-1.0.2"
        core = tree / "src/octop_memory/core.py"
        text = core.read_text()
        text = replace_once(
            text,
            "            saver = PostgresSaver(pool)",
            """            from octop_memory.storage.backends.opengauss import OpenGaussMemoryBackend
            if isinstance(backend, OpenGaussMemoryBackend):
                from octop_memory.storage.backends.opengauss_checkpoint import OpenGaussSaver
                saver = OpenGaussSaver(pool)
            else:
                saver = PostgresSaver(pool)""",
        )
        text = replace_once(
            text,
            "                saver.setup()\n                try:",
            """                saver.setup()
                if isinstance(backend, OpenGaussMemoryBackend):
                    return
                try:""",
        )
        text = replace_once(
            text,
            '    if backend_type == "postgres":\n',
            """    if backend_type == "opengauss":
        from octop_memory.storage.backends.opengauss import OpenGaussMemoryBackend
        return OpenGaussMemoryBackend(namespace=namespace, **config)
    if backend_type == "postgres":
""",
        )
        core.write_text(text)
        vacuum = tree / "src/octop_memory/pipeline/lifecycle/vacuum.py"
        text = vacuum.read_text()
        old = '            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))'
        if text.count(old) != 2:
            raise RuntimeError("Upstream vacuum catalog anchors changed; review the patch")
        text = text.replace(
            old,
            """            schema, relation = table.split(".", 1)
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname=%s)",
                (schema, relation),
            )""",
        )
        vacuum.write_text(text)
        project = tree / "pyproject.toml"
        project.write_text(
            replace_once(project.read_text(), 'version = "1.0.2"', f'version = "{VERSION}"')
        )
        (tree / "PKG-INFO").unlink()
        backend = tree / "src/octop_memory/storage/backends"
        for name in (
            "opengauss.py",
            "opengauss_checkpoint.py",
            "memory-baseline.sql",
            "checkpoint-baseline.sql",
        ):
            shutil.copyfile(ROOT / name, backend / name)
        output = ROOT / f"octop_memory-{VERSION}.tar.gz"
        with (
            output.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
            tarfile.open(fileobj=zipped, mode="w") as tar,
        ):
            for path in sorted(tree.rglob("*")):
                if not path.is_file():
                    continue
                info = tar.gettarinfo(
                    str(path),
                    arcname=f"octop_memory-{VERSION}/{path.relative_to(tree).as_posix()}",
                )
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as data:
                    tar.addfile(info, data)
        print(output.name, hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
