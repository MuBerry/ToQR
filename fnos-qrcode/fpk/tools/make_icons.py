#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标（构建期使用，运行时不依赖 Pillow）。

产出：
    ICON.PNG                    64x64   （应用中心列表图标）
    ICON_256.PNG                256x256
    app/ui/images/icon_64.png   64x64   （桌面入口 images/icon_{0}.png 用）
    app/ui/images/icon_256.png  256x256

用法：
    python3 tools/make_icons.py
"""

import os
import random

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
FPK_DIR = os.path.dirname(HERE)

BG_TOP = (59, 130, 246)      # #3b82f6
BG_BOTTOM = (37, 99, 235)    # #2563eb
FG = (255, 255, 255)

SUPERSAMPLE = 4              # 先放大绘制再缩小，得到平滑边缘
MODULES = 21                 # 标准 QR 尺寸（版本 1）
QUIET = 2                    # 静默区模块数


def finder_modules():
    """三个定位图案（7x7）的位置集合"""
    cells = set()
    for ox, oy in ((0, 0), (MODULES - 7, 0), (0, MODULES - 7)):
        for y in range(7):
            for x in range(7):
                border = x in (0, 6) or y in (0, 6)
                core = 2 <= x <= 4 and 2 <= y <= 4
                if border or core:
                    cells.add((ox + x, oy + y))
    return cells


def data_modules():
    """数据区：用固定种子生成一批模块，纯粹为了视觉上像二维码"""
    rng = random.Random(20261016)
    occupied = finder_modules()
    # 定位图案四周的一格留白
    for ox, oy in ((0, 0), (MODULES - 7, 0), (0, MODULES - 7)):
        for y in range(-1, 8):
            for x in range(-1, 8):
                occupied.add((ox + x, oy + y))
    # 时序图案
    for i in range(8, MODULES - 8):
        occupied.add((i, 6))
        occupied.add((6, i))

    cells = set()
    for y in range(MODULES):
        for x in range(MODULES):
            if (x, y) in occupied:
                continue
            if rng.random() < 0.46:
                cells.add((x, y))
    return cells


def build(size):
    scale = size * SUPERSAMPLE
    canvas = Image.new('RGB', (scale, scale), BG_BOTTOM)
    draw = ImageDraw.Draw(canvas)

    # 背景：圆角方块 + 竖向渐变
    radius = int(scale * 0.22)
    mask = Image.new('L', (scale, scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, scale - 1, scale - 1], radius=radius, fill=255)
    gradient = Image.new('RGB', (scale, scale))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(scale):
        ratio = y / max(scale - 1, 1)
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ratio) for i in range(3))
        grad_draw.line([(0, y), (scale, y)], fill=color)
    canvas.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(canvas)

    # 前景：白色二维码
    total = MODULES + QUIET * 2
    unit = scale / float(total)
    cells = sorted(finder_modules() | data_modules())
    for (mx, my) in cells:
        x0 = (mx + QUIET) * unit
        y0 = (my + QUIET) * unit
        x1 = x0 + unit
        y1 = y0 + unit
        draw.rectangle([x0, y0, x1 - unit * 0.06, y1 - unit * 0.06], fill=FG)

    return canvas.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(os.path.join(FPK_DIR, 'app', 'ui', 'images'), exist_ok=True)
    targets = [
        (64, os.path.join(FPK_DIR, 'ICON.PNG')),
        (64, os.path.join(FPK_DIR, 'app', 'ui', 'images', 'icon_64.png')),
        (256, os.path.join(FPK_DIR, 'ICON_256.PNG')),
        (256, os.path.join(FPK_DIR, 'app', 'ui', 'images', 'icon_256.png')),
    ]
    for size, path in targets:
        build(size).save(path, 'PNG', optimize=True)
        print('已生成 %-42s %dx%d  %d bytes' % (os.path.relpath(path, FPK_DIR), size, size,
                                                os.path.getsize(path)))


if __name__ == '__main__':
    main()
