# ToQR — 二维码批量生成器

自托管二维码批量生成服务：把一批文本/链接粘贴进去，一键批量出码，支持在线预览、打包下载、历史记录。**粘贴购物 App 的分享文案会自动识别其中的链接**（京东/淘宝/天猫/拼多多/抖音/小红书等，含无协议短链），只给链接出码；点码放大；手机上点链接直接跳转对应 App。

纯 Python 标准库实现，**零第三方依赖**，无构建步骤。默认**完全离线**：二维码在服务端本地编码，页面不引用任何外部资源；也可以按需切换成「浏览器内离线编码」或「草料开放平台」解析器。

## 部署方式

五种方式任选其一，装完浏览器打开 `http://<主机>:<端口>/` 即可使用。

### 1. 源码安装

```bash
git clone https://github.com/MuBerry/ToQR.git
cd ToQR
python3 server/app.py --port 8080
```

要求 Python 3.8+，**不需要 `pip install`**（纯标准库）。

生产环境建议用 systemd 托管：

```ini
# /etc/systemd/system/toqr.service
[Unit]
Description=ToQR 二维码批量生成器
After=network.target

[Service]
WorkingDirectory=/opt/toqr
Environment=TOQR_ETC_DIR=/var/lib/toqr/etc
Environment=TOQR_VAR_DIR=/var/lib/toqr/var
ExecStart=/usr/bin/python3 /opt/toqr/server/app.py --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now toqr      # 开机自启 + 立即启动
journalctl -u toqr -f                 # 查看日志
```

### 2. Docker（自带 Dockerfile）

仓库根目录的 `Dockerfile` 基于 `python:3.12-slim`，只拷 `server/` 和 `ui/`，不联网装依赖：

```bash
git clone https://github.com/MuBerry/ToQR.git
cd ToQR
docker build -t toqr:latest .
docker run -d --name toqr -p 8080:8080 -v toqr-data:/data toqr:latest
```

也可以直接用官方镜像（GitHub Actions 自动构建推送）：

```bash
docker run -d --name toqr -p 8080:8080 -v toqr-data:/data ghcr.io/muberry/toqr:latest
```

| 参数 | 说明 |
| --- | --- |
| `-p 8080:8080` | 左边改成宿主机想用的端口，容器内固定 8080 |
| `-v toqr-data:/data` | `settings.json` 与会话持久化，容器内固定 `/data` |
| `--set --auth on ...` | 预置登录账号，见下方「服务端管理」 |

### 3. docker compose

```bash
git clone https://github.com/MuBerry/ToQR.git
cd ToQR
docker compose up -d          # 后台启动
docker compose logs -f        # 查看日志
docker compose down           # 停止并移除容器（数据卷保留）
```

`docker-compose.yml` 已配好端口 8080 与 `toqr-data` 卷；要改端口或预置账号，用 `command:` 覆盖：

```yaml
    command: ["--port", "8080", "--set", "--auth", "on", "--user", "admin", "--pass", "yourpassword"]
```

### 4. 二进制运行（Windows / Linux / macOS）

从 [Releases](../../releases) 下载对应平台的单文件可执行程序，无需 Python：

```bash
# Windows（命令行/PowerShell）
.\toqr-<版本>-windows-amd64.exe --port 8080

# Linux / macOS（Apple Silicon）
chmod +x ./toqr-<版本>-<平台>
./toqr-<版本>-<平台> --port 8080
```

配置与数据默认落在可执行文件同目录的 `etc/`、`var/`；想换位置用环境变量 `TOQR_ETC_DIR` / `TOQR_VAR_DIR`。

> macOS 首次运行如被 Gatekeeper 拦截：`xattr -d com.apple.quarantine toqr-*` 后再执行。
> 二进制提供 Windows / Linux 与 Apple Silicon（arm64）版；Intel Mac 可用 Docker 镜像或源码运行。

### 5. 飞牛 fnOS（应用包 / 纯静态）

**5a. 应用包（fpk，推荐）**

应用包自带 Python 服务，桌面出现图标，三条入口与扫码页都可用：

1. 从 [Releases](../../releases) 下载 `fnos-qrcode_<版本>.fpk`；
2. 应用中心 →「手动安装」→ 选该文件；
3. 升级时需先在应用中心卸载旧版，再装新包（`appcenter-cli` 没有升级通道）。

自己打包源码：

```bash
bash fnos-qrcode/fpk/build-fpk.sh        # 产物 fnos-qrcode/fnos-qrcode_<版本>.fpk
```

