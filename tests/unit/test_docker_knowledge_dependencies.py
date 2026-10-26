"""Both Docker sync layers must retain local knowledge dependencies."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_docker_syncs_retain_knowledge_extras() -> None:
    """Docker images retain knowledge extras unless embedding is disabled."""
    repo = Path(__file__).resolve().parents[2]
    dockerfile = (repo / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG INSTALL_LOCAL_EMBEDDING=true" in dockerfile
    assert dockerfile.count('if [ "$INSTALL_LOCAL_EMBEDDING" = "true" ]') == 2
    assert dockerfile.count("--extra browser --extra local-embedding --extra knowledge-ocr") == 2
    without_embedding = dockerfile.replace(" --extra local-embedding", "")
    assert without_embedding.count("--extra browser --extra knowledge-ocr") == 4
    assert "import cv2, fastembed, huggingface_hub, rapidocr, onnxruntime, pymupdf" in dockerfile
    assert "import cv2, rapidocr, onnxruntime, pymupdf" in dockerfile


def test_deepagents_patch_treats_runtime_import_failure_as_optional() -> None:
    """Old CPUs can fail NumPy imports while deepagents builds agent graphs."""
    patch_path = (
        Path(__file__).resolve().parents[2]
        / "docker"
        / "patches"
        / "patch_deepagents_prompt_caching.py"
    )
    spec = importlib.util.spec_from_file_location("patch_deepagents_prompt_caching", patch_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = (
        "try:\n"
        "    module = import_module(module_name)\n"
        "    except ImportError as exc:\n"
        '        if exc.name not in {"langchain_aws", module_name}:\n'
        "            raise\n"
        '        logger.debug("Bedrock prompt caching middleware unavailable.", exc_info=exc)\n'
        "        return None\n"
    ) * 2

    patched = module.patch_source(source)

    assert patched.count("except (ImportError, RuntimeError) as exc:") == 2
    assert "if exc.name not in" not in patched


def test_fnos_source_dockerfile_installs_knowledge_extras() -> None:
    """The manual FnOS source image must bake the same knowledge runtimes.

    Published FnOS Docker packages pull the image built from docker/Dockerfile
    and do not build this file.
    """
    repo = Path(__file__).resolve().parents[2]
    dockerfile = (repo / "fnos" / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert ".[desktop,local-embedding,knowledge-ocr]" in dockerfile
    assert "import cv2, fastembed, huggingface_hub, rapidocr, onnxruntime, pymupdf" in dockerfile
