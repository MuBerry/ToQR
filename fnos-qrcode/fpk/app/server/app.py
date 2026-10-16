#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二维码批量生成器 · 飞牛 fnOS 版服务端

纯 Python3 标准库实现，无第三方依赖。职责：

1. 托管 app/ui/ 下的静态页面（二维码批量生成器前端）
2. 端口可配置（默认 10001），配置来自 settings.json
3. 可选用户认证：开启后需登录（PBKDF2 存密码 + HMAC 签名 Cookie）

命令行：
    python3 app.py                      启动服务（端口读 settings.json）
    python3 app.py --port 8080          指定端口启动（调试用，不写配置）
    python3 app.py --print-port         打印当前端口
    python3 app.py --check              探测当前端口是否已在监听（供 cmd/main status 用）
    python3 app.py --set [选项]          合并写入 settings.json
        --port N            设置端口
        --auth on|off       设置是否开启用户认证
        --user U            设置登录用户名（留空表示不改）
        --pass P            设置登录密码（留空表示不改）

配置文件位置：$TRIM_PKGETC/settings.json（非 fnOS 环境下回退到 <包根>/etc/settings.json）
"""

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlencode
from urllib.request import Request as UrlRequest, urlopen as url_open

APPNAME = 'fnos-qrcode'
APPVERSION = '1.0.4'
DEFAULT_PORT = 10001
PORT_MIN, PORT_MAX = 1024, 65535
SESSION_COOKIE = 'qr_session'
SESSION_TTL = 7 * 24 * 3600          # 7 天
PBKDF2_ITERATIONS = 200_000
LOGIN_MAX_FAIL = 5
LOGIN_LOCK_SECONDS = 120
CSRF_TTL = 30 * 60                   # 登录表单 token 有效期（秒）
MAX_BODY = 64 * 1024                 # POST body 上限
MAX_HEADER_LINE = 8192               # 单行请求头上限（默认 65536 太宽松）
QR_UPSTREAM = 'https://api.2dcode.biz/v1/create-qr-code'
QR_MAX_DATA = 1024                   # 单个二维码内容长度上限
QR_MAX_BYTES = 2 * 1024 * 1024       # 上游图片体积上限
QR_CACHE_MAX = 512                   # 内存里缓存的二维码张数

# 失败计数窗口：**连续**失败之间只要不超过这个秒数就继续累计，超过则计数清零。
# 与 LOGIN_LOCK_SECONDS 合起来就是「120 秒内连续失败 5 次 → 锁定 120 秒」。
LOGIN_FAIL_WINDOW = 120

# 锁定粒度是**来源 IP**，不区分用户名：同一 IP 的失败次数统一累计。
# 默认**不豁免任何来源** —— 回环与私网同样受限流保护，局域网部署下也真实生效。
# 若某个部署确实需要放行特定网段（例如前置反向代理把所有人收敛成一个 IP），
# 把前缀加进这个元组即可，如 ('127.', '::1', '192.168.')；留空表示全部受限。
LOGIN_LOCK_TRUSTED_PREFIXES = ()

# 前端是单文件页面（内联 script/style），并会从 jsDelivr 取 JSZip、
# 从 api.2dcode.biz 取二维码图片，所以必须放行这三处，其余一律 'self'。
CSP = '; '.join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline'",
    # img-src 仍保留第三方域：前端在代理不可用时会回落到直连 <img>
    "img-src 'self' data: blob: https://api.2dcode.biz",
    # connect-src 收紧为 'self'：跨域 fetch 在真实域名下本来就被 CORS 拦，
    # 现在二维码统一走同源 /qr 代理，这里不再需要放行任何第三方
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

HERE = os.path.dirname(os.path.abspath(__file__))     # <app>/server
APP_DIR = os.path.dirname(HERE)                       # <app>
PKG_DIR = os.path.dirname(APP_DIR)                    # <pkg>
SETTINGS_NAME = 'settings.json'


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
def ui_dir():
    """静态资源目录：安装后是 $TRIM_APPDEST/ui，开发态是 <包根>/app/ui"""
    dest = os.environ.get('TRIM_APPDEST')
    if dest:
        candidate = os.path.join(dest, 'ui')
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(APP_DIR, 'ui')


def etc_dir():
    """持久配置目录：$TRIM_PKGETC，回退 <包根>/etc"""
    d = os.environ.get('TRIM_PKGETC')
    return d if d else os.path.join(PKG_DIR, 'etc')


def var_dir():
    """运行时目录：$TRIM_PKGVAR，回退 <包根>/var"""
    d = os.environ.get('TRIM_PKGVAR')
    return d if d else os.path.join(PKG_DIR, 'var')


def settings_path():
    return os.path.join(etc_dir(), SETTINGS_NAME)


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def default_settings():
    return {'port': DEFAULT_PORT, 'auth': {'enabled': False, 'username': 'admin', 'password': None}}


def load_settings():
    path = settings_path()
    data = {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    base = default_settings()
    base['port'] = normalize_port(data.get('port'), fallback=DEFAULT_PORT)

    auth = data.get('auth') if isinstance(data.get('auth'), dict) else {}
    base['auth'] = {
        'enabled': bool(auth.get('enabled', False)),
        'username': str(auth.get('username') or 'admin'),
        'password': auth.get('password') if isinstance(auth.get('password'), dict) else None,
    }
    return base


def save_settings(settings):
    path = settings_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def normalize_port(value, fallback=DEFAULT_PORT):
    """把任意输入收敛成合法端口；非法时返回 fallback"""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return port if PORT_MIN <= port <= PORT_MAX else fallback


# --------------------------------------------------------------------------- #
# 密码与会话
# --------------------------------------------------------------------------- #
def hash_password(raw, iterations=PBKDF2_ITERATIONS, salt=None):
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac('sha256', raw.encode('utf-8'), salt, iterations)
    return {'algo': 'pbkdf2_sha256', 'iterations': iterations,
            'salt': salt.hex(), 'hash': derived.hex()}


def verify_password(raw, record):
    if not isinstance(record, dict):
        return False
    try:
        salt = bytes.fromhex(str(record.get('salt', '')))
        expected = str(record.get('hash', ''))
        iterations = int(record.get('iterations') or PBKDF2_ITERATIONS)
    except (TypeError, ValueError):
        return False
    if not salt or not expected:
        return False
    derived = hashlib.pbkdf2_hmac('sha256', raw.encode('utf-8'), salt, iterations)
    return hmac.compare_digest(derived.hex(), expected)


def session_secret(settings):
    """会话签名密钥。

    由「密码哈希 + 应用名」派生，不额外落盘：这样服务进程完全不需要写配置文件，
    避免安装脚本（可能以 root 运行）与运行用户（package）权限不一致导致写失败。
    副作用是改密码会让已登录会话失效，这符合预期。
    """
    record = settings['auth'].get('password')
    if isinstance(record, dict) and record.get('hash'):
        seed = '%s|%s|%s' % (record.get('hash', ''), record.get('salt', ''), APPNAME)
    else:
        # 未开启认证时不会被用到，给个进程内随机值即可
        seed = secrets.token_hex(16)
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()


def make_session(secret, username):
    payload = json.dumps({'u': username, 'exp': int(time.time()) + SESSION_TTL},
                         separators=(',', ':')).encode('utf-8')
    body = base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')
    sig = hmac.new(secret.encode('utf-8'), body.encode('ascii'), hashlib.sha256).hexdigest()[:32]
    return body + '.' + sig


def parse_session(secret, token):
    if not token:
        return None
    try:
        body, sig = token.rsplit('.', 1)
        expect = hmac.new(secret.encode('utf-8'), body.encode('ascii'), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        padded = body + '=' * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
        if int(data.get('exp', 0)) < time.time():
            return None
        return str(data.get('u') or '') or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 登录页
# --------------------------------------------------------------------------- #
LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 · 二维码批量生成器</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#f9fafb;color:#1f2937;padding:24px;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",Arial,sans-serif}
  .box{width:100%;max-width:360px;background:#fff;border:1px solid #eef0f2;border-radius:10px;
       box-shadow:0 1px 3px rgba(16,24,40,.05);padding:28px 26px}
  .logo{display:flex;align-items:center;gap:9px;justify-content:center;margin-bottom:6px}
  h1{font-size:16px;font-weight:700}
  .sub{text-align:center;font-size:12.5px;color:#9ca3af;margin-bottom:22px}
  label{display:block;font-size:12.5px;color:#4b5563;margin:0 0 6px}
  input{width:100%;height:38px;border:1px solid #d1d5db;border-radius:6px;padding:0 11px;
        font-size:13.5px;font-family:inherit;color:#374151;outline:none;background:#fff;
        transition:border-color .15s,box-shadow .15s}
  input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12)}
  .field{margin-bottom:14px}
  button{width:100%;height:38px;margin-top:4px;border:none;border-radius:6px;background:#3b82f6;
         color:#fff;font-size:14px;font-family:inherit;font-weight:500;cursor:pointer;
         transition:background-color .15s}
  button:hover{background:#2563eb}
  .err{margin-top:14px;padding:9px 11px;border-radius:6px;background:#fef2f2;border:1px solid #fecaca;
       color:#dc2626;font-size:12.5px;line-height:1.6}
  .err.lock{background:#fffbeb;border-color:#fde68a;color:#b45309}
  .err.lock b{font-weight:600;font-variant-numeric:tabular-nums}
  button:disabled{cursor:not-allowed;opacity:.6}
  button:disabled:hover{background:#3b82f6}
  .foot{margin-top:18px;text-align:center;font-size:11.5px;color:#c3c8d0}
</style></head>
<body>
  <form class="box" method="post" action="/login">
    __CSRF__
    <div class="logo">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="2.5" y="2.5" width="8" height="8" rx="1.4" fill="#1f2937"/>
        <rect x="4.8" y="4.8" width="3.4" height="3.4" rx=".5" fill="#fff"/>
        <rect x="13.5" y="2.5" width="8" height="8" rx="1.4" fill="#3b82f6"/>
        <rect x="15.8" y="4.8" width="3.4" height="3.4" rx=".5" fill="#fff"/>
        <rect x="2.5" y="13.5" width="8" height="8" rx="1.4" fill="#3b82f6"/>
        <rect x="4.8" y="15.8" width="3.4" height="3.4" rx=".5" fill="#fff"/>
        <rect x="13.5" y="13.5" width="3.4" height="3.4" rx=".6" fill="#1f2937"/>
        <rect x="18.1" y="13.5" width="3.4" height="3.4" rx=".6" fill="#1f2937"/>
        <rect x="13.5" y="18.1" width="3.4" height="3.4" rx=".6" fill="#1f2937"/>
        <rect x="18.1" y="18.1" width="3.4" height="3.4" rx=".6" fill="#3b82f6"/>
      </svg>
      <h1>二维码批量生成器</h1>
    </div>
    <p class="sub">此应用已开启用户认证，请登录后使用</p>
    <div class="field">
      <label for="u">用户名</label>
      <input id="u" name="username" autocomplete="username" autofocus required>
    </div>
    <div class="field">
      <label for="p">密码</label>
      <input id="p" name="password" type="password" autocomplete="current-password" required>
    </div>
    <button type="submit">登录</button>
    __ERROR__
    <p class="foot">fnOS 应用 · fnos-qrcode</p>
  </form>
</body></html>
"""