构建脚本会先把 `ui/` 里的页面和解码库复制进 `fnos-qrcode/fpk/app/ui/`，再按 `fnos-qrcode/fpk/manifest` 里的版本号打包。

**5b. 纯静态（nginx，无后端）**

没有 Python 也能用：页面在浏览器内完成编码与解码。先由源码生成静态副本，再用 nginx 托管：

```bash
python3 fnos-qrcode/tools/sync-static.py   # 从 ui/ 生成 html/fnos-qrcode/ 下的静态页
```

生成 / 用到的文件：

| 文件 | 用途 |
| --- | --- |
| `fnos-qrcode/index.html` | 单页版页面（放站点根目录时用） |
| `fnos-qrcode/html/fnos-qrcode/` | nginx 托管的整站目录：`index.html`、`qrscan.html`、`vendor/` |
| `fnos-qrcode/nginx/default.conf` | nginx 站点配置：同时支持根路径与 `/fnos-qrcode/` 子路径 |
| `fnos-qrcode/nginx/reverse-proxy.conf` | 给已有反代（下级域名）用的片段 |
| `fnos-qrcode/docker-compose.yml` | 用 nginx:alpine 起一个静态站（默认 8090 端口） |

```bash
cd fnos-qrcode
docker compose up -d        # 起静态站，访问 http://<NAS-IP>:8090/fnos-qrcode/
```

或者直接挂到已有 nginx：

```bash
cp -r fnos-qrcode/html/fnos-qrcode /usr/share/nginx/html/
cp fnos-qrcode/nginx/default.conf /etc/nginx/conf.d/fnos-qrcode.conf
nginx -s reload
```

静态版的差异：
- 二维码在**浏览器内**生成（不经过服务端），没有「默认 / 草料」两档解析器，只剩离线编码；
- `/qrscan` 仍然可用（页面内解码，依赖同目录的 `vendor/zxing.min.js`），但**必须 HTTPS** 才能调摄像头；
- 没有登录、设置页、历史记录服务端能力（历史记录存在浏览器 localStorage）。

更细的静态部署说明（域名反代、子路径、端口冲突）见 [`fnos-qrcode/README.md`](fnos-qrcode/README.md)。

### 端口与配置

```bash
python server/app.py --port 9000            # 指定端口启动（不写配置）
python server/app.py --set --port 9000      # 把端口写进配置（重启后生效）
python server/app.py --print-port           # 查看当前配置端口
python server/app.py --check                # 探测端口是否在监听
```

配置目录优先级：`$TOQR_ETC_DIR` > 程序目录 `etc/`；运行数据：`$TOQR_VAR_DIR` > 程序目录 `var/`。

### HTTPS 与摄像头

`/qrscan` 调摄像头需要**安全上下文**：HTTPS 域名或 `localhost`。用 `http://<局域网IP>:<端口>` 访问时页面会提示并禁用摄像头按钮，图片识别不受影响。建议用 Nginx / Caddy / 飞牛反代挂一层 HTTPS。

## 功能特性

| 功能 | 说明 |
|---|---|
| 批量生成 | 多行文本按行出码，实时预览，打包下载 PNG |
| 三个入口 | `/qr` 每行原样出码、`/qr-shop` 只提取链接出码、`/qrscan` 扫码识别；直接访问根地址等同 `/qr`（不跳转） |
| 链接识别 | 粘贴任意分享文案，自动提取链接并去重（`/qr-shop` 默认行为，`/qr` 也能手动点「提取链接」） |
| 扫码识别 | `/qrscan` 调摄像头连续扫码；也可批量上传 / 拖入 / 粘贴图片，识别二维码与一维码 |
| 点码放大 | 点任意二维码弹出 800px 大图，可下载、复制、用浏览器打开 |
| 手机跳 App | 卡片下方「平台标签 + 短链」，手机点击直接通过 App 协议唤起，没装 App 再点一次走网页 |
| 本地出码 | 服务端内置 QR 编码器，`/api/qr` 直接输出 PNG，不依赖任何外部接口 |
| 解析器可选 | 输入区右上可切换：默认（本机编码）/ 备用1（浏览器离线编码）/ 备用2（草料开放平台 API，服务端转发） |
| 用户认证 | 可选登录（PBKDF2 + HMAC Cookie），多账号、设备数限制、失败锁定、IP 黑白名单 |
| 历史记录 | 最近 50 条本地保存，缩略图回看、点击回填 |

## 三个入口（文根）

