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

echo "==> 1/5 同步前端页面与服务端"
# 服务端唯一真源在仓库根 server/app.py，打包时复制进来，仓库里不再保留副本
SERVER_SRC="$ROOT_DIR/../server/app.py"
if [ ! -f "$SERVER_SRC" ]; then
  echo "找不到服务端源文件：$SERVER_SRC" >&2
  exit 1
fi
mkdir -p "$FPK_DIR/app/server"
cp "$SERVER_SRC" "$FPK_DIR/app/server/app.py"
echo "    server/app.py -> app/server/app.py ($(wc -c <"$FPK_DIR/app/server/app.py") bytes)"

# 页面源码统一在 ui/index.html（根目录不再放重复副本）
SRC="$ROOT_DIR/../ui/index.html"
if [ ! -f "$SRC" ]; then
  echo "找不到前端源文件：$SRC" >&2
  exit 1
fi
mkdir -p "$FPK_DIR/app/ui"
cp "$SRC" "$FPK_DIR/app/ui/index.html"
echo "    index.html -> app/ui/index.html ($(wc -c <"$FPK_DIR/app/ui/index.html") bytes)"

# 扫码页与内置解码库（/qrscan 文根用）
SCAN_SRC="$ROOT_DIR/../ui/qrscan.html"
VENDOR_SRC="$ROOT_DIR/../ui/vendor"
if [ -f "$SCAN_SRC" ]; then
  cp "$SCAN_SRC" "$FPK_DIR/app/ui/qrscan.html"
  echo "    qrscan.html -> app/ui/qrscan.html ($(wc -c <"$FPK_DIR/app/ui/qrscan.html") bytes)"
fi
if [ -d "$VENDOR_SRC" ]; then
  rm -rf "$FPK_DIR/app/ui/vendor"
  mkdir -p "$FPK_DIR/app/ui/vendor"
  cp "$VENDOR_SRC"/* "$FPK_DIR/app/ui/vendor/" 2>/dev/null || true
  echo "    vendor/ -> app/ui/vendor/ ($(du -sk "$FPK_DIR/app/ui/vendor" | cut -f1) KB)"
fi

echo "==> 2/5 整理命令权限"
chmod 755 "$FPK_DIR"/cmd/* 2>/dev/null || true
chmod 644 "$FPK_DIR/manifest" "$FPK_DIR"/config/* "$FPK_DIR"/wizard/* 2>/dev/null || true

echo "==> 3/5 清理构建垃圾"
find "$FPK_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$FPK_DIR" -name '*.pyc' -delete 2>/dev/null || true
find "$FPK_DIR" -name '.DS_Store' -delete 2>/dev/null || true
rm -f "$FPK_DIR"/*.fpk 2>/dev/null || true

if [ "$LIST_ONLY" = "1" ]; then
  echo "==> 4/5 打包清单"
else
  echo "==> 4/5 打包"
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

[ "$LIST_ONLY" = "1" ] && exit 0

# ---------------------------------------------------------------
# 5/5 归档：产物统一带版本号，放在 fnos-qrcode/ 下
#   fnos-qrcode_<version>.fpk        —— 直接安装 / 上传 Release 用
#   fnos-qrcode_<version>.fpk.zip    —— 论坛等不便传二进制的地方用
#   每次只保留当前版本，历史版本靠 git tag 与 Release 留存
# ---------------------------------------------------------------
echo
echo "==> 5/5 归档产物（带版本号）"
VER="$(sed -n 's/^version[[:space:]]*=[[:space:]]*//p' "$FPK_DIR/manifest" | head -1 | tr -d '\r' | tr -d '[:space:]')"
[ -n "$VER" ] || { echo "无法从 manifest 读取版本号" >&2; exit 1; }

BUILT="$(ls -1 "$FPK_DIR"/fnos-qrcode_${VER}*.fpk 2>/dev/null | head -1)"
if [ -z "$BUILT" ]; then
  echo "没有找到本次构建产物（期望 fnos-qrcode_${VER}*.fpk）" >&2
  exit 1
fi

OUT="$ROOT_DIR/fnos-qrcode_${VER}.fpk"
rm -f "$ROOT_DIR"/fnos-qrcode_*.fpk "$ROOT_DIR"/fnos-qrcode_*.fpk.zip 2>/dev/null || true
rm -f "$ROOT_DIR/fnos-qrcode.fpk" "$ROOT_DIR/fnos-qrcode.fpk.zip" 2>/dev/null || true
cp "$BUILT" "$OUT"
"$PY" - "$OUT" "${OUT}.zip" <<'PYEOF'
import sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
    z.write(src, 'fnos-qrcode.fpk')
PYEOF
rm -f "$BUILT"

echo "    版本      $VER"
echo "    安装包    $OUT ($(wc -c <"$OUT") bytes)"
echo "    md5       $(md5sum "$OUT" | cut -d' ' -f1)"
echo "    压缩包    ${OUT}.zip ($(wc -c <"${OUT}.zip") bytes)"
echo
echo "    Release 正文可直接取 manifest 里 ${VER} 那一段："
echo "    sed -n 's/^changelog[[:space:]]*=[[:space:]]*//p' \"$FPK_DIR/manifest\" | sed 's|<br />|\\n|g' | sed -n '/^${VER}/,/^[0-9]/p'"
