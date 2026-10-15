#!/usr/bin/env bash
# 把仓库根目录的开发主版本同步到本部署包的两份副本
# 用法：bash fnos-qrcode/sync.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/index.html"

cp "$src" "$root/fnos-qrcode/index.html"
cp "$src" "$root/fnos-qrcode/html/fnos-qrcode/index.html"

echo "已同步 index.html ->"
echo "  fnos-qrcode/index.html"
echo "  fnos-qrcode/html/fnos-qrcode/index.html"
