# 注: 原首行 `# syntax=docker/dockerfile:1` 已移除 — 本机 daemon 的阿里云
# 镜像加速器对 docker/dockerfile 返回 403;Docker 内置 BuildKit 前端已支持
# 本文件用到的全部特性(RUN --mount=type=cache 等),无需外部前端镜像。
# =============================================================================
# Octop — 多阶段 Docker 构建
#
# 阶段 1: 构建 React/TypeScript 前端
# 阶段 2: Python 运行时 + 预构建前端
#
# 用法（在仓库根目录执行，构建上下文为根目录）:
#   docker build -f docker/Dockerfile -t octop:latest .
#   docker run -d -p 8088:8088 -v octop-data:/data/.octop -e HOME=/data octop:latest
#
# 国内加速（可选，需 BuildKit，docker/docker_build.sh 默认已开启）:
#   PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
#   PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
#   NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/ \
#   APT_MIRROR=mirrors.cloud.tencent.com \
#   bash docker/docker_build.sh
#
# 或直接传 build-arg:
#   docker build \
#     -f docker/Dockerfile \
#     --build-arg PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
#     --build-arg PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
#     --build-arg NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/ \
#     --build-arg APT_MIRROR=mirrors.cloud.tencent.com \
#     --build-arg NODE_MAX_OLD_SPACE_SIZE=1024 \
#     -t octop:latest .
#
# 或使用 Compose:
#   docker compose -f docker/docker-compose.yml up -d
# =============================================================================

# ---------------------------------------------------------------------------
# 阶段 1 — 前端构建
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-builder

ARG NPM_REGISTRY=
# Node heap ceiling for the vite build. Lower this (e.g. 1024) on low-memory
# build hosts (< 4G RAM) to avoid OOM kills — the value only caps the max,
# it is not pre-allocated.
ARG NODE_MAX_OLD_SPACE_SIZE=2048
WORKDIR /build/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    set -eu; \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm ci --no-audit \
    || { echo "npm ci failed — retrying clean"; rm -rf node_modules; npm ci --no-audit; }; \
    test -x node_modules/.bin/vite

COPY dashboard/ ./

RUN mkdir -p ../src/octop/dashboard

# 镜像构建跳过完整 tsc；生产打包由 vite/esbuild 完成（使用 lockfile 中的 vite，不用 npx）
RUN NODE_ENV=production NODE_OPTIONS="--max-old-space-size=${NODE_MAX_OLD_SPACE_SIZE}" npm run build:docker

# 白标补丁：在镜像内构建产物上应用品牌替换（标题/manifest/PWA 图标/logo）。
# 脚本带命中断言，上游文案变化时构建失败以提示更新 brand/patch-octop-brand.sh。
COPY brand/assets /build/brand/assets
COPY brand/patch-octop-brand.sh /build/brand/patch-octop-brand.sh
RUN bash /build/brand/patch-octop-brand.sh /build


# ---------------------------------------------------------------------------
# 阶段 2 — Python 运行时
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL maintainer="OrcaKit Team"
LABEL org.opencontainers.image.title="Octop"
LABEL org.opencontainers.image.description="Smarter self-hosted AI assistant — multi-user, multi-agent."
LABEL org.opencontainers.image.source="https://github.com/TencentCloud/orca"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/data \
    OCTOP_BIND_HOST=0.0.0.0 \
    OCTOP_PORT=8088 \
    OCTOP_LOG_LEVEL=info \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ARG APT_MIRROR=
ARG INSTALL_LOCAL_EMBEDDING=true

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    if [ -n "$APT_MIRROR" ]; then \
        printf 'Types: deb\nURIs: https://%s/debian\nSuites: trixie trixie-updates\nComponents: main contrib non-free non-free-firmware\n\nTypes: deb\nURIs: https://%s/debian-security\nSuites: trixie-security\nComponents: main contrib non-free non-free-firmware\n' \
            "$APT_MIRROR" "$APT_MIRROR" > /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libgl1 \
        libglib2.0-0 \
        curl \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app

# 先安装锁定依赖，仅改源码时可复用此层缓存
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY vendor/octop-memory/ vendor/octop-memory/
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PIP_INDEX_URL" ]; then \
        export UV_INDEX_URL="$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then export UV_INSECURE_HOST="$PIP_TRUSTED_HOST"; fi; \
    fi \
    && if [ "$INSTALL_LOCAL_EMBEDDING" = "true" ]; then \
        uv sync --frozen --no-install-project --no-dev \
            --extra browser --extra local-embedding --extra knowledge-ocr; \
    else \
        uv sync --frozen --no-install-project --no-dev \
            --extra browser --extra knowledge-ocr; \
    fi

ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY src/ ./src/
COPY --from=frontend-builder /build/src/octop/dashboard/ ./src/octop/dashboard/

COPY .env.example ./
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PIP_INDEX_URL" ]; then \
        export UV_INDEX_URL="$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then export UV_INSECURE_HOST="$PIP_TRUSTED_HOST"; fi; \
    fi \
    && if [ "$INSTALL_LOCAL_EMBEDDING" = "true" ]; then \
        uv sync --frozen --no-dev \
            --extra browser --extra local-embedding --extra knowledge-ocr; \
        .venv/bin/python -c "import cv2, fastembed, huggingface_hub, rapidocr, onnxruntime, pymupdf"; \
    else \
        uv sync --frozen --no-dev \
            --extra browser --extra knowledge-ocr; \
        .venv/bin/python -c "import cv2, rapidocr, onnxruntime, pymupdf"; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/* /tmp/*

# octop-harness 1.0.5 已保留 enum/array 类型；构建期补丁只补齐
# LangChain 重建时丢失的嵌套 object properties，锚点失效时构建失败提示复核。
COPY docker/patches/patch_harness_mcp.py /tmp/patch_harness_mcp.py
COPY docker/patches/patch_deepagents_prompt_caching.py /tmp/patch_deepagents_prompt_caching.py
RUN python /tmp/patch_harness_mcp.py \
    && python /tmp/patch_deepagents_prompt_caching.py \
    && rm -f /tmp/patch_harness_mcp.py /tmp/patch_deepagents_prompt_caching.py

RUN mkdir -p /data/.octop

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["sh", "-c", "curl -f http://localhost:${OCTOP_PORT:-8088}/api/health || exit 1"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD []

# The openGauss compose selects this target; the default image stays unchanged.
FROM 10.24.1.80:2443/library/opengauss:6.0.5 AS opengauss-clients

FROM runtime AS opengauss-runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreadline8t64 libcrypt1 libkeyutils1 libzstd1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=opengauss-clients /usr/local/opengauss/lib/ /opt/opengauss/lib/
COPY --from=opengauss-clients /usr/local/opengauss/bin/gs_dump /usr/local/opengauss/bin/gs_restore /opt/opengauss/bin/
COPY docker/opengauss/gs-client /usr/local/bin/gs-client
RUN chmod +x /usr/local/bin/gs-client \
    && ln -s gs-client /usr/local/bin/gs_dump \
    && ln -s gs-client /usr/local/bin/gs_restore \
    && gs_dump --version && gs_restore --version

FROM runtime AS default