# 锁定提示：占位符 __SEC__ 会被替换成剩余秒数。
# 页面内脚本按秒递减显示，归零后隐藏提示并恢复登录按钮，服务端无需再次渲染。
LOGIN_LOCK_NOTICE = """
    <div class="err lock" id="lock-box">登录失败次数过多，请 <b id="lock-cd" data-sec="__SEC__">__SEC__</b> 秒后重试</div>
    <script>
    (function () {
      var box = document.getElementById('lock-box');
      if (!box) { return; }
      var cd = document.getElementById('lock-cd');
      var btn = document.querySelector('button[type="submit"]');
      var sec = parseInt(cd.getAttribute('data-sec'), 10) || 0;
      function paint() {
        if (!btn) { return; }
        btn.disabled = sec > 0;
        btn.style.cursor = sec > 0 ? 'not-allowed' : '';
      }
      document.addEventListener('submit', function (e) {
        if (sec > 0) { e.preventDefault(); }
      }, true);
      paint();
      (function tick() {
        if (sec <= 0) { box.style.display = 'none'; paint(); return; }
        cd.textContent = sec;
        sec -= 1;
        window.setTimeout(tick, 1000);
      })();
    })();
    </script>
"""

LOGOUT_BUTTON = (
    '<a href="/logout" style="position:fixed;right:14px;bottom:14px;z-index:99;'
    "font:12px/1.4 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#6b7280;"
    'background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:7px 11px;'
    'text-decoration:none;box-shadow:0 1px 3px rgba(16,24,40,.07)">退出登录</a></body>'
)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = 'fnos-qrcode/' + APPVERSION
    protocol_version = 'HTTP/1.1'

    # ---- 基础工具 ----
    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

    def _settings(self):
        return self.server.settings

    def _auth_on(self):
        return bool(self.server.settings['auth']['enabled'])

    def _send(self, status, body=b'', ctype='text/html; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        # ---- 安全响应头（报告 F-04）----
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', CSP)
        # 一律短连接：请求体可能被截断，长连接会串包
        self.send_header('Connection', 'close')
        self.close_connection = True
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    # ---- 请求层防护（报告 R2-01 / R2-02 / R2-03）----
    def handle_one_request(self):
        """拒绝 HTTP/0.9 简写请求；请求行过长给明确的 414 而不是静默断连。

        其余逻辑与 BaseHTTPRequestHandler.handle_one_request 保持一致。
        """
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ''
                self.request_version = ''
                self.command = ''
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                # parse_request 已回过错误响应
                return
            if self.request_version not in ('HTTP/1.0', 'HTTP/1.1'):
                # 无版本号的 HTTP/0.9 简写请求（报告 R2-02）
                self.send_error(HTTPStatus.HTTP_VERSION_NOT_SUPPORTED,
                                'HTTP Version Not Supported')
                self.close_connection = True
                return
            name = 'do_' + self.command
            if not hasattr(self, name):
                self.send_error(HTTPStatus.NOT_IMPLEMENTED,
                                'Unsupported method (%r)' % self.command)
                return
            getattr(self, name)()
            self.wfile.flush()
        except (TimeoutError, socket.timeout) as err:
            self.log_error('Request timed out: %r', err)
            self.close_connection = True

    def _oversized_header(self):
        """逐行检查请求头长度：超限直接 431，避免被静默丢弃（报告 R2-01）。"""
        for line in str(self.headers).splitlines():
            if len(line) > MAX_HEADER_LINE:
                return True
        return False

    def _bad_host(self):
        """Host 必须是 host[:port] 形态（IPv6 带方括号）。缺失或含异常字符一律 400。"""
        host = self.headers.get('Host')
        if not host:
            return True
        allowed = set('abcdefghijklmnopqrstuvwxyz'
                      'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:[]_')
        return not set(host) <= allowed or len(host) > 255

    def _guard_request(self):
        """返回 True 表示请求已（带着错误响应）被拒。"""
        if self._oversized_header():
            self._send(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                       'request header too large', 'text/plain; charset=utf-8')
            return True
        if self._bad_host():
            self._send(HTTPStatus.BAD_REQUEST, 'bad host', 'text/plain; charset=utf-8')
            return True
        return False

    def _read_form(self):
        """按上限读取并解析 POST 表单；超限返回 None（调用方回 413）。"""
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            length = -1
        if length < 0 or length > MAX_BODY:
            return None
        raw = self.rfile.read(length).decode('utf-8', 'replace') if length else ''
        form = {}
        for pair in raw.split('&'):
            if '=' in pair:
                key, _, value = pair.partition('=')
                # unquote_plus 同时处理 + 和 %XX
                form[unquote_plus(key)] = unquote_plus(value)
        return form

    def _redirect(self, location, extra=None):
        headers = {'Location': location, 'Cache-Control': 'no-store'}
        headers.update(extra or {})
        self._send(HTTPStatus.FOUND, b'', 'text/plain; charset=utf-8', headers)

    def _cookie(self, name):
        raw = self.headers.get('Cookie') or ''
        for item in raw.split(';'):
            if '=' in item:
                key, _, value = item.partition('=')
                if key.strip() == name:
                    return value.strip()
        return ''

    def _current_user(self):
        if not self._auth_on():
            return 'anonymous'
        token = self._cookie(SESSION_COOKIE)
        if not token or self.server.session_store.revoked(token):
            return None
        return parse_session(self.server.secret, token)

    # ---- 路由 ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self._guard_request():
            return
        path = self._path()
        if path == '/logout':
            self.server.session_store.revoke(self._cookie(SESSION_COOKIE))
            self._redirect('/login' if self._auth_on() else '/',
                           {'Set-Cookie': '%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax' % SESSION_COOKIE})
            return
        if path == '/login':
            if not self._auth_on():
                self._redirect('/')
                return
            if self._current_user():
                self._redirect('/')
                return
            # 锁定按来源 IP 记账，刷新登录页时可直接取到该来源的剩余锁定时间。
            locked = self.server.login_guard.blocked(self.client_address[0])
            block = LOGIN_LOCK_NOTICE.replace('__SEC__', str(locked)) if locked > 0 else ''
            self._render_login(block, HTTPStatus.OK)
            return

        # 以下全部需要（可选的）登录态
        if self._auth_on() and not self._current_user():
            self._redirect('/login')
            return

        if path == '/qr':
            self._serve_qr()
            return

        self._serve_static(path)

    def _serve_qr(self):
        """同源二维码代理：/qr?data=...&size=400x400"""
        raw_query = self.path.split('?', 1)[1] if '?' in self.path else ''
        query = parse_qs(raw_query, keep_blank_values=True)
        data = (query.get('data') or [''])[0]
        size = (query.get('size') or [''])[0]
        if not self.server.qr_proxy.valid(data, size):
            self._send(HTTPStatus.BAD_REQUEST, 'bad qr request', 'text/plain; charset=utf-8')
            return
        try:
            ctype, blob = self.server.qr_proxy.get(data, size)
        except Exception as err:                      # noqa: BLE001 —— 上游任何异常都回 502
            sys.stderr.write('[qr] upstream failed: %r\n' % (err,))
            self._send(HTTPStatus.BAD_GATEWAY, 'upstream error', 'text/plain; charset=utf-8')
            return
        self._send(HTTPStatus.OK, blob, ctype, {'Cache-Control': 'public, max-age=86400'})

    def do_POST(self):
        if self._guard_request():
            return
        path = self._path()
        if path != '/login':
            self._send(HTTPStatus.NOT_FOUND, 'not found', 'text/plain; charset=utf-8')
            return
        if not self._auth_on():
            self._redirect('/')
            return

        form = self._read_form()
        if form is None:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       'request body too large', 'text/plain; charset=utf-8')
            return

        # 一次性 CSRF 令牌（报告 A-03）：跨站页面拿不到，提交后立即作废
        if not self.server.csrf_guard.consume(form.get('csrf_token', '')):
            self._fail('表单已过期，请重新输入', HTTPStatus.FORBIDDEN)
            return

        ip = self.client_address[0]
        guard = self.server.login_guard
        auth = self.server.settings['auth']
        username = form.get('username', '')
        password = form.get('password', '')

        locked = guard.blocked(ip)
        if locked > 0:
            self._fail_locked(locked)
            return

        ok = (hmac.compare_digest(username, auth['username'])
              and verify_password(password, auth['password']))
        if not ok:
            guard.fail(ip)
            locked = guard.blocked(ip)
            if locked > 0:
                self._fail_locked(locked)
            else:
                # 不再透露「还剩几次」（报告 A-02）
                self._fail('用户名或密码不正确')
            return

        guard.reset(ip)
        token = make_session(self.server.secret, auth['username'])
        self._redirect('/', {'Set-Cookie': '%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax'
                                          % (SESSION_COOKIE, token, SESSION_TTL)})

    def _render_login(self, block, status):
        page = (LOGIN_PAGE
                .replace('__CSRF__', '<input type="hidden" name="csrf_token" value="%s">'
                         % self.server.csrf_guard.issue())
                .replace('__ERROR__', block))
        self._send(status, page, 'text/html; charset=utf-8', {'Cache-Control': 'no-store'})

    def _fail(self, message, status=HTTPStatus.UNAUTHORIZED):
        self._render_login('<div class="err">%s</div>' % message, status)

    def _fail_locked(self, seconds):
        self._render_login(LOGIN_LOCK_NOTICE.replace('__SEC__', str(seconds)),
                           HTTPStatus.TOO_MANY_REQUESTS)

    def _path(self):
        return unquote_plus(self.path.split('?', 1)[0])

    # ---- 静态资源 ----
    def _serve_static(self, path):
        root = self.server.ui_root
        if path == '/favicon.ico':
            self._send_file(os.path.join(root, 'images', 'icon_64.png'))
            return
        rel = 'index.html' if path in ('/', '') else path.lstrip('/')
        target = os.path.realpath(os.path.join(root, rel))
        if not (target == os.path.realpath(root) or target.startswith(os.path.realpath(root) + os.sep)):
            self._send(HTTPStatus.FORBIDDEN, 'forbidden', 'text/plain; charset=utf-8')
            return
        if os.path.isdir(target):
            target = os.path.join(target, 'index.html')
        if not os.path.isfile(target):
            self._send(HTTPStatus.NOT_FOUND, 'not found', 'text/plain; charset=utf-8')
            return
        self._send_file(target)

    def _send_file(self, target):
        ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
            ctype += '; charset=utf-8'
        try:
            with open(target, 'rb') as fh:
                body = fh.read()
        except OSError:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, 'read error', 'text/plain; charset=utf-8')
            return

        if target.endswith('.html') and self._auth_on():
            text = body.decode('utf-8', 'replace')
            if '</body>' in text:
                body = text.replace('</body>', LOGOUT_BUTTON, 1).encode('utf-8')

        self._send(HTTPStatus.OK, body, ctype, {'Cache-Control': 'no-store, must-revalidate'})


