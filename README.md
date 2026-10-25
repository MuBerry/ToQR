# ToQR — 二维码批量生成器

自托管二维码批量生成服务：把一批文本/链接粘贴进去，一键批量出码，支持在线预览、打包下载、历史记录。**粘贴购物 App 的分享文案会自动识别其中的链接**（京东/淘宝/天猫/拼多多/抖音/小红书等，含无协议短链），只给链接出码；点码放大；手机上点链接直接跳转对应 App。

纯 Python 标准库实现，**零第三方依赖**。前端单文件，无构建步骤。

![首页](docs/screenshots/01-home.png)

## 快速开始

### 方式一：源码运行

```bash
git clone https://github.com/MuBerry/ToQR.git
cd ToQR
python server/app.py --port 8080
```

要求 Python 3.8+，无需 `pip install`。

### 方式二：二进制运行（Windows / macOS / Linux）

从 [Releases](../../releases) 下载对应平台的单文件可执行程序：

```bash
# Windows (PowerShell)
.\toqr-<版本>-windows-amd64.exe --port 8080

# macOS（Apple Silicon）/ Linux
chmod +x toqr-<版本>-<平台>
./toqr-<版本>-<平台> --port 8080
```

配置与数据自动落在可执行文件同目录的 `etc/`、`var/` 下；想换位置用环境变量 `TOQR_ETC_DIR` / `TOQR_VAR_DIR`。

> macOS 首次运行如被 Gatekeeper 拦截：`xattr -d com.apple.quarantine toqr-*` 后再执行。
> 二进制目前只提供 Apple Silicon（arm64）版；Intel Mac 可用 Docker 镜像（原生 amd64）或源码运行。

### 方式三：Docker

```bash
docker run -d --name toqr -p 8080:8080 -v toqr-data:/data ghcr.io/muberry/toqr:latest
```

或使用 docker-compose：

```bash
docker compose up -d
```

数据（`settings.json`、会话）持久化在 `toqr-data` 卷中。

## 功能特性

| 功能 | 说明 |
|---|---|
| 批量生成 | 多行文本按行出码，实时预览，打包下载 PNG |
| 链接识别 | 粘贴任意分享文案，自动提取链接并去重；「智能识别 / 按行原样」可切换 + 「提取链接」按钮 |
| 点码放大 | 点任意二维码弹出 800px 大图，可下载、复制、用浏览器打开 |
| 手机跳 App | 卡片下方「平台标签 + 短链」，手机点击直接通过 App 协议唤起，没装 App 再点一次走网页 |
| 同源代理 | 二维码图片经服务端 `/qr` 代理取回，跨域失败自动回退直连 |
| 用户认证 | 可选登录（PBKDF2 + HMAC Cookie），多账号、设备数限制、失败锁定、IP 黑白名单 |
| 历史记录 | 最近 50 条本地保存，缩略图回看、点击回填 |

![链接识别](docs/screenshots/06-links.png)

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
├── ui/                    # 前端静态资源
│   ├── index.html
│   └── images/            # 图标
├── docs/screenshots/      # 截图
├── Dockerfile
├── docker-compose.yml
├── CHANGELOG.md
└── .github/workflows/release.yml   # 打 tag 自动构建三平台二进制 + Docker 镜像并发 Release
```

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 说明

- 二维码图片来自公共生成服务（`api.2dcode.biz`），由服务端代理转发；自建或离线环境可自行替换
- 飞牛 fnOS 原生应用包（fpk）版本保留在 [`fnos-qrcode` 分支](../../tree/fnos-qrcode)
