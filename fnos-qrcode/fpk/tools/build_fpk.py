#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在没有官方 fnpack 的机器上构建 .fpk 安装包。

FPK 容器结构（依据 fnpack 1.2.3 实测产物，见飞牛应用开放平台文档与社区验证）：
    .fpk  = gzip 压缩的 tar
    外层  = manifest / app.tgz / cmd/ / config/ / wizard/ / ICON.PNG / ICON_256.PNG
    app.tgz = 内层 tar.gz，内容是 app/ 目录下的文件，**不带 app/ 前缀**
    manifest 里的 checksum 字段 = app.tgz 文件字节的 MD5

官方推荐仍然是在飞牛设备上用 fnpack 打包（build-fpk.sh 会自动优先调用）。
本脚本用于在没有 fnOS 环境时也能产出结构一致的包，便于交付与自测。

用法：
    python3 tools/build_fpk.py            # 产出 fnos-qrcode_<version>_<platform>.fpk
    python3 tools/build_fpk.py --list     # 只打印将要打包的文件清单
"""

import argparse
import gzip
import hashlib
import io
import os
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FPK_DIR = os.path.dirname(HERE)
APP_DIR = os.path.join(FPK_DIR, 'app')

# app.tgz 里放的是 app/ 的全部内容；外层放这些
OUTER_DIRS = ('cmd', 'config', 'wizard')
# manifest 不在此列：它要在写入 checksum 后单独加入（且在 app.tgz 之前落位）
OUTER_FILES = ('ICON.PNG', 'ICON_256.PNG', 'LICENSE')

JUNK_NAMES = ('.DS_Store', 'Thumbs.db', '.gitkeep')
JUNK_SUFFIX = ('.pyc', '.pyo', '.swp', '.tmp', '.fpk', '.tgz')


def should_skip(name):
    if name in JUNK_NAMES:
        return True
    if name == '__pycache__':
        return True
    return name.endswith(JUNK_SUFFIX)


def collect_tree(root):
    """返回 [(绝对路径, 包内相对路径, 是否目录)]，已过滤垃圾文件"""
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip(d))
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, '/')
        if rel_dir != '.':
            items.append((dirpath, rel_dir, True))
        for name in sorted(filenames):
            if should_skip(name):
                continue
            full = os.path.join(dirpath, name)
            # tar 内路径一律用正斜杠：Windows 下 os.path.join 会给反斜杠，
            # 装到 Linux 侧会变成一个带反斜杠的文件名而不是目录层级
            rel = name if rel_dir == '.' else rel_dir + '/' + name
            items.append((full, rel, False))
    return items


def add_to_tar(tar, path, arcname, mtime, mode=None):
    info = tar.gettarinfo(path, arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = 'root'
    info.gname = 'root'
    info.mtime = mtime
    if mode is not None:
        info.mode = mode
    elif info.isdir():
        info.mode = 0o755
    else:
        info.mode = 0o644
    # 顶层 cmd/ 脚本需要可执行
    if not info.isdir() and arcname.startswith('cmd/'):
        info.mode = 0o755
    if info.isdir():
        # 目录条目只需头信息，不能当普通文件读取
        tar.addfile(info)
        return
    if os.path.islink(path):
        with open(os.devnull, 'rb') as fh:
            tar.addfile(info, fh)
    else:
        with open(path, 'rb') as fh:
            tar.addfile(info, fh)


def make_targz(build, mtime, compresslevel=9):
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=compresslevel, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            build(tar)
    return raw.getvalue()


def read_manifest():
    with open(os.path.join(FPK_DIR, 'manifest'), 'r', encoding='utf-8') as fh:
        return fh.read()


def parse_manifest(text):
    data = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if '=' in stripped:
            key, _, value = stripped.partition('=')
            data[key.strip()] = value.strip()
    return data


def with_checksum(text, checksum):
    """在 manifest 末尾写入/替换 checksum 字段"""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith('checksum')]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append('checksum              = %s' % checksum)
    return '\n'.join(lines) + '\n'


def build(list_only=False):
    manifest_text = read_manifest()
    meta = parse_manifest(manifest_text)
    appname = meta.get('appname') or 'fnos-qrcode'
    version = meta.get('version') or '1.0.0'
    platform = meta.get('platform') or 'all'
    out_name = '%s_%s_%s.fpk' % (appname, version, platform)
    out_path = os.path.join(FPK_DIR, out_name)

    # 结构自检
    for required in ('manifest', 'ICON.PNG', 'ICON_256.PNG'):
        if not os.path.isfile(os.path.join(FPK_DIR, required)):
            sys.exit('缺少必需文件：%s' % required)
    for required in OUTER_DIRS:
        if not os.path.isdir(os.path.join(FPK_DIR, required)):
            sys.exit('缺少必需目录：%s/' % required)
    ui_dir = os.path.join(APP_DIR, meta.get('desktop_uidir') or 'ui')
    if not os.path.isdir(ui_dir):
        sys.exit('缺少 UI 目录：%s（manifest desktop_uidir 指向它）' % ui_dir)
    if not os.path.isfile(os.path.join(ui_dir, 'index.html')):
        sys.exit('缺少 %s/index.html —— 先执行 build-fpk.sh 同步前端页面' % os.path.relpath(ui_dir, FPK_DIR))
    if not os.path.isfile(os.path.join(APP_DIR, 'server', 'app.py')):
        sys.exit('缺少 app/server/app.py —— 先执行 build-fpk.sh 同步服务端'
                 '（唯一真源是仓库根 server/app.py，不再入库）')

    app_items = collect_tree(APP_DIR)

    if list_only:
        print('== app.tgz 内容 ==')
        for full, rel, isdir in app_items:
            print('  %s%s' % (rel, '/' if isdir else ''))
        print('== 外层内容 ==')
        for name in OUTER_FILES:
            if os.path.exists(os.path.join(FPK_DIR, name)):
                print('  %s' % name)
        for d in OUTER_DIRS:
            for full, rel, isdir in collect_tree(os.path.join(FPK_DIR, d)):
                print('  %s/%s%s' % (d, rel, '/' if isdir else ''))
        print('  app.tgz')
        print('== 产物 ==')
        print('  %s' % out_name)
        return 0

    mtime = int(os.environ.get('SOURCE_DATE_EPOCH') or time.time())

    # ---- 1. 内层 app.tgz ----
    def build_app(tar):
        for full, rel, isdir in app_items:
            add_to_tar(tar, full, rel, mtime)

    app_tgz = make_targz(build_app, mtime)
    checksum = hashlib.md5(app_tgz).hexdigest()

    # ---- 2. 外层 ----
    manifest_final = with_checksum(manifest_text, checksum).encode('utf-8')

    def build_outer(tar):
        for name in OUTER_DIRS:
            for full, rel, isdir in collect_tree(os.path.join(FPK_DIR, name)):
                add_to_tar(tar, full, '%s/%s' % (name, rel), mtime)
        for name in OUTER_FILES:
            path = os.path.join(FPK_DIR, name)
            if os.path.isfile(path):
                add_to_tar(tar, path, name, mtime)

        # manifest（写入 checksum 后的版本）
        info = tarfile.TarInfo('manifest')
        info.size = len(manifest_final)
        info.mode = 0o644
        info.mtime = mtime
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        tar.addfile(info, io.BytesIO(manifest_final))

        # app.tgz
        info = tarfile.TarInfo('app.tgz')
        info.size = len(app_tgz)
        info.mode = 0o644
        info.mtime = mtime
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        tar.addfile(info, io.BytesIO(app_tgz))

    fpk_bytes = make_targz(build_outer, mtime)

    with open(out_path, 'wb') as fh:
        fh.write(fpk_bytes)

    print('构建完成：%s' % out_name)
    print('  app.tgz  %8d bytes  md5=%s' % (len(app_tgz), checksum))
    print('  .fpk     %8d bytes' % len(fpk_bytes))
    print('  路径     %s' % out_path)
    return 0


def main():
    parser = argparse.ArgumentParser(description='构建 fnos-qrcode 的 .fpk 包')
    parser.add_argument('--list', action='store_true', help='只列出将要打包的文件')
    args = parser.parse_args()
    return build(list_only=args.list)


if __name__ == '__main__':
    sys.exit(main())
