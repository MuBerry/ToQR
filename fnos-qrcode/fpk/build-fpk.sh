#!/bin/bash
# 构建 fnos-qrcode 的 .fpk 安装包
#
#   bash build-fpk.sh          构建（优先调用官方 fnpack，找不到则用 tools/build_fpk.py）
#   bash build-fpk.sh --list    只列出打包清单，不产出文件
#
# 在飞牛设备上构建时，fnpack 位于 /usr/local/bin/fnpack；
# 没有 fnOS 环境时，脚本会自动回退到同目录下的 Python 打包器。
set -euo pipefail

cd "$(cd "$(dirname "$0")" && pwd)"
FPK_DIR="$PWD"
ROOT_DIR="$(dirname "$FPK_DIR")"

LIST_ONLY=0
[ "${1:-}" = "--list" ] && LIST_ONLY=1

echo "==> 1/4 同步前端页面"
SRC="$ROOT_DIR/../index.html"
if [ ! -f "$SRC" ]; then
  echo "找不到前端源文件：$SRC" >&2
  exit 1
fi
mkdir -p "$FPK_DIR/app/ui"
cp "$SRC" "$FPK_DIR/app/ui/index.html"
echo "    index.html -> app/ui/index.html ($(wc -c <"$FPK_DIR/app/ui/index.html") bytes)"

echo "==> 2/4 整理命令权限"
chmod 755 "$FPK_DIR"/cmd/* 2>/dev/null || true
chmod 644 "$FPK_DIR/manifest" "$FPK_DIR"/config/* "$FPK_DIR"/wizard/* 2>/dev/null || true

echo "==> 3/4 清理构建垃圾"
find "$FPK_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$FPK_DIR" -name '*.pyc' -delete 2>/dev/null || true
find "$FPK_DIR" -name '.DS_Store' -delete 2>/dev/null || true
rm -f "$FPK_DIR"/*.fpk 2>/dev/null || true

if [ "$LIST_ONLY" = "1" ]; then
  echo "==> 4/4 打包清单"
else
  echo "==> 4/4 打包"
fi

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 && { PY="$candidate"; break; }
  done
fi
[ -n "$PY" ] || { echo "未找到 python3，无法构建" >&2; exit 1; }

if command -v fnpack >/dev/null 2>&1 && [ "$LIST_ONLY" = "0" ]; then
  echo "    使用官方 fnpack 打包"
  fnpack build
  echo
  echo "产物："
  ls -1 "$FPK_DIR"/*.fpk 2>/dev/null || true
else
  [ "$LIST_ONLY" = "0" ] && echo "    未检测到 fnpack，使用内置打包器（结构依据 fnpack 1.2.3 实测产物）"
  if [ "$LIST_ONLY" = "1" ]; then
    "$PY" "$FPK_DIR/tools/build_fpk.py" --list
  else
    "$PY" "$FPK_DIR/tools/build_fpk.py"
  fi
fi
