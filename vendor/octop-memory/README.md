# Vendored octop-memory patch

This directory carries a reproducible source distribution of `octop-memory` 1.0.2 with the small openGauss backend and checkpoint changes needed by this worktree.

- Upstream source: PyPI `octop_memory-1.0.2.tar.gz`
- Upstream source SHA-256: `6234797fff4ea340cad2fc51b73f1e4e9931a8680ed448c1fe95d37c1e8ae961`
- Upstream license: MIT; see [LICENSE](LICENSE)
- Patched version: `1.0.2+opengauss.2`
- Rebuild: `uv run python vendor/octop-memory/rebuild.py`

The rebuild script downloads only the hash-verified upstream sdist, applies guarded edits to the backend factory, copies the openGauss implementation and baselines, and writes a deterministic tarball. It fails if the expected upstream factory anchors changed, so an upstream refresh requires reviewing and adapting the patch rather than silently applying it.

Revision 2 also replaces both maintenance-path to_regclass lookups with parameterized schema/relation catalog queries supported by PostgreSQL and openGauss. This covers the background vacuum timer used by the real harness; the patch checks both upstream anchors before rebuilding.

After rebuilding the archive, run `uv lock` to refresh `uv.lock`, then `uv sync --frozen` and the Octop checks. Do not edit the installed copy under `.venv` as a source of truth.

The source checkout references this archive by local path. For wheel delivery, run docker/opengauss/build-dist.sh and ship BOTH the Octop wheel and the patched octop-memory wheel; install both explicit files together. The patched version is not published to public PyPI, so shipping only an Octop wheel is unsupported. See docker/opengauss/README.md for paired-wheel and offline dependency instructions. Publishing a standalone pip-installable release still requires making the patched dependency available on an accessible package index.

The openGauss subclass reuses upstream PostgreSQL methods where their SQL is supported. It overrides every upstream method that emits PostgreSQL `ON CONFLICT` SQL and owns the openGauss schema baseline, lock timeout, and checkpoint saver. The patch does not claim compatibility with arbitrary octop-memory releases or complete PostgreSQL semantic equivalence.