同一个服务提供三个入口，页面行为由地址决定，互相之间**不做跳转**：

| 地址 | 行为 |
|---|---|
| `/` 或 `/qr` | 不筛选内容，每行原样编成二维码 |
| `/qr-shop` | 只提取文本里的链接（京东/淘宝/天猫/拼多多/抖音等），仅给链接出码 |
| `/qrscan` | 调摄像头连续扫码；也支持批量上传 / 拖拽 / 粘贴图片，识别二维码与一维码 |

`/qrscan` 的识别结果可以一键导出 zip：里面是原图（`images/`，按序号命名）和 `results.csv`（序号 / 来源 / 图片文件 / 识别内容），图片与文本一一对应。

解码优先用浏览器原生 `BarcodeDetector`（Android Chrome、ChromeOS、macOS 可用），不支持时自动回退到内置的 ZXing（Windows / Linux 的 Chrome、Edge 走这条），无需联网。

> 摄像头只在 **HTTPS 或 localhost** 下可用，这是浏览器强制的安全策略。用 HTTP 访问时页面会提示，但图片识别不受影响。

## 服务端管理

```bash
python server/app.py --port 9000            # 以指定端口启动（不写配置）
python server/app.py --set --port 9000      # 把端口写进配置（重启后生效）
python server/app.py --set --auth on --user admin --pass secret   # 开启登录
python server/app.py --print-port           # 查看当前配置端口
python server/app.py --check                # 探测端口是否在监听
```

完整参数（失败锁定、设备数限制、IP 名单等）见 `python server/app.py --help`。

## 目录结构

```
ToQR/
├── server/
│   └── app.py                 # 服务端：静态托管 + 路由 + 登录 + 本地二维码编码（纯标准库，单文件）
├── ui/
│   ├── index.html             # 出码页：/、/qr、/qr-shop 共用，行为由地址决定
│   ├── qrscan.html            # 扫码识别页：/qrscan
│   ├── vendor/
│   │   ├── toqr-qr.js         # 浏览器内 QR 编码器（「备用1 · 离线」解析器 + 静态版用）
│   │   └── zxing.min.js       # 条码解码库（扫码页在没有原生 BarcodeDetector 时用）
│   └── images/                # 站点图标
├── fnos-qrcode/               # 飞牛 fnOS 部署形态
│   ├── fpk/                   # 原生应用包源码（manifest / cmd / wizard / app / tools）
│   ├── index.html             # 单页静态版（sync-static.py 生成，勿手改）
│   ├── html/fnos-qrcode/      # nginx 整站目录（index.html / qrscan.html / vendor）
│   ├── nginx/                 # nginx 站点配置（根路径 + 子路径）与反代片段
│   ├── tools/sync-static.py   # 从 ui/ 生成上面的静态副本
│   ├── docker-compose.yml     # 用 nginx:alpine 起静态站
│   └── README.md              # 静态部署详细说明
├── Dockerfile                 # 通用镜像：python:3.12-slim + server/ + ui/
├── docker-compose.yml         # 通用镜像的 compose（8080 + toqr-data 卷）
├── DEPLOY.md                  # fpk 构建过程与源码清单
├── CHANGELOG.md               # 版本更新日志
├── README.md                  # 本文件
└── .github/workflows/         # 打 tag 自动构建三平台二进制 + Docker 镜像并发 Release
```

构建产物不入库（见 `.gitignore`）：`fnos-qrcode/*.fpk`、`fnos-qrcode/fpk/app/ui/{index,qrscan}.html` 与 `vendor/` 都由脚本生成，安装包只发在 [Releases](../../releases)。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 说明

- **默认完全离线**：二维码由服务端按 QR Code Model 2 规范在本地编码成 PNG，不请求任何外部接口；页面也不引用任何 CDN 资源，NAS 不通外网同样可用，内容不出本机
- **解析器可切换**：输入区右上方可选「默认（本机编码）」/「备用1（浏览器内离线编码）」/「备用2（草料开放平台 API，服务端同源转发）」；只有显式选备用2 才会出外网
- 飞牛 fnOS 形态在 [`fnos-qrcode/`](fnos-qrcode/) 目录：`fpk/` 打应用包（`bash fnos-qrcode/fpk/build-fpk.sh`），`nginx/` + `html/` 是纯静态部署（页面由 `tools/sync-static.py` 从 `ui/` 生成）
- 服务端只有 `server/app.py` 一个文件；fpk 版另有一份副本 `fnos-qrcode/fpk/app/server/app.py`，改服务端逻辑时两处都要同步