class LoginGuard(object):
    """登录失败限流（进程内，按**来源 IP** 记账）

    规则：同一来源 IP 在 `LOGIN_FAIL_WINDOW` 秒内**连续**失败
    `LOGIN_MAX_FAIL` 次 → 锁定该 IP `LOGIN_LOCK_SECONDS` 秒。

    - 计数键就是来源 IP，**不区分用户名**：同一 IP 换个用户名再试并不会"洗掉"计数；
    - 锁定期内的再次提交只累加计数，**不会延长**锁定窗口
      （否则「等一会儿再试」等于给自己续命，倒计时看起来永远是同一个数）；
    - 「连续」的判定：两次失败之间只要超过 `LOGIN_FAIL_WINDOW` 秒没有新失败，
      计数就清零重来，避免隔几天累计 5 次被误锁；
    - 锁定到期后计数与锁定一并清零，重新给满额机会；
    - 默认**不豁免任何来源**（含回环与私网）。需要放行特定网段时，
      往 `LOGIN_LOCK_TRUSTED_PREFIXES` 里加前缀即可。

    状态结构：`_state[ip] = [失败次数, 最近一次失败时间戳, 锁定截止时间戳]`
    （`锁定截止时间戳` 为 0 表示未锁定）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}

    @staticmethod
    def trusted(ip):
        """来源是否在放行名单里。名单默认为空 → 任何来源都受限流保护。"""
        return bool(ip) and ip.startswith(LOGIN_LOCK_TRUSTED_PREFIXES)

    def info(self, ip):
        """返回 (剩余锁定秒数, 当前连续失败次数)。

        锁定已到期、或失败计数已超出连续窗口时，顺带把该来源整条清除。
        """
        with self._lock:
            rec = self._state.get(ip)
            if not rec:
                return 0, 0
            count, last, until = rec
            now = time.time()
            if until and now >= until:                      # 锁定到期
                self._state.pop(ip, None)
                return 0, 0
            if not until and last and now - last > LOGIN_FAIL_WINDOW:   # 连续窗口断裂
                self._state.pop(ip, None)
                return 0, 0
            remain = int(until - now + 0.5) if until else 0
            return (remain if remain > 0 else 0), count

    def blocked(self, ip):
        """该来源剩余锁定秒数（0 表示未锁定）。"""
        return self.info(ip)[0]

    def fail(self, ip):
        """记一次失败，必要时进入锁定。"""
        with self._lock:
            now = time.time()
            rec = self._state.get(ip)
            count, last, until = rec if rec else (0, 0, 0)

            if until and now < until:
                # 已在锁定期：只累加计数，保持原锁定窗口不变
                self._state[ip] = [count + 1, last, until]
                return

            if until and now >= until:      # 上一轮锁定已到期 → 重新开始
                count, last, until = 0, 0, 0
            if last and now - last > LOGIN_FAIL_WINDOW:
                count, last = 0, 0          # 连续窗口断裂 → 计数清零

            count += 1
            last = now
            if count >= LOGIN_MAX_FAIL and not self.trusted(ip):
                until = now + LOGIN_LOCK_SECONDS

            self._state[ip] = [count, last, until]

            if len(self._state) > 1024:     # 轻量清理，避免长期累积
                stale = [k for k, v in self._state.items()
                         if (v[2] and now >= v[2])
                         or (not v[2] and (not v[1] or now - v[1] > LOGIN_FAIL_WINDOW))]
                for k in stale:
                    self._state.pop(k, None)

    def reset(self, ip):
        with self._lock:
            self._state.pop(ip, None)


class QrProxy(object):
    """二维码同源代理

    第三方接口只对 localhost / 127.0.0.1 回 `Access-Control-Allow-Origin`，
    从局域网 IP 或域名访问时浏览器的 fetch 必然被 CORS 拦掉——「打包下载 zip」因此
    在真实部署上必定降级（实测：NAS 上 zip 下载超时，落到 window.open 兜底）。
    前端改为请求同源的 /qr，由服务端转发，同时把二维码收进 CSP 的 'self'。

    安全要点：上游主机写死在 QR_UPSTREAM（不可由请求指定，杜绝 SSRF）；
    data / size 严格校验后用 urlencode 拼装，杜绝参数注入；响应限体积、限类型。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}

    @staticmethod
    def valid(data, size):
        if not data or len(data) > QR_MAX_DATA:
            return False
        if not re.fullmatch(r'\d{1,4}x\d{1,4}', size or ''):
            return False
        return max(int(v) for v in size.split('x')) <= 1024

    def get(self, data, size):
        """返回 (content_type, bytes)；失败抛异常。"""
        key = (data, size)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit

        params = urlencode({'data': data, 'size': size})
        req = UrlRequest(QR_UPSTREAM + '?' + params,
                         headers={'User-Agent': '%s/%s' % (APPNAME, APPVERSION)})
        with url_open(req, timeout=15) as resp:
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if resp.status != 200 or not ctype.startswith('image/'):
                raise ValueError('upstream %s %s' % (resp.status, ctype or '-'))
            blob = resp.read(QR_MAX_BYTES + 1)
        if not blob or len(blob) > QR_MAX_BYTES:
            raise ValueError('upstream size %d' % len(blob))

        with self._lock:
            if len(self._cache) >= QR_CACHE_MAX:
                self._cache.clear()
            self._cache[key] = (ctype, blob)
        return (ctype, blob)


