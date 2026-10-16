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
import ipaddress
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
APPVERSION = '1.1.0'
DEFAULT_PORT = 10001
PORT_MIN, PORT_MAX = 1024, 65535
SESSION_COOKIE = 'qr_session'
SESSION_TTL = 7 * 24 * 3600          # 7 天
PBKDF2_ITERATIONS = 200_000
LOGIN_MAX_FAIL = 5                   # 失败次数默认值（可在设置里改）
LOGIN_LOCK_SECONDS = 120             # 锁定时长默认值（可在设置里改）
DEFAULT_MAX_DEVICES = 3              # 每个账号默认允许同时在线的设备数
MAX_ACCOUNTS = 10                    # 账号数量上限（设置页 / 向导都受此约束）
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
    return {
        'port': DEFAULT_PORT,
        'auth': {'enabled': False},
        'accounts': [],
        'login_security': {
            'max_fail': LOGIN_MAX_FAIL,
            'lock_seconds': LOGIN_LOCK_SECONDS,
            'device_limit_enabled': True,
            'default_max_devices': DEFAULT_MAX_DEVICES,
            'ip_filter_enabled': False,
            'ip_blacklist': [],
            'ip_whitelist': [],
        },
    }


def normalize_int(value, fallback, low, high):
    """把任意输入收敛成 [low, high] 内的整数；非法时返回 fallback。"""
    try:
        num = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return num if low <= num <= high else fallback


def normalize_account(raw):
    """把一条账号记录收敛成规范结构；不合法返回 None。"""
    if not isinstance(raw, dict):
        return None
    user = str(raw.get('user') or '').strip()
    if not user or len(user) > 32:
        return None
    return {
        'user': user,
        'password': raw.get('password') if isinstance(raw.get('password'), dict) else None,
        'max_devices': normalize_int(raw.get('max_devices'), DEFAULT_MAX_DEVICES, 1, 999),
        'unlimited_devices': bool(raw.get('unlimited_devices', False)),
    }


def normalize_accounts(raw):
    out, seen = [], set()
    if isinstance(raw, list):
        for item in raw:
            acc = normalize_account(item)
            if acc and acc['user'] not in seen:
                seen.add(acc['user'])
                out.append(acc)
            if len(out) >= MAX_ACCOUNTS:
                break
    return out


def normalize_ip_list(raw):
    """把 'IP, CIDR; IP' 这类输入收敛成去重列表（最多 64 条）。"""
    if isinstance(raw, str):
        raw = re.split(r'[\s,;]+', raw)
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out[:64]


def escape_html(text):
    """把用户可控字符串安全地放进 HTML（用户名、消息文案等都要过一遍）。"""
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;'))


