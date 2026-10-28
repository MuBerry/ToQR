# 构建说明（DEPLOY.md）

> 本文档说明「二维码批量生成器」（ToQR）的**构建流程、产物校验、安装验证与发布方式**，以飞牛 fnOS 应用包（FPK）为主线。
>
> 源码以仓库为准，本文**不再内嵌源码全文**——源码有任何改动，`git diff` 就是唯一事实来源。功能说明见 [README.md](./README.md)，版本记录见 [CHANGELOG.md](./CHANGELOG.md)，FPK 的安装/排错细节见 [fnos-qrcode/fpk/README.md](./fnos-qrcode/fpk/README.md)。

最后更新：2026-10-28（`manifest` 与服务端 `APPVERSION` 统一为 `1.2.8`）

---

## 目录

1. [项目形态与文档范围](#1-项目形态与文档范围)
2. [仓库结构](#2-仓库结构)
3. [环境要求](#3-环境要求)
4. [构建 FPK](#4-构建-fpk)
5. [产物校验](#5-产物校验)
6. [安装与验证](#6-安装与验证)
7. [其他部署形态](#7-其他部署形态)
8. [实现要点与踩坑记录](#8-实现要点与踩坑记录)

---

## 1. 项目形态与文档范围

仓库同时维护三种部署形态，共用同一份前端与同一份服务端源码：

| 形态 | 位置 | 产物 | 说明 |
| --- | --- | --- | --- |
| **原生应用包（FPK）** | `fnos-qrcode/fpk/` | `fnos-qrcode/fnos-qrcode_<版本>.fpk` | **本文主题**。应用中心手动安装，桌面出现图标，自带 Python 服务 |
| 通用服务 | `server/` + `ui/` | Docker 镜像 / 单文件二进制 / 源码运行 | 默认端口 `12990`，零第三方依赖 |
| 纯静态 | `fnos-qrcode/html/` + `nginx/` | 静态站点目录 | 无后端，二维码在浏览器内生成 |

三种形态的关系：`ui/` 是唯一的前端源，`server/app.py` 是唯一的服务端源；FPK 与静态站都以它们为源、在构建时复制生成。

### 单一真源约定

| 源文件 | 副本 / 生成物 | 由谁生成 |
| --- | --- | --- |
| `server/app.py` | `fnos-qrcode/fpk/app/server/app.py` | `build-fpk.sh` |
| `ui/index.html`、`ui/qrscan.html`、`ui/vendor/` | `fnos-qrcode/fpk/app/ui/` | `build-fpk.sh` |
| `ui/` | `fnos-qrcode/html/fnos-qrcode/`、`fnos-qrcode/index.html` | `fnos-qrcode/tools/sync-static.py` |

> `fnos-qrcode/fpk/app/` 下的全部内容与 `fnos-qrcode/*.fpk` 是**生成物，已列入 `.gitignore`**，不要手改、也不要提交。
> 因此不要单独把 `fpk/` 目录拷走再打包——包里会缺服务端与页面。统一用 `build-fpk.sh`。
>
> `fnos-qrcode/html/` 与 `fnos-qrcode/index.html` 同样是生成物，但为了「拿到仓库就能挂静态站」而**入库**。
> 改完 `ui/` 后要重跑 `python3 fnos-qrcode/tools/sync-static.py` 并把产物一起提交，否则静态站会落后于通用版。

---

## 2. 仓库结构

```
ToQR/
├── server/
│   └── app.py                       # 服务端唯一源：静态托管 + 路由 + 登录 + 本地 QR 编码（纯标准库）
├── ui/
│   ├── index.html                   # 出码页：/、/qr、/qr-shop 共用，行为由地址决定
│   ├── qrscan.html                  # 扫码识别页：/qrscan
│   ├── vendor/
│   │   ├── toqr-qr.js               # 浏览器内 QR 编码器（「备用1 · 离线」+ 静态版用）
│   │   └── zxing.min.js             # 条码解码库（无原生 BarcodeDetector 时用）
│   └── images/                      # 站点图标
├── fnos-qrcode/                     # 飞牛 fnOS 部署形态
│   ├── fpk/                         # ★ FPK 工程（本文主题）
│   │   ├── manifest                 # 应用清单：appname / version / 端口 / 入口
│   │   ├── build-fpk.sh             # 一键构建脚本（唯一推荐入口）
│   │   ├── ICON.PNG、ICON_256.PNG   # 列表图标
│   │   ├── app/                     # 生成物，不入库
│   │   │   ├── server/app.py        #   由仓库根 server/app.py 复制
│   │   │   └── ui/                  #   由仓库根 ui/ 复制（含 config 桌面入口、images）
│   │   ├── cmd/                     # 生命周期脚本（main / install / config / upgrade / uninstall）
│   │   ├── config/                  # privilege（运行身份）、resource（资源声明）
│   │   ├── wizard/                  # install / config / uninstall 三个向导的字段定义
│   │   ├── tools/                   # build_fpk.py（备用打包器）、make_icons.py（图标）
│   │   └── README.md、README-USER.md
│   ├── html/fnos-qrcode/            # 静态整站目录（生成物）
│   ├── nginx/                       # default.conf（根路径 + 子路径）、reverse-proxy.conf
│   ├── tools/sync-static.py         # 从 ui/ 生成静态副本
│   ├── docker-compose.yml           # 静态站方案（nginx:alpine，12990）
│   ├── index.html                   # 静态单页版（生成物）
│   └── README.md                    # 静态部署详细说明
├── .github/workflows/release.yml    # 打 tag 自动构建三平台二进制 + Docker 镜像 + Release
├── Dockerfile                       # python:3.12-slim + server/ + ui/
├── docker-compose.yml               # 通用镜像的 compose（12990 + toqr-data 卷）
├── CHANGELOG.md
├── DEPLOY.md                        # 本文件
└── README.md
```

### FPK 工程文件职责

| 文件 | 职责 |
| --- | --- |
| `manifest` | `appname=fnos-qrcode`、`version`、`service_port=12990`、`checkport=false`、`desktop_uidir=ui` |
| `build-fpk.sh` | 五步：同步源 → 整理权限 → 清理垃圾 → 打包 → 归档产物 |
| `app/ui/config` | 桌面入口 JSON，端口写 `${wizard_port}`（应用中心运行时替换） |
| `config/privilege` | 运行身份：`run-as: package` |
| `config/resource` | 资源声明（数据共享为空） |
| `wizard/install` | 安装向导：服务端口 + 认证三项 |
| `wizard/config` | 设置向导（装后调整，字段名与安装向导一致） |
| `wizard/uninstall` | 卸载向导：保留 / 删除配置 |
| `cmd/main` | `start` / `stop` / `status`；启动时注入 `TOQR_APP_NAME`、`TOQR_DEFAULT_PORT` |
| `cmd/install_init` | 安装前校验：包内服务端文件、Python3、端口、密码 |
| `cmd/install_callback` | 安装后落盘配置、修权限、同步 manifest 端口 |
| `cmd/config_init` / `cmd/config_callback` | 装后改设置：写入配置并按需重启 |
| `cmd/upgrade_init` / `cmd/upgrade_callback` | 升级：停旧进程、保留已有配置 |
| `cmd/uninstall_init` / `cmd/uninstall_callback` | 卸载：停进程、按向导保留或删除配置 |
| `tools/build_fpk.py` | 无 `fnpack` 时的等效打包器（按 fnpack 1.2.3 实测产物结构） |
| `tools/make_icons.py` | 重生成图标（构建期工具，需 Pillow） |

---

## 3. 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| Python3 | 3.6+ | 构建与运行都需要；FPK 运行时用盒子上的系统 Python |
| bash | 任意 | 执行 `build-fpk.sh` 与 `cmd/` 脚本 |
| 官方 `fnpack` | 可选 | 飞牛系统自带 `/usr/local/bin/fnpack`，会做完整校验，**优先使用** |
| Pillow | 可选 | 仅重生成图标时需要 |
| Docker | 可选 | 仅构建通用镜像时需要 |

服务端本身**零第三方依赖**，不需要 `pip install`。

---

## 4. 构建 FPK

### 4.1 一键构建（推荐）

在仓库根执行：

```bash
bash fnos-qrcode/fpk/build-fpk.sh          # 构建
bash fnos-qrcode/fpk/build-fpk.sh --list   # 只列打包清单，不产出文件
```

产物：

```
fnos-qrcode/fnos-qrcode_<版本>.fpk        # 直接安装 / 附到 Release
fnos-qrcode/fnos-qrcode_<版本>.fpk.zip    # 论坛等不便传二进制的地方用
```

每次只保留当前版本，历史版本靠 git tag 与 Release 留存。

### 4.2 构建脚本做了什么

| 步骤 | 动作 |
| --- | --- |
| 1/5 | **同步源**：`server/app.py` → `app/server/app.py`；`ui/index.html`、`ui/qrscan.html`、`ui/vendor/*` → `app/ui/` |
| 2/5 | **整理权限**：`cmd/*` → `755`；`manifest`、`config/*`、`wizard/*` → `644` |
| 3/5 | **清理垃圾**：`__pycache__`、`*.pyc`、`.DS_Store`、旧 `*.fpk` |
| 4/5 | **打包**：检测到 `fnpack` 用 `fnpack build`，否则用 `tools/build_fpk.py` |
| 5/5 | **归档**：按 `manifest` 的版本号重命名到 `fnos-qrcode/`，并生成 `.zip` |

第 1 步缺任一源文件会直接失败并打印缺失路径——这是刻意的，避免产出缺页面的包。

### 4.3 直接用官方 fnpack 打包

`fnpack` 只认 `fpk/` 目录里的内容，**不负责同步**。所以必须先让第 1 步跑过一次（整仓库构建一次即可），再在 `fpk/` 里打包：

```bash
bash fnos-qrcode/fpk/build-fpk.sh --list   # 只同步 + 列清单，不打最终产物
cd fnos-qrcode/fpk && fnpack build
```

若跳过了同步，包内会缺 `app/server/app.py`；此时 `cmd/install_init` 会在**安装阶段**直接失败并提示重新打包，不会等到装完启动才报错。

### 4.4 无 fnOS 环境的备用打包器

```bash
cd fnos-qrcode/fpk
python3 tools/build_fpk.py            # 直接产出 fpk
python3 tools/build_fpk.py --list     # 只打印 app.tgz 与外层清单
```

产物结构与官方 `fnpack` 同构。它同样带结构自检：缺 `app/server/app.py` 或 `app/ui/index.html` 时报错退出，并提示先执行 `build-fpk.sh`。

> Windows 上构建也可用：打包器已把 tar 内路径统一为正斜杠（早期版本会产生 `server\app.py` 这种带反斜杠的条目，装到 Linux 侧会变成一个文件名而非目录）。

### 4.5 FPK 容器结构

```
fnos-qrcode_<版本>.fpk                ← gzip 压缩的 tar
├── manifest                          ← 含 checksum = app.tgz 的 MD5
├── app.tgz                           ← 内层 tar.gz，内容为 app/ 下的文件（不带 app/ 前缀）
│   ├── server/app.py
│   └── ui/
│       ├── index.html、qrscan.html
│       ├── config                    ← 桌面入口（端口用 ${wizard_port}）
│       ├── images/icon_64.png、icon_256.png
│       └── vendor/toqr-qr.js、zxing.min.js
├── cmd/                              ← 生命周期脚本（755）
├── config/                           ← privilege、resource
├── wizard/                           ← install、config、uninstall
├── ICON.PNG                          ← 64x64
└── ICON_256.PNG                      ← 256x256
```

关键点：

- `desktop_uidir = ui` 指向的 `ui/` 在 **`app.tgz` 内部**，不在外层；
- `app.tgz` 内**不带 `app/` 前缀**；
- `manifest.checksum` 是 `app.tgz` 的 MD5，写入后才组装外层——顺序错了包会被应用中心拒绝。

---

## 5. 产物校验

构建后务必确认三件事：`checksum` 与实际 `app.tgz` 一致、包内文件齐全、路径分隔符正确。

```python
import hashlib, io, tarfile

path = 'fnos-qrcode/fnos-qrcode_1.2.8.fpk'
outer = tarfile.open(path, 'r:*')                     # fpk 是 gzip tar，不是 zip

manifest = outer.extractfile('manifest').read().decode('utf-8')
checksum = dict(
    (k.strip(), v.strip())
    for k, _, v in (line.partition('=') for line in manifest.splitlines() if '=' in line)
)


app_tgz = outer.extractfile('app.tgz').read()
print('app.tgz md5 实际 =', hashlib.md5(app_tgz).hexdigest())
print('manifest checksum =', checksum.get('checksum'))

inner = tarfile.open(fileobj=io.BytesIO(app_tgz))
names = inner.getnames()
print('条目:', names)
print('含反斜杠:', [n for n in names if '\\' in n] or '无')
print('服务端与仓库一致:',
      inner.extractfile('server/app.py').read() == open('server/app.py', 'rb').read())
```

期望输出：

```
app.tgz md5 实际 = <与 manifest checksum 相同>
manifest checksum = <同上>
条目: ['server', 'server/app.py', 'ui', 'ui/config', 'ui/index.html', 'ui/qrscan.html',
       'ui/images', 'ui/images/icon_256.png', 'ui/images/icon_64.png',
       'ui/vendor', 'ui/vendor/toqr-qr.js', 'ui/vendor/zxing.min.js']
含反斜杠: 无
服务端与仓库一致: True
```

### 2026-10-28 本次构建实测

| 项目 | 结果 |
| --- | --- |
| `build-fpk.sh` 构建 | 成功；`app.tgz` 191873 B，`app.tgz` MD5 `1bdf6dfae4f5091bb81958e6d454f868` |
| 产物 | `fnos-qrcode_1.2.8.fpk`，229157 B，MD5 `ee6d0d72ced765fda70cd30ae645de48` |
| 版本一致性 | `manifest` 的 `version` 与包内 `APPVERSION` 同为 `1.2.8` |
| 默认端口 | 无配置启动即监听 `12990`（`--print-port` 返回 `12990`），`GET /` 200 |
| 干净检出后构建 | 排除全部生成物重新构建，一次通过 |
| 包内 `server/app.py` | 与仓库源字节一致 |
| 反斜杠路径 | 无 |
| 模拟 fnOS 环境运行 | `--print-port` 返回 `12990`；`GET /` 200（77043 B）；`GET /qrscan` 200（43050 B） |

> 产物 MD5 每次构建都会变（打包时间戳参与），上表仅作本次记录，不作为可复现的固定值。

---

## 6. 安装与验证

### 6.1 安装向导字段

| 向导项 | 默认 | 说明 |
| --- | --- | --- |
| 服务端口 | `12990` | 1024~65535，不能与其他应用冲突 |
| 是否开启用户认证 | 不开启 | 开启后访问本应用需先登录 |
| 登录用户名 | `admin` | 仅在开启认证时生效 |
| 登录密码 | 空 | 至少 6 位；开启认证时必填 |

### 6.2 Web UI（推荐，能看到向导）

1. 应用中心 → 左下角「设置」→ 打开「手动安装」
2. 应用中心 → 左下角「手动安装」→ 选择 `fnos-qrcode_<版本>.fpk`
3. 按向导填写端口与认证
4. 装完点「启用」，桌面出现「二维码批量生成器」，点击即打开

### 6.3 命令行

```bash
cat > /tmp/install.env <<'ENV'
wizard_port=12990
wizard_auth_enable=off
wizard_auth_user=admin
wizard_auth_pass=
ENV

sudo appcenter-cli manual-install enable        # 首次需要打开手动安装开关
sudo appcenter-cli install-fpk fnos-qrcode_1.2.8.fpk -e /tmp/install.env -v 1
sudo appcenter-cli start fnos-qrcode
```

> 带向导的应用**卸载**必须走 Web UI（命令行会提示 `please uninstall it from Web UI`）。
> 升级目前等同「卸载 → 重新安装」，`upgrade_*` 脚本已实现并会保留你改过的端口与认证设置。

### 6.4 升级兼容性（已实测）

FPK 形态通过环境变量保持与旧安装一致：`cmd/main` 启动服务时注入 `TOQR_APP_NAME=fnos-qrcode`（沿用原会话密钥的哈希盐）与 `TOQR_DEFAULT_PORT=12990`（缺配置时的默认端口）。因此升级不会把已登录设备踢下线，也不会与 manifest 的 `service_port` 错位。

2026-10-28 实测（旧版服务端写配置并真实登录 → 新代码接管同一份配置）：

| 场景 | 结果 |
| --- | --- |
| 新版用**旧版 Cookie** 访问 `/` | 200，无需重新登录 |
| 新版无 Cookie 访问 `/` | 302 → `/login` |
| 用旧密码登录新版 | 302 成功 |
| 错误密码 | 401 拒绝 |
| 端口 / 账号 / 认证开关 / 配置文件路径 | 与旧版读取结果完全一致 |

### 6.5 整机安装验证

真机（飞牛设备）上的完整安装、装后改端口、启停与状态检测清单维护在
[`fnos-qrcode/fpk/README.md`](./fnos-qrcode/fpk/README.md) 的「在测试机上实测通过的项目」一节，
以及 [`CHANGELOG.md`](./CHANGELOG.md) 对应版本条目。本文不重复罗列历史验证记录，避免两处数字漂移。

---

## 7. 其他部署形态

### 7.1 源码运行

```bash
python3 server/app.py --port 12990
```

配置与数据默认落在程序目录的 `etc/`、`var/`；可用环境变量重定向：

| 环境变量 | 作用 |
| --- | --- |
| `TOQR_UI_DIR` | 静态资源目录 |
| `TOQR_ETC_DIR` | 配置目录（`settings.json`） |
| `TOQR_VAR_DIR` | 运行数据目录 |
| `TOQR_APP_NAME` | 应用标识，参与会话密钥派生（默认 `toqr`） |
| `TOQR_DEFAULT_PORT` | 缺配置时的默认端口（默认 `12990`） |

`TOQR_APP_NAME` / `TOQR_DEFAULT_PORT` 供 FPK 形态注入，改动它们会使已签发的会话 Cookie 失效。

### 7.2 Docker / compose

```bash
docker build -t toqr:latest .
docker run -d --name toqr -p 12990:12990 -v toqr-data:/data toqr:latest
```

镜像基于 `python:3.12-slim`，只拷 `server/` 与 `ui/`，不联网装依赖；配置与会话数据固定落 `/data`。CI 已推送 `ghcr.io/muberry/toqr:latest` 与 `:v<版本>`。

### 7.3 单文件二进制

由 `.github/workflows/release.yml` 用 PyInstaller 打包，产物为 `toqr-<版本>-<平台>.tar.gz`（Windows 为 `.zip`），平台为 `linux-amd64`、`macos-arm64`、`windows-amd64`。配置与数据默认落在可执行文件同目录的 `etc/`、`var/`。

### 7.4 纯静态（nginx，无后端）

```bash
python3 fnos-qrcode/tools/sync-static.py     # 从 ui/ 生成 fnos-qrcode/html/fnos-qrcode/
cd fnos-qrcode && docker compose up -d       # 起静态站，默认 12990
```

静态版差异：二维码在**浏览器内**生成，没有「默认 / 草料」两档解析器；`/qrscan` 仍可用但**必须 HTTPS** 才能调摄像头；没有登录、设置页与服务端历史记录（历史存 localStorage）。

---

## 8. 实现要点与踩坑记录

### 8.1 端口为什么能「装时可指定、装后可改」

```
向导 wizard_port
   └─> cmd/install_callback、cmd/config_callback
          ├─> app.py --set --port N
          │      └─> $TRIM_PKGETC/settings.json      ← 运行时唯一真实来源
          │             ├─> cmd/main 启动时 app.py --print-port 读取
          │             └─> app/ui/config 的 ${wizard_port} 由应用中心运行时替换
          └─> 同步已安装 manifest 的 service_port = N
                 └─> /var/apps/<app>/manifest        ← 应用中心据此做端口占用检查
```

- 桌面入口端口写 `${wizard_port}`，所以**改端口不需要重建包**；
- 应用中心「启用」前的占用检查只认**已安装 manifest 的静态 `service_port`**，不认 `${wizard_port}`。本包因此 `checkport = false`，并由回调里的 `sync_manifest_port()` 把该字段同步成真实端口。

### 8.2 认证

PBKDF2-SHA256（20 万次迭代 + 随机盐）存密码，`accounts` 数组支持多账号（上限 10）；会话是 HMAC 签名 Cookie，密钥存 `settings.json` 的 `session_key`，**增删账号与重启都不会踢掉已登录设备**（要强制重登就删掉该字段再重启）。

安全项：一次性 CSRF 令牌、登录失败按来源 IP 计数与锁定、IP 黑白名单（只作用于登录提交）、回环始终放行、CSP / `X-Frame-Options: DENY` / `Referrer-Policy` / `nosniff` 等响应头。

### 8.3 踩坑记录

| 问题 | 原因 | 处理 |
| --- | --- | --- |
| 改任何端口都提示「端口被占用」 | 应用中心拿 manifest 里静态 `service_port` 做检查，它不跟随向导改的端口 | `checkport = false` + `sync_manifest_port()` 同步真实端口 |
| 登录锁定倒计时「卡住不动」 | 锁定期内每次提交都把 `until` 推后一整轮 | 只在首次越界时设置 `until`，锁定期内仅累加计数 |
| 登录框输入中文报 `ERR_EMPTY_RESPONSE` | 用 `hmac.compare_digest` 直接比较含非 ASCII 的用户名，抛异常导致连接被静默关闭 | 编码后再比较，并加「异常也返回 500」兜底 |
| 打包下载 zip 在真实部署上必失败 | 前端直连第三方接口，该接口只对 localhost 放行 CORS | 已随「二维码改为本地编码」一并消除 |
| 二维码必须联网、内容出本机 | 早期版本走草料开放平台公开接口 | v1.2.3 起服务端内置 QR Code Model 2 编码器，纯标准库本地出码，页面不引用任何外链 |
| Windows 构建出的包在 Linux 侧结构错乱 | 打包器用 `os.path.join` 生成 tar 条目名，产生 `server\app.py` | 条目名统一正斜杠 |
| 服务端两份副本逐渐漂移 | `fnos-qrcode/fpk/app/server/app.py` 是手工维护的第二份 | 改为构建时从 `server/app.py` 复制生成并 gitignore；fpk 的标识与默认端口用环境变量注入 |
| 绕过构建脚本直接 `fnpack build` 产出缺服务端的包 | `fnpack` 只读 `app/`，不负责同步 | `cmd/install_init` 在安装阶段校验 `app/server/app.py` 并提示重新打包 |

### 8.4 版本号

版本号只有一条线：**1.2.x**。两处必须同步，改版本时一起改：

| 位置 | 作用 |
| --- | --- |
| `fnos-qrcode/fpk/manifest` 的 `version` | 安装包命名（`fnos-qrcode_<版本>.fpk`）、应用中心显示、Release 标题 |
| `server/app.py` 的 `APPVERSION` | `Server` 响应头、访问草料接口时的 `User-Agent` |

发版时这两个值与 `CHANGELOG.md` 的版本标题、git tag 名四者一致。

注意 `APPVERSION` 会被 `build-fpk.sh` 复制进包内，所以**改完要重新打包**，否则包里仍是旧值。改版前可先核对：

```bash
grep -n '^version' fnos-qrcode/fpk/manifest
grep -n '^APPVERSION' server/app.py
```

---

## 附：许可与出处

服务端与前端均为本仓库自有实现，纯 Python 标准库，未引入第三方运行时依赖。`ui/vendor/zxing.min.js` 为第三方解码库（ZXing 的 JS 构建），仅用于 `/qrscan` 在缺少原生 `BarcodeDetector` 时解码。
