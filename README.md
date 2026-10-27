# ToQR — 二维码批量生成器

自托管二维码批量生成服务：把一批文本/链接粘贴进去，一键批量出码，支持在线预览、打包下载、历史记录。**粘贴购物 App 的分享文案会自动识别其中的链接**（京东/淘宝/天猫/拼多多/抖音/小红书等，含无协议短链），只给链接出码；点码放大；手机上点链接直接跳转对应 App。

纯 Python 标准库实现，**零第三方依赖**，无构建步骤。默认**完全离线**：二维码在服务端本地编码，页面不引用任何外部资源；也可以按需切换成「浏览器内离线编码」或「草料开放平台」解析器。

![首页](docs/screenshots/01-home.png)

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

### 5. 飞牛 fnOS

| 形态 | 位置 | 用法 |
| --- | --- | --- |
| 应用包（fpk） | `fnos-qrcode/fnos-qrcode_<版本>.fpk` | 应用中心「手动安装」选这个文件；升级需先在应用中心卸载旧版 |
| 纯静态（nginx） | `fnos-qrcode/nginx/` + `fnos-qrcode/html/` | 挂到已有 Web 服务或反代后面 |
| 单文件静态页 | `fnos-index/index.html` | 丢到任意 Web 目录即可，二维码在浏览器内生成 |

应用包自带 Python 服务与 `/qrscan` 扫码页；纯静态形态没有后端，扫码页不可用（且仍需 HTTPS 才能调摄像头）。

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

![链接识别](docs/screenshots/06-links.png)

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
├── server/app.py          # 服务端（Python 标准库，单文件）
├── ui/                    # 前端静态资源（无构建步骤）
│   ├── index.html         # 出码页（/、/qr、/qr-shop 共用，行为由地址决定）
│   ├── qrscan.html        # 扫码识别页（/qrscan）
│   ├── vendor/            # toqr-qr.js（浏览器内离线编码）、zxing.min.js（扫码解码）
│   └── images/            # 图标
├── fnos-qrcode/           # 飞牛 fnOS 形态：fpk/ 应用包、nginx/ + html/ 静态部署、安装包
├── fnos-index/            # 单文件静态页（无后端）
├── docs/screenshots/      # 截图
├── DEPLOY.md              # fpk 构建说明
├── Dockerfile
├── docker-compose.yml
├── CHANGELOG.md
└── .github/workflows/release.yml   # 打 tag 自动构建三平台二进制 + Docker 镜像并发 Release
```

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 说明

- **默认完全离线**：二维码由服务端按 QR Code Model 2 规范在本地编码成 PNG，不请求任何外部接口；页面也不引用任何 CDN 资源，NAS 不通外网同样可用，内容不出本机
- **解析器可切换**：输入区右上方可选「默认（本机编码）」/「备用1（浏览器内离线编码）」/「备用2（草料开放平台 API，服务端同源转发）」；只有显式选备用2 才会出外网
- 飞牛 fnOS 形态在 [`fnos-qrcode/`](fnos-qrcode/) 目录：`fpk/` 打应用包（`bash fnos-qrcode/fpk/build-fpk.sh`），`nginx/` + `html/` 是纯静态部署；另有 [`fnos-index/`](fnos-index/) 单文件静态版
- 服务端只有 `server/app.py` 一个文件；fpk 版另有一份副本 `fnos-qrcode/fpk/app/server/app.py`，改服务端逻辑时两处都要同步