def ip_matches(ip, entries):
    """ip 是否命中 entries 中的某一条；支持单个 IP 与 CIDR 网段（192.168.1.0/24）。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for item in entries or []:
        text = str(item).strip()
        if not text:
            continue
        try:
            if '/' in text:
                if addr in ipaddress.ip_network(text, strict=False):
                    return True
            elif addr == ipaddress.ip_address(text):
                return True
        except ValueError:
            continue
    return False


def is_loopback(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def _valid_cidr(text):
    """校验单个 IP 或 CIDR 网段是否合法。"""
    try:
        if '/' in str(text):
            ipaddress.ip_network(str(text), strict=False)
        else:
            ipaddress.ip_address(str(text))
        return True
    except ValueError:
        return False


def coerce_settings(data):
    """把任意（可能不完整、或是旧版结构）的配置补齐成完整结构。

    `load_settings()` 与 `build_server()` 都走这里，因此：
    - 手工写了一半的 settings.json 不会让服务起不来；
    - v1.0.4 及更早的 `auth.username / auth.password` 单账号配置会自动迁移成 accounts[0]。
    """
    base = default_settings()
    if not isinstance(data, dict):
        return base

    base['port'] = normalize_port(data.get('port'), fallback=DEFAULT_PORT)

    auth = data.get('auth') if isinstance(data.get('auth'), dict) else {}
    base['auth'] = {'enabled': bool(auth.get('enabled', False))}

    accounts = normalize_accounts(data.get('accounts'))
    if not accounts:
        # 兼容 v1.0.4 及更早的「单账号」配置
        legacy_user = str(auth.get('username') or '').strip()
        legacy_pw = auth.get('password') if isinstance(auth.get('password'), dict) else None
        if legacy_user and legacy_pw:
            accounts = [{'user': legacy_user, 'password': legacy_pw,
                         'max_devices': DEFAULT_MAX_DEVICES,
                         'unlimited_devices': False}]
    base['accounts'] = accounts

    sec = data.get('login_security') if isinstance(data.get('login_security'), dict) else {}
    base['login_security'] = {
        'max_fail': normalize_int(sec.get('max_fail'), LOGIN_MAX_FAIL, 1, 999),
        'lock_seconds': normalize_int(sec.get('lock_seconds'), LOGIN_LOCK_SECONDS, 5, 86400),
        'device_limit_enabled': bool(sec.get('device_limit_enabled', True)),
        'default_max_devices': normalize_int(sec.get('default_max_devices'),
                                             DEFAULT_MAX_DEVICES, 1, 999),
        'ip_filter_enabled': bool(sec.get('ip_filter_enabled', False)),
        'ip_blacklist': normalize_ip_list(sec.get('ip_blacklist')),
        'ip_whitelist': normalize_ip_list(sec.get('ip_whitelist')),
    }

    key = data.get('session_key')
    if isinstance(key, str) and len(key) >= 32:
        base['session_key'] = key
    return base


def load_settings():
    path = settings_path()
    data = {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return coerce_settings(data)


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
    # 同上：compare_digest 只接受纯 ASCII 的 str，统一按 bytes 比较更稳
    return hmac.compare_digest(derived.hex().encode('ascii'), expected.encode('utf-8'))


def session_secret(settings):
    """会话签名密钥。

    用配置文件里的 `session_key`（首次调用时生成并落盘）。这样：

    - **增删账号、改密码、重启应用都不会把已登录设备踢下线** —— 否则设备数量
      限制会被"配置一改就全员掉线"抵消掉；
    - 需要强制所有人重新登录时，把 settings.json 里的 `session_key` 删掉再重启即可；
    - 落盘失败（例如配置文件只读）时退回"每次启动随机"，此时重启会让会话失效。
    """
    key = settings.get('session_key')
    if not isinstance(key, str) or len(key) < 32:
        key = secrets.token_hex(32)
        settings['session_key'] = key
    return hashlib.sha256((key + '|' + APPNAME).encode('utf-8')).hexdigest()


def find_account(settings, username):
    """按用户名精确匹配账号；找不到返回 None。"""
    for acc in settings.get('accounts') or []:
        if hmac.compare_digest(str(acc.get('user', '')).encode('utf-8'),
                               str(username).encode('utf-8')):
            return acc
    return None


def verify_login(settings, username, password):
    """遍历账号校验凭据，命中则返回该账号，否则 None。

    用户名不匹配时也会**跑一次 PBKDF2**（拿首个账号的哈希当诱饵），
    避免从响应时间上判断「某个用户名是否存在」。
    最多只做一次哈希运算，不会因为账号变多而拖慢登录。
    """
    accounts = settings.get('accounts') or []
    matched = find_account(settings, username)
    if matched is not None:
        return matched if verify_password(password, matched.get('password')) else None
    decoy = accounts[0].get('password') if accounts else None
    verify_password(password, decoy)
    return None


def make_session(secret, username):
    """签发会话令牌。

    payload 里带一个随机 `n`：否则同一账号在同一秒内登录两次会生成**完全相同的
    token**，导致 ① 设备登记表被覆盖、设备数统计失效；② 登出一台设备把另一台
    也一起登出。
    """
    payload = json.dumps({'u': username, 'exp': int(time.time()) + SESSION_TTL,
                          'n': secrets.token_hex(8)},
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
    '<a href="/settings" style="position:fixed;right:96px;bottom:14px;z-index:99;'
    "font:12px/1.4 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#6b7280;"
    'background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:7px 11px;'
    'text-decoration:none;box-shadow:0 1px 3px rgba(16,24,40,.07)">设置</a>'
    '<a href="/logout" style="position:fixed;right:14px;bottom:14px;z-index:99;'
    "font:12px/1.4 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#6b7280;"
    'background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:7px 11px;'
    'text-decoration:none;box-shadow:0 1px 3px rgba(16,24,40,.07)">退出登录</a></body>'
)


# --------------------------------------------------------------------------- #
# 设置页（需登录）
# --------------------------------------------------------------------------- #
SETTINGS_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>设置 · 二维码批量生成器</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#f9fafb;color:#1f2937;padding:26px 18px 60px;
       font:13.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif}
  .wrap{max-width:920px;margin:0 auto}
  header{display:flex;align-items:baseline;gap:12px;margin-bottom:18px}
  h1{font-size:19px}
  header a{font-size:13px;color:#3b82f6;text-decoration:none}
  header a:hover{text-decoration:underline}
  h2{font-size:15px;margin:26px 0 8px;padding-bottom:7px;border-bottom:1px solid #e9ecef}
  p.hint{color:#6b7280;font-size:12.5px;margin:0 0 10px}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e9ecef;border-radius:9px;overflow:hidden}
  th{background:#f3f5f7;text-align:left;font-weight:500;font-size:12.5px;color:#4b5563;padding:8px 11px;border-bottom:1px solid #e9ecef}
  td{padding:10px 11px;border-bottom:1px solid #eef0f2;vertical-align:middle}
  tr:last-child td{border-bottom:none}
  .u{font-weight:500}
  .dev{color:#9ca3af;font-size:11.5px;font-weight:400}
  form{display:flex;flex-wrap:wrap;gap:9px;align-items:center}
  form.inline{gap:8px;margin:0}
  input[type=text],input[type=password],input[type=number]{
    height:34px;border:1px solid #d7dbe0;border-radius:6px;padding:0 10px;font-size:13px;
    font-family:inherit;color:#374151;outline:none;background:#fff}
  input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12)}
  input.num{width:74px}
  input.pw{width:170px}
  input.wide{flex:1;min-width:260px}
  label.cb{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;color:#4b5563;white-space:nowrap}
  button{height:34px;border:none;border-radius:6px;background:#3b82f6;color:#fff;
         font-size:13px;font-family:inherit;padding:0 15px;cursor:pointer}
  button:hover{background:#2563eb}
  button.mini{background:#fff;color:#374151;border:1px solid #d7dbe0;padding:0 12px}
  button.mini:hover{background:#f3f5f7}
  button.danger{background:#fff;color:#c02626;border:1px solid #f0c4c4}
  button.danger:hover{background:#fdecec}
  .box{background:#fff;border:1px solid #e9ecef;border-radius:9px;padding:14px 15px;margin-bottom:12px}
  .box form{margin-top:10px}
  .flash{padding:10px 13px;border-radius:8px;font-size:13px;margin-bottom:16px}
  .flash.ok{background:#e8f6ee;border:1px solid #bfe3ce;color:#0f7b46}
  .flash.err{background:#fdecec;border:1px solid #f3c9c9;color:#c02626}
  .empty{color:#9ca3af;font-size:12.5px;padding:6px 0}
  .foot{margin-top:34px;color:#9ca3af;font-size:11.5px}
</style></head>
<body><div class="wrap">
  <header>
    <h1>设置</h1>
    <a href="/">← 返回首页</a>
    <a href="/logout" style="margin-left:auto">退出登录（__USER__）</a>
  </header>
  {{FLASH}}

  <h2>账号（最多 __MAXACCOUNTS__ 个）</h2>
  <p class="hint">「设备上限」是同一账号可同时在线的设备数；勾选「不限设备」则该账号不受限制。
     在线设备数统计的是当前进程内的会话，应用重启后重新累计。</p>
  <table>
    <thead><tr><th style="width:190px">账号</th><th>设备上限 / 新密码</th><th style="width:96px">删除</th></tr></thead>
    <tbody>{{ACCOUNTS}}</tbody>
  </table>

  <div class="box">
    <strong>新增账号</strong>
    <form method="post" action="/settings">
      {{CSRF_ADD}}
      <input type="hidden" name="action" value="account_add">
      <input type="text" name="user" placeholder="用户名（1~32 位字母数字 _ . -）" required>
      <input type="password" name="pass" placeholder="密码（至少 6 位）" required>
      <input type="number" name="max_devices" class="num" min="1" max="999" value="__DEFDEV__">
      <label class="cb"><input type="checkbox" name="unlimited" value="1"> 不限设备</label>
      <button type="submit">新增</button>
    </form>
  </div>

  <h2>登录安全</h2>
  <div class="box">
    <form method="post" action="/settings">
      {{CSRF_SEC}}
      <input type="hidden" name="action" value="security">
      <label class="cb">连续失败次数 <input type="number" name="max_fail" class="num" min="1" max="999" value="__MAXFAIL__"></label>
      <label class="cb">锁定时长（秒） <input type="number" name="lock_seconds" class="num" min="5" max="86400" value="__LOCKSEC__"></label>
      <label class="cb"><input type="checkbox" name="device_limit_enabled" value="1" __DEVON__> 启用「每账号最多 N 台设备」</label>
      <label class="cb">默认上限 <input type="number" name="default_max_devices" class="num" min="1" max="999" value="__DEFDEV__"></label>
      <button type="submit">保存</button>
    </form>
    <p class="hint" style="margin-top:9px">锁定粒度是**来源 IP**：同一 IP 在 120 秒内连续失败达到设定次数即锁定该 IP；
       达到设备上限时**拒绝新登录**，可在应用中心「设置」里临时调高上限或用下方「踢出」释放名额。</p>
  </div>

  <h2>IP 名单（只作用于登录提交）</h2>
  <div class="box">
    <form method="post" action="/settings">
      {{CSRF_IP}}
      <input type="hidden" name="action" value="ipfilter">
      <label class="cb"><input type="checkbox" name="enabled" value="1" __IPON__> 启用 IP 名单</label>
      <div style="flex-basis:100%"></div>
      <label class="cb">黑名单（命中即拒绝）</label>
      <input type="text" name="blacklist" class="wide" value="__BLACK__" placeholder="192.168.1.50, 10.0.0.0/8">
      <div style="flex-basis:100%"></div>
      <label class="cb">白名单（非空时仅这些来源可登录）</label>
      <input type="text" name="whitelist" class="wide" value="__WHITE__" placeholder="192.168.1.0/24">
      <button type="submit">保存</button>
    </form>
    <p class="hint" style="margin-top:9px">支持单个 IP 与网段（如 <code>192.168.1.0/24</code> 或 <code>10.0.0.0/8</code>），
       多条用逗号分隔。页面浏览不受影响，只在提交登录时判断；**回环地址始终放行**，避免把自己关在外面。</p>
  </div>

  <h2>当前在线设备（账号：__USER__）</h2>
  <table>
    <thead><tr><th>登录时间</th><th>剩余有效期</th><th style="width:110px">操作</th></tr></thead>
    <tbody>{{DEVICES}}</tbody>
  </table>
  <div class="box" style="margin-top:12px">
    <form method="post" action="/settings">
      {{CSRF_KICK}}
      <input type="hidden" name="action" value="revoke_others">
      <input type="hidden" name="user" value="__USERRAW__">
      <button type="submit" class="mini">退出本账号的其他全部设备</button>
    </form>
  </div>

  <p class="foot">fnOS 应用 · fnos-qrcode · 配置文件 $TRIM_PKGETC/settings.json</p>
</div></body></html>
"""


