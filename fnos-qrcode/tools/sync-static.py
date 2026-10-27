#!/usr/bin/env python3
"""把 ui/ 下的页面同步成「纯静态部署」副本。

纯静态版没有后端，所以要去掉依赖服务端的东西：
  · 顶栏三个文根入口（/qr、/qr-shop、/qrscan 是服务端路由）→ 整个 nav 去掉
  · 解析器下拉（默认档和草料档都要服务端）→ 去掉控件，引擎固定为「浏览器内离线编码」
  · 页脚文案改为「在浏览器内生成」

用法：python3 fnos-qrcode/tools/sync-static.py
产物：
  fnos-qrcode/index.html                        （部署包根目录，供 nginx 根路径使用）
  fnos-qrcode/html/fnos-qrcode/index.html       （nginx 子路径 /fnos-qrcode/ 使用）
  fnos-qrcode/html/fnos-qrcode/qrscan.html      （扫码页，同样无后端）
  fnos-qrcode/html/fnos-qrcode/vendor/*.js      （页面用到的编码器 / 解码器）
"""
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI = os.path.join(ROOT, 'ui')
OUT_ROOT = os.path.join(ROOT, 'fnos-qrcode')
OUT_HTML = os.path.join(OUT_ROOT, 'html', 'fnos-qrcode')

BANNER = ("<!-- 本文件由 fnos-qrcode/tools/sync-static.py 从 ui/ 生成，请勿手改 -->\n")

NAV_RE = re.compile(r'\n\s*<nav class="root-nav"[\s\S]*?</nav>')
ENGINE_RE = re.compile(r'\n\s*<label class="engine-wrap"[\s\S]*?</label>')


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    print('  写入', os.path.relpath(path, ROOT), len(text.encode('utf-8')), 'B')


def to_static_index(html):
    before = html
    html, n_nav = NAV_RE.subn('', html, count=1)
    assert n_nav == 1, '没找到顶栏 nav，ui/index.html 结构变了？'
    html, n_eng = ENGINE_RE.subn('', html, count=1)
    assert n_eng == 1, '没找到解析器下拉，ui/index.html 结构变了？'

    old_engines = "const ENGINES = ['server', 'offline', 'caoliao'];"
    assert old_engines in html, '引擎列表常量变了'
    html = html.replace(old_engines, "const ENGINES = ['offline'];   // 纯静态只能浏览器内编码", 1)

    old_engine = "let engine = 'server';"
    assert old_engine in html, '引擎默认值变了'
    html = html.replace(old_engine, "let engine = 'offline';", 1)

    # 下拉被移除了，JS 里对它的引用要兜底，否则 addEventListener 会抛错
    old_ref = "const engineSelect = $('engineSelect');"
    assert old_ref in html, '解析器下拉的 JS 引用变了'
    html = html.replace(old_ref, "const engineSelect = $('engineSelect') || document.createElement('select');", 1)

    old_footer = '二维码由本机服务直接编码生成，不联网、不上传 · 内容只留在你自己的设备上'
    if old_footer in html:
        html = html.replace(old_footer, '二维码在浏览器内本地生成，不联网、不上传 · 内容只留在你自己的设备上', 1)

    assert html != before
    return BANNER + html


def to_static_scan(html):
    html, n_nav = NAV_RE.subn('', html, count=1)
    assert n_nav == 1, '没找到扫码页的顶栏 nav'
    return BANNER + html


def main():
    print('==> 生成纯静态页面')
    write(os.path.join(OUT_ROOT, 'index.html'), to_static_index(read(os.path.join(UI, 'index.html'))))
    write(os.path.join(OUT_HTML, 'index.html'), to_static_index(read(os.path.join(UI, 'index.html'))))
    write(os.path.join(OUT_HTML, 'qrscan.html'), to_static_scan(read(os.path.join(UI, 'qrscan.html'))))

    print('==> 复制页面依赖')
    vendor_out = os.path.join(OUT_HTML, 'vendor')
    os.makedirs(vendor_out, exist_ok=True)
    for name in ('toqr-qr.js', 'zxing.min.js'):
        src = os.path.join(UI, 'vendor', name)
        shutil.copy2(src, os.path.join(vendor_out, name))
        print('  复制', os.path.relpath(os.path.join(vendor_out, name), ROOT))

    print('完成。把 fnos-qrcode/html/ 挂到 nginx 的 /usr/share/nginx/html 即可。')


if __name__ == '__main__':
    main()