class SessionStore(object):
    """登出后作废对应会话（进程内吊销表）

    会话本身仍是 HMAC 签名的无状态 Cookie，不写盘；这里只额外记住「已登出的 token」，
    让 logout 之后复制走的 Cookie 也立即失效（报告 §5 的待测项：登出是否真正失效）。
    已知取舍：吊销表在进程内存里，重启应用即清空；改密码会让所有会话失效（密钥派生自密码哈希）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._revoked = {}

    def revoke(self, token):
        if not token:
            return
        now = time.time()
        with self._lock:
            self._revoked[token] = now + SESSION_TTL
            if len(self._revoked) > 256:
                for stale in [k for k, exp in self._revoked.items() if now >= exp]:
                    self._revoked.pop(stale, None)

    def revoked(self, token):
        if not token:
            return True
        now = time.time()
        with self._lock:
            exp = self._revoked.get(token)
            if exp is None:
                return False
            if now >= exp:
                self._revoked.pop(token, None)
                return False
            return True


class CsrfGuard(object):
    """登录表单的一次性令牌（服务端保存，不依赖预认证 Cookie）

    GET /login 时签发一个随机 token 放进隐藏字段；提交时校验并**立即作废**。
    跨站页面拿不到 token，因此无法借受害者的浏览器驱动登录表单
    （修的是报告里的 A-03；也顺带切断了 A-01 那条「借浏览器刷失败把别人锁死」的路径）。
    """

    def __init__(self, ttl=CSRF_TTL):
        self._lock = threading.Lock()
        self._ttl = ttl
        self._tokens = {}

    def issue(self):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            if len(self._tokens) > 512:
                for stale in [k for k, exp in self._tokens.items() if now >= exp]:
                    self._tokens.pop(stale, None)
            self._tokens[token] = now + self._ttl
        return token

    def consume(self, token):
        """校验并作废；通过返回 True。"""
        if not token:
            return False
        now = time.time()
        with self._lock:
            exp = self._tokens.pop(token, None)
        return bool(exp) and now < exp


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def build_server(port, settings, secret):
    httpd = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    httpd.daemon_threads = True
    httpd.settings = settings
    httpd.secret = secret
    httpd.ui_root = ui_dir()
    httpd.login_guard = LoginGuard()
    httpd.csrf_guard = CsrfGuard()
    httpd.session_store = SessionStore()
    httpd.qr_proxy = QrProxy()
    return httpd


def port_in_use(port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.5)
    try:
        return probe.connect_ex(('127.0.0.1', port)) == 0
    finally:
        probe.close()


def cmd_serve(port_override):
    settings = load_settings()
    port = port_override or settings['port']
    secret = session_secret(settings)

    httpd = build_server(port, settings, secret)

    def stop(signum, frame):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):
            pass

    auth_state = '开启' if settings['auth']['enabled'] else '关闭'
    sys.stderr.write('[fnos-qrcode] listening on 0.0.0.0:%d  ui=%s  auth=%s\n'
                     % (port, httpd.ui_root, auth_state))
    sys.stderr.flush()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def cmd_set(args):
    settings = load_settings()
    changed = []

    if args.port is not None:
        port = normalize_port(args.port, fallback=None) if str(args.port).strip() else None
        if port is None:
            sys.stderr.write('错误：端口必须是 %d~%d 之间的整数，收到 %r\n' % (PORT_MIN, PORT_MAX, args.port))
            return 1
        if port != settings['port']:
            settings['port'] = port
            changed.append('port=%d' % port)

    if args.auth is not None:
        enabled = str(args.auth).strip().lower() in ('on', 'true', '1', 'yes', 'enable', 'enabled')
        if enabled != settings['auth']['enabled']:
            settings['auth']['enabled'] = enabled
            changed.append('auth=%s' % ('on' if enabled else 'off'))

    if args.user:
        user = str(args.user).strip()
        if user and user != settings['auth']['username']:
            settings['auth']['username'] = user
            changed.append('user=%s' % user)

    if args.password:
        settings['auth']['password'] = hash_password(str(args.password))
        changed.append('password=已更新')
    elif args.pass_stdin:
        # 从标准输入读密码，避免密码出现在进程命令行（ps 可见）
        raw = sys.stdin.read().strip()
        if raw:
            settings['auth']['password'] = hash_password(raw)
            changed.append('password=已更新')

    # 开启认证但没有可用密码时拒绝写入，避免把应用锁死或放空
    if settings['auth']['enabled']:
        if not settings['auth']['username']:
            sys.stderr.write('错误：开启用户认证时必须设置用户名\n')
            return 1
        if not isinstance(settings['auth']['password'], dict):
            sys.stderr.write('错误：开启用户认证时必须设置登录密码（至少 6 位）\n')
            return 1

    if not changed:
        sys.stderr.write('配置无变化\n')

    save_settings(settings)
    sys.stderr.write('[fnos-qrcode] settings written: %s (%s)\n'
                     % (settings_path(), ', '.join(changed) if changed else 'no change'))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(add_help=True, description='fnos-qrcode server')
    parser.add_argument('--port', type=int, default=None, help='以指定端口启动（不写入配置）')
    parser.add_argument('--print-port', action='store_true', help='打印当前配置端口')
    parser.add_argument('--check', action='store_true', help='探测当前端口是否在监听')
    parser.add_argument('--set', action='store_true', help='写入配置')
    parser.add_argument('--auth', default=None, help='on / off')
    parser.add_argument('--user', default='', help='登录用户名')
    parser.add_argument('--pass', dest='password', default='', help='登录密码')
    parser.add_argument('--pass-stdin', dest='pass_stdin', action='store_true',
                        help='从标准输入读取登录密码（供生命周期脚本使用，避免密码出现在 ps 里）')
    args = parser.parse_args(argv)

    if args.print_port:
        sys.stdout.write('%d\n' % load_settings()['port'])
        return 0

    if args.check:
        port = load_settings()['port']
        if port_in_use(port):
            sys.stdout.write('running on port %d\n' % port)
            return 0
        sys.stderr.write('port %d not listening\n' % port)
        return 3

    if args.set:
        return cmd_set(args)

    return cmd_serve(args.port)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
