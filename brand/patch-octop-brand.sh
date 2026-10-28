#!/usr/bin/env bash
# PLAT 助手白标补丁（非侵入式）：只改 octop 的「构建产物」，octop 仓库源码零改动。
#
# 用法：
#   ./patch-octop-brand.sh [octop仓库路径]     # 默认：脚本所在目录的上一级（本仓库）
#
# 前置：octop 仓库已执行 make build-frontend（存在 src/octop/dashboard/index.html）。
# 每次从上游同步后：git pull → make build-frontend → 重跑本脚本。
#
# 部署到容器（注意：必须先删旧 assets，docker cp 是叠加式会残留旧 hash 文件）：
#   docker exec octop sh -c 'rm -rf /app/src/octop/dashboard/assets /app/src/octop/dashboard/sw.js /app/src/octop/dashboard/workbox-*.js'
#   docker cp <octop仓库>/src/octop/dashboard/. octop:/app/src/octop/dashboard/
#   docker restart octop
#
# ⚠️ 已打开过 Octop 的浏览器会缓存同 URL 的旧 JS 字节（文件名不变、内容变了），
#    部署后需硬刷新一次（Cmd+Shift+R），之后恢复正常。
#
# 改动内容（全部带命中断言，上游文案变化会响亮失败而非静默漏掉）：
#   1. 同名覆盖 9 个品牌资产（favicon/横竖版 logo/PWA 图标），代码引用路径不变
#   2. index.html：<title> 与 apple-mobile-web-app-title
#   3. manifest.json：name / short_name
#   4. JS chunk 里的 zh/en pageTitle 文案（浏览器标签标题的运行时来源）
set -euo pipefail

OCTOP_REPO="${1:-${OCTOP_REPO:-$(cd "$(dirname "$0")/.." && pwd)}}"
BRAND_DIR="$(cd "$(dirname "$0")" && pwd)"
ASSETS="$BRAND_DIR/assets"
DASH="$OCTOP_REPO/src/octop/dashboard"

fail() { echo "❌ $*" >&2; exit 1; }

[[ -f "$DASH/index.html" ]] || fail "$DASH/index.html 不存在，请先在 octop 仓库执行 make build-frontend"
[[ -d "$ASSETS" ]] || fail "缺少资产目录 $ASSETS（先运行 generate_brand_assets.py）"

# 兼容 macOS(BSD sed 需要空备份参数) 与 Linux/GNU sed（Docker 构建容器内）。
sed_inplace() { # file, expr
  if sed --version >/dev/null 2>&1; then
    sed -i "$2" "$1"
  else
    sed -i '' "$2" "$1"
  fi
}

patch_count=0
patch_string() { # file, old, new, desc —— 幂等：已是目标值跳过；未命中源串才失败
  if grep -qF -- "$3" "$1" 2>/dev/null && ! grep -qF -- "$2" "$1" 2>/dev/null; then
    echo "✓ $4（已是目标值，跳过）"
    return 0
  fi
  if ! grep -qF -- "$2" "$1" 2>/dev/null; then
    fail "断言失败：$4 —— 在 $(basename "$1") 中未找到待替换内容。上游可能改了该文案/结构，请更新本脚本。"
  fi
  sed_inplace "$1" "s|$2|$3|g"
  echo "✓ $4（已替换）"
}

echo "==> 1/4 覆盖品牌资产"
for f in favico.svg apple-touch-icon.png pwa-192.png pwa-512.png \
         logo_horizontal_dark.png logo_horizontal_white.png \
         logo_vertical_dark.svg logo_vertical_white.svg; do
  [[ -f "$ASSETS/$f" ]] || fail "资产缺失：$ASSETS/$f"
  cp "$ASSETS/$f" "$DASH/$f"
done
echo "✓ 9 个资产已覆盖（favicon / 横竖版 logo / PWA 图标）"

echo "==> 2/4 index.html 标题"
patch_string "$DASH/index.html" '<title>Octop</title>' '<title>PLAT 助手</title>' '<title>'
patch_string "$DASH/index.html" 'content="Octop"' 'content="PLAT 助手"' 'apple-mobile-web-app-title'

echo "==> 3/4 manifest.json"
patch_string "$DASH/manifest.json" '"name": "Octop"' '"name": "PLAT 助手"' 'manifest name'
patch_string "$DASH/manifest.json" '"short_name": "Octop"' '"short_name": "PLAT 助手"' 'manifest short_name'

echo "==> 4/4 JS chunk 内 pageTitle 文案（zh/en）"
ZH_OLD='Octop - 懂你、帮你、陪你成长的智能伙伴'
EN_OLD='Octop - An intelligent partner that understands you, helps you, and grows with you'
hit=0
for js in "$DASH"/assets/*.js; do
  if grep -qF -- "$ZH_OLD" "$js" 2>/dev/null; then
    sed_inplace "$js" "s|$ZH_OLD|PLAT 助手|"; hit=$((hit + 1))
  fi
  if grep -qF -- "$EN_OLD" "$js" 2>/dev/null; then
    sed_inplace "$js" "s|$EN_OLD|PLAT 助手|"
  fi
done
if [[ "$hit" -lt 1 ]]; then
  # 幂等：可能上一轮已替换（chunk 里已有目标串则视为完成）
  if grep -qlF 'PLAT 助手' "$DASH"/assets/zh.*.js 2>/dev/null; then
    echo "✓ pageTitle（已是目标值，跳过）"
  else
    fail "断言失败：zh pageTitle 文案未在任何 JS chunk 中命中，上游可能改了 locales"
  fi
else
  echo "✓ pageTitle（zh/en）已替换（$hit 个 chunk）"
fi
patch_count=$((patch_count + 1))

grep -qF '<title>PLAT 助手</title>' "$DASH/index.html" \
  || fail "品牌校验失败：index.html 标题不是 PLAT 助手"
grep -qF 'content="PLAT 助手"' "$DASH/index.html" \
  || fail "品牌校验失败：移动端标题不是 PLAT 助手"
grep -qF '"name": "PLAT 助手"' "$DASH/manifest.json" \
  || fail "品牌校验失败：manifest name 不是 PLAT 助手"
grep -qF '"short_name": "PLAT 助手"' "$DASH/manifest.json" \
  || fail "品牌校验失败：manifest short_name 不是 PLAT 助手"
if ! grep -qlF 'PLAT 助手' "$DASH"/assets/*.js 2>/dev/null; then
  fail "品牌校验失败：JS chunk 中没有 PLAT 助手标题"
fi

echo "==> 完成：白标补丁已应用/已验证。octop 仓库源码零改动（仅构建产物 + brand/ 工具目录）。"