def _fmt_time(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return '-'


def _fmt_left(ts):
    try:
        left = int(float(ts) - time.time())
    except (TypeError, ValueError):
        return '-'
    if left <= 0:
        return '已过期'
    return '%d 天 %d 小时' % (left // 86400, (left % 86400) // 3600)


def render_settings_page(server, user, flash=None, flash_kind='ok', current_token=''):
    """服务端渲染设置页。每个表单各自领一个一次性 CSRF 令牌。"""
    settings = server.settings
    sec = settings['login_security']
    accounts = settings.get('accounts') or []
    issue = server.csrf_guard.issue

    rows = []
    for acc in accounts:
        raw_user = acc['user']
        safe_user = escape_html(raw_user)
        online = server.session_store.count(raw_user)
        rows.append(
            '<tr>'
            '<td class="u">%s<div class="dev">在线 %d 台</div></td>'
            '<td><form method="post" action="/settings">'
            '<input type="hidden" name="csrf_token" value="%s">'
            '<input type="hidden" name="action" value="account_update">'
            '<input type="hidden" name="user" value="%s">'
            '<input type="number" name="max_devices" class="num" min="1" max="999" value="%d">'
            '<label class="cb"><input type="checkbox" name="unlimited" value="1"%s>不限设备</label>'
            '<input type="password" name="pass" class="pw" placeholder="新密码（留空不改）">'
            '<button type="submit" class="mini">保存</button>'
            '</form></td>'
            '<td><form method="post" action="/settings">'
            '<input type="hidden" name="csrf_token" value="%s">'
            '<input type="hidden" name="action" value="account_delete">'
            '<input type="hidden" name="user" value="%s">'
            '<button type="submit" class="danger">删除</button>'
            '</form></td>'
            '</tr>'
            % (safe_user, online, issue(), escape_html(raw_user),
               int(acc.get('max_devices') or DEFAULT_MAX_DEVICES),
               ' checked' if acc.get('unlimited_devices') else '',
               issue(), escape_html(raw_user)))

    devices = server.session_store.devices(user)
    current = current_token if isinstance(current_token, str) else ''
    drows = []
    for i, dev in enumerate(devices):
        is_me = (dev['token'] == current)
        drows.append(
            '<tr><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (_fmt_time(dev['issued']), _fmt_left(dev['exp']),
               ('<span style="color:#9ca3af;font-size:12px">当前设备</span>' if is_me else
                '<form method="post" action="/settings">'
                '<input type="hidden" name="csrf_token" value="%s">'
                '<input type="hidden" name="action" value="revoke_device">'
                '<input type="hidden" name="token" value="%s">'
                '<input type="hidden" name="user" value="%s">'
                '<button type="submit" class="mini">踢出</button></form>'
                % (issue(), escape_html(dev['token']), escape_html(user))))
        )

    flash_html = ''
    if flash:
        flash_html = '<div class="flash %s">%s</div>' % (
            'err' if flash_kind == 'err' else 'ok', escape_html(flash))

    page = (SETTINGS_PAGE
            .replace('{{FLASH}}', flash_html)
            .replace('{{ACCOUNTS}}', ''.join(rows) or
                     '<tr><td colspan="3" class="empty">还没有账号。请先新增一个，否则登录页无法登录。</td></tr>')
            .replace('{{DEVICES}}', ''.join(drows) or
                     '<tr><td colspan="3" class="empty">没有记录到在线设备。</td></tr>')
            .replace('{{CSRF_ADD}}', '<input type="hidden" name="csrf_token" value="%s">' % issue())
            .replace('{{CSRF_SEC}}', '<input type="hidden" name="csrf_token" value="%s">' % issue())
            .replace('{{CSRF_IP}}', '<input type="hidden" name="csrf_token" value="%s">' % issue())
            .replace('{{CSRF_KICK}}', '<input type="hidden" name="csrf_token" value="%s">' % issue())
            .replace('__MAXACCOUNTS__', str(MAX_ACCOUNTS))
            .replace('__USERRAW__', escape_html(user))
            .replace('__USER__', escape_html(user))
            .replace('__MAXFAIL__', str(sec['max_fail']))
            .replace('__LOCKSEC__', str(sec['lock_seconds']))
            .replace('__DEFDEV__', str(sec['default_max_devices']))
            .replace('__DEVON__', ' checked' if sec['device_limit_enabled'] else '')
            .replace('__IPON__', ' checked' if sec['ip_filter_enabled'] else '')
            .replace('__BLACK__', escape_html(', '.join(sec['ip_blacklist'])))
            .replace('__WHITE__', escape_html(', '.join(sec['ip_whitelist']))))
    return page


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = 'fnos-qrcode/' + APPVERSION
    protocol_version = 'HTTP/1.1'

    def version_string(self):
        """只回应用名与版本号，不拼 `sys_version`。

        标准库默认返回 `server_version + ' ' + sys_version`，而 sys_version 就是
        `Python/3.x.y`，只覆盖 server_version 根本盖不住（渗透测试 F-21）。
        """
        return self.server_version

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
            try:
                getattr(self, name)()
            except Exception as err:      # noqa: BLE001
                # 兜底：任何未预期的异常都必须变成一个有内容的 500 响应，
                # 绝不能让连接被静默关闭（那在浏览器里就是 ERR_EMPTY_RESPONSE，
                # 用户看不到任何原因，排查成本极高）。
                self.log_error('handler error: %r', err)
                try:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                               'internal error', 'text/plain; charset=utf-8')
                except Exception:
                    self.close_connection = True
                return
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

    def _client_ip(self):
        """请求来源 IP：TCP 对端地址。

        刻意**不读** X-Forwarded-For / X-Real-IP —— 那两个头客户端可以任意伪造，
        一旦信任就等于把 IP 限流与 IP 名单交给攻击者。反代场景请自行在代理层限流。
        """
        return self.client_address[0]

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

        if path == '/settings':
            self._serve_settings()
            return

        if path == '/qr':
            self._serve_qr()
            return

        self._serve_static(path)

    def _serve_settings(self):
        """设置页：读取 ?msg= &kind= 作为一次性提示（PRG 模式，避免刷新重复提交）。"""
        if not self._auth_on():
            self._redirect('/')          # 没开认证时没有账号可管，直接回首页
            return
        user = self._current_user()
        if not user:
            self._redirect('/login')
            return
        query = {}
        if '?' in self.path:
            query = parse_qs(self.path.split('?', 1)[1], keep_blank_values=True)
        msg = (query.get('msg') or [''])[0]
        kind = (query.get('kind') or ['ok'])[0]
        page = render_settings_page(self.server, user, msg, kind,
                                    self._cookie(SESSION_COOKIE))
        self._send(HTTPStatus.OK, page, 'text/html; charset=utf-8',
                   {'Cache-Control': 'no-store'})

    def _redirect_settings(self, msg, kind='ok'):
        self._redirect('/settings?' + urlencode({'msg': msg, 'kind': kind}))

    def _do_settings_post(self):
        if not self._auth_on():
            self._redirect('/')
            return
        user = self._current_user()
        if not user:
            self._redirect('/login')
            return

        form = self._read_form()
        if form is None:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       'request body too large', 'text/plain; charset=utf-8')
            return
        if not self.server.csrf_guard.consume(form.get('csrf_token', '')):
            self._redirect_settings('表单已过期，请刷新页面后重试', 'err')
            return

        settings = self.server.settings
        sec = settings['login_security']
        action = form.get('action', '')
        msg, kind = '已保存', 'ok'

        def text(name):
            return str(form.get(name, '')).strip()

        try:
            if action == 'account_add':
                new_user = text('user')
                if not re.fullmatch(r'[A-Za-z0-9_.\-]{1,32}', new_user):
                    raise ValueError('用户名需为 1~32 位字母、数字或 _ . -')
                if find_account(settings, new_user):
                    raise ValueError('账号「%s」已存在' % new_user)
                if len(settings['accounts']) >= MAX_ACCOUNTS:
                    raise ValueError('账号数量已达上限 %d 个' % MAX_ACCOUNTS)
                raw_pass = str(form.get('pass', ''))
                if len(raw_pass) < 6:
                    raise ValueError('密码至少 6 位')
                settings['accounts'].append({
                    'user': new_user,
                    'password': hash_password(raw_pass),
                    'max_devices': normalize_int(form.get('max_devices'),
                                                 sec['default_max_devices'], 1, 999),
                    'unlimited_devices': bool(form.get('unlimited')),
                })
                msg = '已新增账号「%s」' % new_user

            elif action == 'account_update':
                acc = find_account(settings, text('user'))
                if not acc:
                    raise ValueError('账号不存在')
                acc['max_devices'] = normalize_int(form.get('max_devices'),
                                                   sec['default_max_devices'], 1, 999)
                acc['unlimited_devices'] = bool(form.get('unlimited'))
                raw_pass = str(form.get('pass', ''))
                if raw_pass:
                    if len(raw_pass) < 6:
                        raise ValueError('密码至少 6 位')
                    acc['password'] = hash_password(raw_pass)
                    msg = '已更新账号「%s」并重置密码' % acc['user']
                else:
                    msg = '已更新账号「%s」' % acc['user']

            elif action == 'account_delete':
                target = text('user')
                if target == user:
                    raise ValueError('不能删除当前登录的账号')
                before = len(settings['accounts'])
                settings['accounts'] = [a for a in settings['accounts']
                                        if a['user'] != target]
                if len(settings['accounts']) == before:
                    raise ValueError('账号不存在')
                msg = '已删除账号「%s」' % target

            elif action == 'security':
                sec['max_fail'] = normalize_int(form.get('max_fail'), LOGIN_MAX_FAIL, 1, 999)
                sec['lock_seconds'] = normalize_int(form.get('lock_seconds'),
                                                    LOGIN_LOCK_SECONDS, 5, 86400)
                sec['device_limit_enabled'] = bool(form.get('device_limit_enabled'))
                sec['default_max_devices'] = normalize_int(form.get('default_max_devices'),
                                                           DEFAULT_MAX_DEVICES, 1, 999)
                msg = '已保存登录安全设置'

            elif action == 'ipfilter':
                black = normalize_ip_list(text('blacklist'))
                white = normalize_ip_list(text('whitelist'))
                bad = [x for x in black + white if not _valid_cidr(x)]
                if bad:
                    raise ValueError('IP/网段格式不正确：%s' % ', '.join(bad))
                sec['ip_filter_enabled'] = bool(form.get('enabled'))
                sec['ip_blacklist'] = black
                sec['ip_whitelist'] = white
                msg = '已保存 IP 名单'

            elif action == 'revoke_device':
                token = text('token')
                if token and token != self._cookie(SESSION_COOKIE):
                    self.server.session_store.revoke(token)
                    msg = '已踢出该设备'
                else:
                    raise ValueError('不能踢出当前设备')

            elif action == 'revoke_others':
                keep = self._cookie(SESSION_COOKIE)
                n = 0
                for dev in self.server.session_store.devices(text('user') or user):
                    if dev['token'] != keep:
                        self.server.session_store.revoke(dev['token'])
                        n += 1
                msg = '已退出 %d 台其他设备' % n

            else:
                raise ValueError('未知操作')
        except ValueError as err:
            msg, kind = str(err), 'err'
        else:
            try:
                save_settings(settings)
            except OSError as err:
                msg, kind = '保存失败（配置文件不可写）：%r' % (err,), 'err'

        self._redirect_settings(msg, kind)

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
        if path == '/login':
            self._do_login_post()
            return
        if path == '/settings':
            self._do_settings_post()
            return
        self._send(HTTPStatus.NOT_FOUND, 'not found', 'text/plain; charset=utf-8')

    # ---- 登录 ----
    def _do_login_post(self):
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

        ip = self._client_ip()

        # IP 黑白名单：只作用于「提交登录」这一步，页面本身仍可浏览
        if not self._ip_allowed(ip):
            self._fail('该来源已被限制登录', HTTPStatus.FORBIDDEN)
            return

        guard = self.server.login_guard
        username = form.get('username', '')
        password = form.get('password', '')

        locked = guard.blocked(ip)
        if locked > 0:
            self._fail_locked(locked)
            return

        # 遍历账号做定时安全校验。注意 verify_login 内部已把两侧都 encode 成
        # UTF-8 bytes —— hmac.compare_digest 对含非 ASCII 的 str 会抛 TypeError，
        # 用户在登录框里输入中文就会命中，异常冒泡会让连接被静默关闭
        # （浏览器表现为 ERR_EMPTY_RESPONSE）。
        account = verify_login(self.server.settings, username, password)
        if account is None:
            guard.fail(ip)
            locked = guard.blocked(ip)
            if locked > 0:
                self._fail_locked(locked)
            else:
                # 不再透露「还剩几次」（报告 A-02）
                self._fail('用户名或密码不正确')
            return

        # 每账号设备数限制：达到上限时**拒绝新登录**，
        # 可在设置页踢掉旧设备、或在应用中心「设置」里调高上限/关掉限制。
        if not self._device_slot_available(account):
            sec = self.server.settings['login_security']
            limit = int(account.get('max_devices') or sec.get('default_max_devices')
                        or DEFAULT_MAX_DEVICES)
            self._fail('账号「%s」已达到 %d 台设备上限，请先在其他设备退出登录'
                       % (escape_html(account['user']), limit), HTTPStatus.FORBIDDEN)
            return

        guard.reset(ip)
        token = make_session(self.server.secret, account['user'])
        self.server.session_store.register(token, account['user'])
        self._redirect('/', {'Set-Cookie': '%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax'
                                          % (SESSION_COOKIE, token, SESSION_TTL)})

    def _device_slot_available(self, account):
        sec = self.server.settings['login_security']
        if not sec.get('device_limit_enabled', True):
            return True
        if account.get('unlimited_devices'):
            return True
        store = self.server.session_store
        store.prune()
        limit = int(account.get('max_devices') or sec.get('default_max_devices')
                    or DEFAULT_MAX_DEVICES)
        return store.count(account['user']) < limit

    def _ip_allowed(self, ip):
        """登录提交的 IP 白/黑名单判定。回环地址始终放行，避免把自己关在外面。"""
        sec = self.server.settings['login_security']
        if not sec.get('ip_filter_enabled'):
            return True
        if is_loopback(ip):
            return True
        if ip_matches(ip, sec.get('ip_blacklist')):
            return False
        white = sec.get('ip_whitelist') or []
        if white and not ip_matches(ip, white):
            return False
        return True

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

    def __init__(self, settings=None):
        self._lock = threading.Lock()
        self._state = {}
        self._settings = settings if isinstance(settings, dict) else {}

    def _limits(self):
        """(失败次数阈值, 锁定时长秒数)，每次调用都从设置里现取，改配置即时生效。"""
        sec = self._settings.get('login_security') or {}
        return (normalize_int(sec.get('max_fail'), LOGIN_MAX_FAIL, 1, 999),
                normalize_int(sec.get('lock_seconds'), LOGIN_LOCK_SECONDS, 5, 86400))

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
            max_fail, lock_seconds = self._limits()
            if count >= max_fail and not self.trusted(ip):
                until = now + lock_seconds

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
    """会话登记表（进程内）

    会话本身仍是 HMAC 签名的无状态 Cookie，不写盘；这里额外记录每个已签发会话
    属于哪个账号、何时签发，用来：

    1. 登出 / 被踢时立即失效（原「吊销表」的能力）；
    2. 统计某个账号当前有多少台设备在线，落实「每个账号最多 N 台设备」。

    有效性的判定仍是**签名 + 未被吊销**，登记表只负责计数 —— 这样应用重启后
    已登录的设备不会被强制下线。代价是重启后设备计数从 0 重新开始
    （与登录限流同样都是进程内状态，属已知取舍）。
    """

    def __init__(self, ttl=SESSION_TTL):
        self._lock = threading.Lock()
        self._ttl = ttl
        self._sessions = {}      # token -> {'user', 'issued', 'exp'}
        self._revoked = {}       # token -> exp

    def prune(self):
        """清掉已过期的登记与吊销记录。"""
        now = time.time()
        with self._lock:
            for token in [t for t, r in self._sessions.items() if r['exp'] <= now]:
                self._sessions.pop(token, None)
            for token in [t for t, exp in self._revoked.items() if now >= exp]:
                self._revoked.pop(token, None)

    def register(self, token, user):
        now = time.time()
        with self._lock:
            self._sessions[token] = {'user': user, 'issued': now, 'exp': now + self._ttl}
            self._revoked.pop(token, None)
            if len(self._sessions) > 4096:      # 兜底清理，避免长期累积
                for stale in [t for t, r in self._sessions.items() if r['exp'] <= now]:
                    self._sessions.pop(stale, None)

    def revoke(self, token):
        if not token:
            return
        now = time.time()
        with self._lock:
            self._sessions.pop(token, None)
            self._revoked[token] = now + self._ttl
            if len(self._revoked) > 512:
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

    def devices(self, user):
        """该账号当前在线的设备，按签发时间升序（最早登录的排前面）。"""
        now = time.time()
        with self._lock:
            items = [(t, r) for t, r in self._sessions.items()
                     if r['user'] == user and r['exp'] > now]
        items.sort(key=lambda kv: kv[1]['issued'])
        return [{'token': t, 'issued': r['issued'], 'exp': r['exp']} for t, r in items]

    def count(self, user):
        return len(self.devices(user))


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
    settings = coerce_settings(settings)     # 容忍不完整 / 旧版配置
    httpd = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    httpd.daemon_threads = True
    httpd.settings = settings
    httpd.secret = secret
    httpd.ui_root = ui_dir()
    httpd.login_guard = LoginGuard(settings)
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
    had_key = isinstance(settings.get('session_key'), str) and len(settings['session_key']) >= 32
    secret = session_secret(settings)       # 缺 session_key 时会补一个
    if not had_key:
        try:
            save_settings(settings)         # 落盘，避免每次重启都把已登录设备踢下线
        except OSError as err:
            sys.stderr.write('[fnos-qrcode] 无法写入 session_key（重启后会话将失效）：%r\n'
                             % (err,))

    httpd = build_server(port, settings, secret)

    def stop(signum, frame):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):
            pass

    auth_state = '开启' if settings['auth']['enabled'] else '关闭'
    sys.stderr.write('[fnos-qrcode] listening on 0.0.0.0:%d  ui=%s  auth=%s  accounts=%d\n'
                     % (port, httpd.ui_root, auth_state, len(settings['accounts'])))
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

    # ---- 账号 ----
    new_user = str(args.user or '').strip()
    raw_pass = None
    if args.password:
        raw_pass = str(args.password)
    elif args.pass_stdin:
        raw_pass = sys.stdin.read().strip() or None

    if new_user or raw_pass:
        target = find_account(settings, new_user) if new_user else None

        if target is None and new_user:
            if len(settings['accounts']) == 1:
                # 单账号场景沿用旧语义：「改用户名」= 重命名。
                # 但**必须同时给了新密码**才动 —— 否则「打开应用中心设置对话框直接保存」
                # 就会把用户的账号名改成表单默认值（admin），属于误伤。
                if raw_pass:
                    settings['accounts'][0]['user'] = new_user
                    target = settings['accounts'][0]
                    changed.append('user=%s' % new_user)
                else:
                    sys.stderr.write('提示：账号「%s」不存在且未提供新密码，已忽略用户名变更\n'
                                     % new_user)
            elif raw_pass:
                if len(settings['accounts']) >= MAX_ACCOUNTS:
                    sys.stderr.write('错误：账号数量已达上限 %d 个\n' % MAX_ACCOUNTS)
                    return 1
                target = {'user': new_user, 'password': None,
                          'max_devices': settings['login_security']['default_max_devices'],
                          'unlimited_devices': False}
                settings['accounts'].append(target)
                changed.append('account+=%s' % new_user)
            else:
                # 关键：没有密码就不允许创建账号，避免留下一个无密码的账号
                sys.stderr.write('提示：账号「%s」不存在且未提供新密码，已忽略\n' % new_user)
        elif target is None and raw_pass:
            if settings['accounts']:
                target = settings['accounts'][0]
            else:
                sys.stderr.write('错误：请用 --user 指定要设置密码的账号\n')
                return 1

        if target is not None and raw_pass:
            if len(raw_pass) < 6:
                sys.stderr.write('错误：登录密码至少 6 位\n')
                return 1
            target['password'] = hash_password(raw_pass)
            changed.append('password(%s)=已更新' % target['user'])

    # ---- 登录安全 ----
    sec = settings['login_security']

    if args.max_fail is not None:
        val = normalize_int(args.max_fail, None, 1, 999)
        if val is None:
            sys.stderr.write('错误：失败次数必须是 1~999 的整数，收到 %r\n' % args.max_fail)
            return 1
        if val != sec['max_fail']:
            sec['max_fail'] = val
            changed.append('max_fail=%d' % val)

    if args.lock_seconds is not None:
        val = normalize_int(args.lock_seconds, None, 5, 86400)
        if val is None:
            sys.stderr.write('错误：锁定时长必须是 5~86400 秒，收到 %r\n' % args.lock_seconds)
            return 1
        if val != sec['lock_seconds']:
            sec['lock_seconds'] = val
            changed.append('lock_seconds=%d' % val)

    if args.device_limit is not None:
        on = str(args.device_limit).strip().lower() in ('on', 'true', '1', 'yes',
                                                       'enable', 'enabled')
        if on != sec['device_limit_enabled']:
            sec['device_limit_enabled'] = on
            changed.append('device_limit=%s' % ('on' if on else 'off'))

    if args.max_devices is not None:
        val = normalize_int(args.max_devices, None, 1, 999)
        if val is None:
            sys.stderr.write('错误：设备上限必须是 1~999 的整数，收到 %r\n' % args.max_devices)
            return 1
        if val != sec['default_max_devices']:
            sec['default_max_devices'] = val
            changed.append('max_devices=%d' % val)

    if args.ip_filter is not None:
        on = str(args.ip_filter).strip().lower() in ('on', 'true', '1', 'yes',
                                                     'enable', 'enabled')
        if on != sec['ip_filter_enabled']:
            sec['ip_filter_enabled'] = on
            changed.append('ip_filter=%s' % ('on' if on else 'off'))

    for attr, label in (('ip_blacklist', '黑名单'), ('ip_whitelist', '白名单')):
        raw = getattr(args, attr)
        if raw is None:
            continue
        items = normalize_ip_list(raw)
        bad = [x for x in items if not _valid_cidr(x)]
        if bad:
            sys.stderr.write('错误：IP %s格式不正确：%s\n' % (label, ', '.join(bad)))
            return 1
        if items != sec[attr]:
            sec[attr] = items
            changed.append('%s=%d 条' % (attr, len(items)))

    # 开启认证但没有任何可用账号时拒绝写入，避免把应用锁死或放空
    if settings['auth']['enabled']:
        usable = [a for a in settings['accounts'] if isinstance(a.get('password'), dict)]
        if not usable:
            sys.stderr.write('错误：开启用户认证时必须至少有一个设置了密码的账号\n')
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
    parser.add_argument('--user', default='', help='登录用户名（不存在则创建；仅有一个账号时表示重命名）')
    parser.add_argument('--pass', dest='password', default='', help='登录密码')
    parser.add_argument('--pass-stdin', dest='pass_stdin', action='store_true',
                        help='从标准输入读取登录密码（供生命周期脚本使用，避免密码出现在 ps 里）')
    parser.add_argument('--max-fail', dest='max_fail', default=None,
                        help='连续登录失败多少次后锁定（1~999）')
    parser.add_argument('--lock-seconds', dest='lock_seconds', default=None,
                        help='锁定时长秒数（5~86400）')
    parser.add_argument('--device-limit', dest='device_limit', default=None,
                        help='on / off：是否限制每个账号的在线设备数')
    parser.add_argument('--max-devices', dest='max_devices', default=None,
                        help='新账号默认的设备数上限（1~999）')
    parser.add_argument('--ip-filter', dest='ip_filter', default=None,
                        help='on / off：是否启用登录 IP 黑白名单')
    parser.add_argument('--ip-blacklist', dest='ip_blacklist', default=None,
                        help='IP 黑名单，逗号分隔，支持 10.0.0.0/8 这样的网段')
    parser.add_argument('--ip-whitelist', dest='ip_whitelist', default=None,
                        help='IP 白名单，非空时仅这些来源可提交登录')
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
