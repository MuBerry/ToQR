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
import secrets
import signal
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus

APPNAME = 'fnos-qrcode'
APPVERSION = '1.0.0'
DEFAULT_PORT = 10001
PORT_MIN, PORT_MAX = 1024, 65535
SESSION_COOKIE = 'qr_session'
SESSION_TTL = 7 * 24 * 3600          # 7 天
PBKDF2_ITERATIONS = 200_000
LOGIN_MAX_FAIL = 5
LOGIN_LOCK_SECONDS = 60

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
  .foot{margin-top:18px;text-align:center;font-size:11.5px;color:#c3c8d0}
</style></head>
<body>
  <form class="box" method="post" action="/login">
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
        self.send_header('X-Content-Type-Options', 'nosniff')
        # 一律短连接：请求体可能被截断，长连接会串包
        self.send_header('Connection', 'close')
        self.close_connection = True
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

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
        return parse_session(self.server.secret, self._cookie(SESSION_COOKIE))

    # ---- 路由 ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self._path()
        if path == '/logout':
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
            self._send(HTTPStatus.OK, LOGIN_PAGE.replace('__ERROR__', ''), 'text/html; charset=utf-8',
                       {'Cache-Control': 'no-store'})
            return

        # 以下全部需要（可选的）登录态
        if self._auth_on() and not self._current_user():
            self._redirect('/login')
            return

        self._serve_static(path)

    def do_POST(self):
        path = self._path()
        if path != '/login':
            self._send(HTTPStatus.NOT_FOUND, 'not found', 'text/plain; charset=utf-8')
            return
        if not self._auth_on():
            self._redirect('/')
            return

        length = min(int(self.headers.get('Content-Length') or 0), 64 * 1024)
        raw = self.rfile.read(length).decode('utf-8', 'replace') if length else ''
        form = {}
        for pair in raw.split('&'):
            if '=' in pair:
                key, _, value = pair.partition('=')
                # unquote_plus 同时处理 + 和 %XX
                form[unquote_plus(key)] = unquote_plus(value)

        ip = self.client_address[0]
        locked = self.server.login_guard.blocked(ip)
        if locked > 0:
            self._fail('%d 秒后重试（登录失败次数过多）' % locked)
            return

        auth = self.server.settings['auth']
        username = form.get('username', '')
        password = form.get('password', '')
        ok = (hmac.compare_digest(username, auth['username'])
              and verify_password(password, auth['password']))
        if not ok:
            self.server.login_guard.fail(ip)
            remain = LOGIN_MAX_FAIL - self.server.login_guard.count(ip)
            extra = '，还可尝试 %d 次' % remain if remain > 0 else '，已暂时锁定'
            self._fail('用户名或密码不正确' + extra)
            return

        self.server.login_guard.reset(ip)
        token = make_session(self.server.secret, auth['username'])
        self._redirect('/', {'Set-Cookie': '%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax'
                                          % (SESSION_COOKIE, token, SESSION_TTL)})

    def _fail(self, message):
        page = LOGIN_PAGE.replace('__ERROR__', '<div class="err">%s</div>' % message)
        self._send(HTTPStatus.UNAUTHORIZED, page, 'text/html; charset=utf-8', {'Cache-Control': 'no-store'})

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
    """极简登录失败限流（进程内）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}

    def blocked(self, ip):
        with self._lock:
            count, until = self._state.get(ip, (0, 0))
            remain = int(until - time.time())
            return remain if remain > 0 else 0

    def count(self, ip):
        with self._lock:
            return self._state.get(ip, (0, 0))[0]

    def fail(self, ip):
        with self._lock:
            count, _ = self._state.get(ip, (0, 0))
            count += 1
            until = time.time() + LOGIN_LOCK_SECONDS if count >= LOGIN_MAX_FAIL else 0
            self._state[ip] = (count, until)

    def reset(self, ip):
        with self._lock:
            self._state.pop(ip, None)


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
