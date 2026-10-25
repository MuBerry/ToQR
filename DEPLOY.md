# 构建说明（DEPLOY.md）

> 本文档输出「二维码批量生成器 · 飞牛 fnOS 版」的**构建源码**与**完整构建过程**，
> 供 GitHub 上传、代码评审与复现构建使用。
>
> 所有源码段落均由构建脚本从仓库文件直接读取写入，与仓库内容逐字一致。

仓库里共有两个部署形态，本文档聚焦第一个：

| 形态 | 位置 | 说明 |
| --- | --- | --- |
| **原生应用包（FPK）** | `fnos-qrcode/fpk/` → 产物 `fnos-qrcode/fnos-qrcode.fpk` | 本文档主题。可被飞牛「应用中心」安装，桌面出现图标，自带 Python 服务 |
| 静态部署包 | `fnos-qrcode/`（`docker-compose.yml` / `nginx/` / `html/`） | 纯静态页面，挂到已有 Web 服务或反向代理后面，见 `fnos-qrcode/README.md` |

分支划分：

| 分支 | 内容 |
| --- | --- |
| `main` | 开发主版本：单文件 `index.html`（二维码批量生成页面，63755 B） |
| `fnos-qrcode` | 在 `main` 之上叠加上述两种部署形态；本文档所在分支 |

---

## 目录

1. [项目概览](#1-项目概览)
2. [仓库结构](#2-仓库结构)
3. [构建源码清单](#3-构建源码清单)
4. [构建源码全文](#4-构建源码全文)
5. [构建过程](#5-构建过程)
6. [构建产物与校验](#6-构建产物与校验)
7. [安装与验证](#7-安装与验证)
8. [实现要点与踩坑记录](#8-实现要点与踩坑记录)
9. [上传到 GitHub](#9-上传到-github)

---

## 1. 项目概览

**目标**：把「二维码批量生成器」做成飞牛 fnOS 的原生应用 —— 在应用中心手动安装一个 `.fpk`，
装完桌面出现图标，点开就是二维码批量生成页面。

**硬性需求**

| 需求 | 实现方式 |
| --- | --- |
| 默认端口 `10001` | `wizard/install` 的 `initValue`（`manifest` 里的 `service_port` 仅作声明） |
| 安装时可指定端口 | 安装向导字段 `wizard_port` → `cmd/install_callback` 写入 `settings.json` |
| 装好后可调整端口 | 应用中心「运行设置」→ `wizard/config` → `cmd/config_callback` 写入并重启 |
| 安装时可开启用户认证 | 向导字段 `wizard_auth_enable` / `wizard_auth_user` / `wizard_auth_pass` |
| 装好后可调整认证 | 同一组字段被 `wizard/config` 复用，留空表示保持原值 |
| 换端口后不再误报「端口被占用」 | `checkport = false`，并且回调会把已安装 manifest 的 `service_port` 同步成真实端口 |

**技术选型**

- 服务端：**纯 Python3 标准库**（`http.server` + `hashlib` + `hmac`），无第三方依赖、不需要联网装包
- 前端：单文件 `index.html`（无框架、无构建），直接由服务端静态托管
- 二维码：由服务端代理转发草料开放平台公开接口（`/qr`），
  原因是该接口**只对 localhost 放行 CORS**，浏览器直连在局域网/域名下必定失败（详见 8.3 第 9 条）
- 打包：优先使用飞牛自带官方 `fnpack`；无 fnOS 环境时用仓库内置的等效打包器

**关键设计：端口为什么能做到「装时可指定、装后可改」**

- 真实端口只存在**一处**：配置目录下的 `settings.json`
- `app/ui/config` 里桌面入口端口写成 `${wizard_port}` —— 这是应用中心的**运行时变量**，
  桌面图标地址会自动跟随向导里的端口，**改端口不需要重建包**
- `cmd/main` 启动时用 `app.py --print-port` 读端口，用 `--check` 确认端口真的起来了
- **注意**：应用中心「启用」前的端口占用检查读的是**已安装 manifest 里的静态
  `service_port`**，不认 `${wizard_port}`。所以本包把 `checkport` 关掉，
  并在安装 / 改设置时用 `sync_manifest_port()` 把该字段同步成真实端口。
  这是 v1.0.1 修掉的那个坑，细节见 8.3 第 5 条。

---

## 2. 仓库结构

```
.
├── index.html                      # 开发主版本页面（唯一前端源，63755 B）
├── .gitattributes                  # 强制文本 LF（cmd/ 脚本在 Linux 上必须 LF）
├── .gitignore
├── DEPLOY.md                       # 本文件
└── fnos-qrcode/                    # 分支 fnos-qrcode 引入的部署目录
    ├── README.md                   # 静态部署（docker + nginx）指导
    ├── fnos-qrcode.fpk             # ★ FPK 构建产物，76840 B，可直接安装
    ├── index.html                  # 静态部署用的页面副本
    ├── docker-compose.yml          # 静态部署方案 A
    ├── html/fnos-qrcode/index.html # 静态部署容器挂载副本
    ├── nginx/                      # 静态部署方案 B（反代配置）
    ├── sync.sh                     # 主版本 → 静态副本 同步脚本
    └── fpk/                        # ★ FPK 工程（本文档主题）
        ├── manifest                # 应用清单
        ├── build-fpk.sh            # 一键构建脚本
        ├── ICON.PNG                # 64x64 列表图标
        ├── ICON_256.PNG            # 256x256 图标
        ├── app/
        │   ├── ui/
        │   │   ├── index.html      # 构建时从仓库根 index.html 同步而来
        │   │   ├── config          # 桌面入口配置（端口用 ${wizard_port}）
        │   │   └── images/         # icon_64.png / icon_256.png
        │   └── server/
        │       └── app.py          # 服务端：静态托管 + 可选登录认证
        ├── cmd/                    # 生命周期脚本（飞牛调用）
        │   ├── main                # start / stop / status
        │   ├── install_init、install_callback
        │   ├── config_init、config_callback
        │   ├── upgrade_init、upgrade_callback
        │   └── uninstall_init、uninstall_callback
        ├── config/
        │   ├── privilege           # 运行身份（package 专用用户）
        │   └── resource            # 资源声明
        ├── wizard/                 # 向导字段定义
        │   ├── install             # 安装向导
        │   ├── config              # 配置（设置）向导
        │   └── uninstall           # 卸载向导
        └── tools/
            ├── build_fpk.py        # 无 fnOS 环境时的等效打包器
            └── make_icons.py       # 图标生成器（构建期，需 Pillow）
```

---

## 3. 构建源码清单

FPK 工程共 21 个源文件，按职责归类：

| # | 文件 | 行数 | 大小 | 职责 |
| --- | --- | --- | --- | --- |
| 1 | `manifest` | 15 | 6967 B | 应用清单：appname/版本/入口/端口/平台 |
| 2 | `app/ui/config` | 16 | 321 B | 桌面入口 JSON，端口写 `${wizard_port}` |
| 3 | `config/privilege` | 7 | 107 B | 运行身份：`run-as: package` |
| 4 | `config/resource` | 5 | 43 B | 资源声明（数据共享为空） |
| 5 | `wizard/install` | 71 | 2205 B | 安装向导：端口 + 认证三项 |
| 6 | `wizard/config` | 176 | 5590 B | 设置向导（装后调整，同名字段） |
| 7 | `wizard/uninstall` | 24 | 733 B | 卸载向导：保留 / 删除配置 |
| 8 | `app/server/app.py` | 1797 | 78325 B | 服务端：静态托管 + 端口配置 + 可选认证 |
| 9 | `cmd/main` | 183 | 4816 B | start / stop / status 入口 |
| 10 | `cmd/install_init` | 46 | 1601 B | 安装前：Python、端口、密码校验 |
| 11 | `cmd/install_callback` | 147 | 5110 B | 安装后：落盘配置 + 修权限 |
| 12 | `cmd/config_init` | 5 | 131 B | 配置前：空操作 |
| 13 | `cmd/config_callback` | 192 | 6933 B | 设置保存后：写配置 + 按需重启 |
| 14 | `cmd/upgrade_init` | 12 | 237 B | 升级前：停旧进程 |
| 15 | `cmd/upgrade_callback` | 91 | 3118 B | 升级后：保留配置、补默认值 |
| 16 | `cmd/uninstall_init` | 12 | 272 B | 卸载前：停进程 |
| 17 | `cmd/uninstall_callback` | 27 | 893 B | 卸载后：按向导删除 / 保留配置 |
| 18 | `tools/build_fpk.py` | 228 | 7894 B | 备用打包器（无 fnpack 时） |
| 19 | `tools/make_icons.py` | 116 | 3822 B | 图标生成器（构建期工具） |
| 20 | `build-fpk.sh` | 65 | 2103 B | 一键构建脚本 |
| 21 | `app/ui/index.html` | — | 63755 B | 前端页面（构建时从根 `index.html` 同步，故不重复贴出） |

图标二进制：

| 文件 | 尺寸 | 大小 |
| --- | --- | --- |
| `ICON.PNG` | 64x64 | 5116 B |
| `ICON_256.PNG` | 256x256 | 17783 B |
| `app/ui/images/icon_64.png` | 64x64 | 5116 B |
| `app/ui/images/icon_256.png` | 256x256 | 17783 B |

---

## 4. 构建源码全文

### 4.1 `manifest` —— 应用清单

```ini
appname               = fnos-qrcode
version               = 1.2.1
display_name          = 二维码批量生成器
desc                  = 简单高效的二维码批量生成工具。在页面里每行输入一个内容，即可一次性生成对应的二维码，支持文本与网址。<br /><strong>主要能力</strong><br />· 批量生成：一行一条，一次最多 200 个<br />· 链接识别：把购物分享文案整段粘进来，自动只提取其中的链接（京东 / 淘宝 / 天猫 / 拼多多 / 抖音等）生成二维码，也可一键切回按行原样<br />· 多条导入：支持 <code>.txt</code> / <code>.csv</code> / <code>.tsv</code>，也可直接把文件拖到输入框<br />· 实时预览：生成后网格预览，点二维码可放大；每张可单张下载或一键打包成 zip<br />· 手机上点二维码下方的链接可直接唤起对应 App（淘宝等直接跳 App，不再经停网页登录页），没装 App 时可用「用浏览器打开」<br />· 最近记录：本地保存最近几次生成，点击即可重新载入<br /><strong>账号与登录安全</strong><br />· 多账号：最多 10 个账号，每个账号可单独设置「最多几台设备同时登录」或设为不限设备<br />· 登录限制：连续失败次数、锁定时长、IP 黑白名单均可配置<br />· 账号管理与「在线设备查看 / 踢出」在应用内「设置」页；基础项也可在应用中心「设置」里调整<br /><strong>关于端口与认证</strong><br />· 服务端口安装时填写，默认 <code>10001</code>，装好后可在应用中心「设置」里修改<br />· 用户认证可选：开启后访问本应用需先登录，安装时和装好后都能调整<br /><strong>数据说明</strong><br />二维码由草料开放平台公开接口实时生成，输入内容只在你自己的浏览器里处理，不会写盘、不会上传。
changelog              = 1.2.1：修复「手机点链接卡在要求登录的页面」。① 淘宝等平台的 https 短链在手机浏览器里会落到强制登录页，页面确实跳走了，原「先走网页、1.2 秒没被接管再回落 App 协议」的兜底永远轮不到，人被拦在登录页上。现改为点链接直接用 App 协议唤起（taobao:// / tmall:// / openapp.jdmobile:// / pinduoduo:// / snssdk1128:// 等），不再经停网页。② 唤起失败（多半没装 App）时提示「再点一次改用网页打开」，同一链接第二次点击直接走浏览器；换一张码或刷新页面后重新按 App 优先。③ 放大层新增「用浏览器打开」按钮，随时可手动走网页，不必先失败一次。④ 各平台协议补全为「协议 + 链接」形式（拼多多、抖音、小红书、B 站、快手等此前只打开 App 首页，现在带上目标地址）。<br />1.2.0：新增「粘贴即只出链接」。① 智能识别文本里的链接（京东 u.jd.com / item.jd.com / 3.cn、淘宝 m.tb.cn、天猫、拼多多、抖音等，也认不带 http(s):// 的平台短链），只给链接生成二维码，文案里的其他字自动忽略；② 输入框上方可在「智能识别 / 按行原样」之间切换，并实时提示识别到几个链接、哪些行会被忽略；③ 新增「提取链接」按钮，一键按链接生成；④ 点二维码可放大查看，大图按 800px 取图；⑤ 卡片下方显示「平台标签 + 短链」，手机上点击可直接唤起对应 App（先走 https 通用链接，未接管时回落到 App 协议）；⑥ 灯箱内可下载单张、复制链接，支持 Esc 与点击遮罩关闭。<br />1.1.0：多账号与可配置的登录安全。① 支持多账号（最多 10 个），每个账号可单独设置「最多几台设备同时登录」，也可设为「不限设备」。② 修复「登录框输入中文时报 ERR_EMPTY_RESPONSE」：比较用户名时对非 ASCII 字符会抛异常导致连接被静默关闭，现已修正并加了兜底（任何异常都返回 500 而不是断开）。③ 失败次数、锁定时长改为可配置。④ 新增登录 IP 黑白名单（只作用于登录提交，支持网段，回环始终放行）。⑤ 新增应用内「设置」页：账号增删改、登录安全参数、IP 名单、在线设备查看与踢出。<br />1.0.4：登录失败锁定逻辑调整。① 失败计数改为按「来源 IP」统一累计——同一 IP 换个用户名再试不会洗掉计数，达到 5 次即锁定该 IP。② 取消内网豁免：回环与私网来源同样受限流保护，局域网部署下锁定真正生效（如需放行特定网段，可在 LOGIN_LOCK_TRUSTED_PREFIXES 里配置）。③ 新增 120 秒连续失败窗口：两次失败间隔超过 120 秒则计数清零，避免隔几天累计 5 次被误锁。④ 锁定期间在登录按钮下方实时倒计时，秒数由服务端渲染，刷新后仍准确恢复。<br />1.0.3：安全加固（依据第三轮渗透测试报告）。① 登录失败计数改为按「来源 + 用户名」记账，攻击者用伪造用户名刷失败不再能锁死管理员登录入口；回环与私网来源直接不锁定，避免把自己锁在门外。② 登录错误提示去掉「还可尝试 N 次」，不再泄露剩余次数（锁定倒计时保留）。③ 登录表单加入一次性 CSRF 令牌。④ 补齐安全响应头（CSP / X-Frame-Options / Referrer-Policy 等）。⑤ 登出后对应会话立即失效。⑥ 拒绝 HTTP/0.9 简写请求，超大请求头返回 431，非法 Host 返回 400，超大 POST body 返回 413。<br />1.0.2：修复登录限流。① 锁定倒计时现在按秒实时递减，归零后自动恢复登录按钮（此前是服务端渲染一次的静态数字，看着一直不动）。② 修复「等一会儿刷新再登录仍显示 56 秒」：锁定期内的重复提交不再延长锁定窗口，锁定到期后失败计数清零。③ 同一 IP 连续失败 5 次后的锁定时间由 60 秒延长为 120 秒。<br />1.0.1：修复「启用时报端口被占用」。应用中心启动前会拿 manifest 里 service_port 这个静态端口去判断占用，而它不会跟随安装向导 / 应用设置里改的端口，于是在 10001 已被占用的机器上，改任何端口都报「端口被占用」。现在改为 checkport=false（不再用静态端口做占用检查），同时安装与改设置时会把已安装的 manifest 的 service_port 同步成真实端口。<br />1.0.0：首个版本。原生飞牛应用包，支持安装时指定服务端口（默认 10001）、可选开启用户认证，两者装好后均可在应用中心设置里调整。
platform              = all
source                = thirdparty
maintainer            = Firlsy
distributor           = Firlsy
service_port          = 10001
checkport             = false
ctl_stop              = true
install_type          =
desktop_uidir         = ui
desktop_applaunchname = fnos-qrcode.Application
```

### 4.2 `app/ui/config` —— 桌面入口

```json

{
  ".url": {
    "fnos-qrcode.Application": {
      "title": "二维码批量生成器",
      "icon": "images/icon_{0}.png",
      "type": "url",
      "protocol": "http",
      "port": "${wizard_port}",
      "url": "/",
      "allUsers": false,
      "control": {
        "accessPerm": "readonly"
      }
    }
  }
}
```

入口字段说明：

| 字段 | 值 | 含义 |
| --- | --- | --- |
| `title` | 二维码批量生成器 | 桌面图标文字 |
| `icon` | `images/icon_{0}.png` | `{0}` 由应用中心按尺寸替换（64 / 256） |
| `type` | `url` | 以 URL 方式打开 |
| `protocol` | `http` | 协议 |
| `port` | `${wizard_port}` | **运行时变量**，跟随向导里填的端口 |
| `url` | `/` | 打开路径 |
| `allUsers` | `false` | 入口与 `manifest` 的 `desktop_applaunchname` 对应 |
| `control.accessPerm` | `readonly` | 非管理员也可访问 |

> 入口 ID `fnos-qrcode.Application` 必须与 `manifest` 的
> `desktop_applaunchname = fnos-qrcode.Application` 完全一致。

### 4.3 `config/privilege` —— 运行身份

```json

{
  "defaults": {
    "run-as": "package"
  },
  "username": "fnos-qrcode",
  "groupname": "fnos-qrcode"
}
```

`run-as: package` 表示进程以应用专属用户 `fnos-qrcode` 运行（由飞牛在安装时创建），
而不是 root。`username` / `groupname` 同时用于给配置文件授权。

### 4.4 `config/resource` —— 资源声明

```json

{
  "data-share": {
    "shares": []
  }
}
```

> **注意**：`data-share` 必须是**对象**（`{"shares": []}`），写成数组会被 `fnpack` 直接拒绝。
> 详见 [踩坑记录](#8-实现要点与踩坑记录)。

### 4.5 `wizard/install` —— 安装向导

```json
[
  {
    "stepTitle": "服务端口",
    "items": [
      {
        "type": "text",
        "field": "wizard_port",
        "label": "服务端口",
        "helpText": "应用监听的端口，默认 10001。取值范围 1024~65535，且不能与其他应用冲突。装好后可在应用中心「设置」里修改。",
        "initValue": "10001",
        "rules": [
          {
            "required": true,
            "message": "请输入服务端口"
          },
          {
            "pattern": "^[0-9]{4,5}$",
            "message": "端口号必须是 4~5 位数字（1024~65535）"
          }
        ]
      }
    ]
  },
  {
    "stepTitle": "用户认证（可选）",
    "items": [
      {
        "type": "select",
        "field": "wizard_auth_enable",
        "label": "是否开启用户认证",
        "helpText": "开启后打开本应用需要先输入用户名和密码；不开启则局域网内任何设备都能直接访问。装好后可以随时在应用中心「设置」里调整。",
        "initValue": "off",
        "options": [
          {
            "label": "不开启（局域网内直接访问）",
            "value": "off"
          },
          {
            "label": "开启（需要登录后访问）",
            "value": "on"
          }
        ]
      },
      {
        "type": "text",
        "field": "wizard_auth_user",
        "label": "登录用户名",
        "helpText": "仅在开启用户认证时生效。3~32 位，可含字母、数字、下划线、点和减号。默认 admin。",
        "initValue": "admin",
        "rules": [
          {
            "pattern": "^$|^[A-Za-z0-9_.-]{3,32}$",
            "message": "用户名需为 3~32 位字母、数字或 _ . -"
          }
        ]
      },
      {
        "type": "password",
        "field": "wizard_auth_pass",
        "label": "登录密码",
        "helpText": "仅在开启用户认证时生效，至少 6 位。密码只以加盐哈希保存在 NAS 本地，不会写入日志、不会上传。",
        "rules": [
          {
            "pattern": "^$|^.{6,64}$",
            "message": "密码长度需为 6~64 位"
          }
        ]
      }
    ]
  }
]
```

### 4.6 `wizard/config` —— 设置（装后调整）向导

```json
[
  {
    "stepTitle": "服务端口",
    "items": [
      {
        "type": "text",
        "field": "wizard_port",
        "label": "服务端口",
        "helpText": "修改后应用会自动重启并改用新端口，桌面图标入口同步生效。取值范围 1024~65535。",
        "initValue": "10001",
        "rules": [
          {
            "required": true,
            "message": "请输入服务端口"
          },
          {
            "pattern": "^[0-9]{4,5}$",
            "message": "端口号必须是 4~5 位数字（1024~65535）"
          }
        ]
      }
    ]
  },
  {
    "stepTitle": "用户认证（可选）",
    "items": [
      {
        "type": "select",
        "field": "wizard_auth_enable",
        "label": "是否开启用户认证",
        "helpText": "开启后打开本应用需要先输入用户名和密码；不开启则局域网内任何设备都能直接访问。",
        "initValue": "off",
        "options": [
          {
            "label": "不开启（局域网内直接访问）",
            "value": "off"
          },
          {
            "label": "开启（需要登录后访问）",
            "value": "on"
          }
        ]
      },
      {
        "type": "text",
        "field": "wizard_auth_user",
        "label": "登录用户名",
        "helpText": "仅在开启用户认证时生效。留空表示保持当前用户名不变。",
        "initValue": "admin",
        "rules": [
          {
            "pattern": "^$|^[A-Za-z0-9_.-]{3,32}$",
            "message": "用户名需为 3~32 位字母、数字或 _ . -"
          }
        ]
      },
      {
        "type": "password",
        "field": "wizard_auth_pass",
        "label": "登录密码",
        "helpText": "仅在开启用户认证时生效。留空表示保持当前密码不变；填写则覆盖为新密码（至少 6 位）。",
        "rules": [
          {
            "pattern": "^$|^.{6,64}$",
            "message": "密码长度需为 6~64 位"
          }
        ]
      }
    ]
  },
  {
    "stepTitle": "登录安全（可选）",
    "items": [
      {
        "type": "text",
        "field": "wizard_max_fail",
        "label": "连续失败次数",
        "helpText": "同一 IP 在锁定时长内连续登录失败达到该次数，即锁定该 IP。取值 1~999。留空表示保持当前设置不变。",
        "initValue": "",
        "rules": [
          {
            "pattern": "^$|^[0-9]{1,3}$",
            "message": "请填写 1~999 的整数，或留空保持不变"
          }
        ]
      },
      {
        "type": "text",
        "field": "wizard_lock_seconds",
        "label": "锁定时长（秒）",
        "helpText": "被锁定后需要等待多少秒才能再次尝试登录。取值 5~86400（最长 24 小时）。留空表示保持当前设置不变。",
        "initValue": "",
        "rules": [
          {
            "pattern": "^$|^[0-9]{1,5}$",
            "message": "请填写 5~86400 的整数，或留空保持不变"
          }
        ]
      },
      {
        "type": "select",
        "field": "wizard_device_limit",
        "label": "限制每个账号的在线设备数",
        "helpText": "开启后每个账号最多只能有指定数量的设备同时登录，达到上限时新的登录会被拒绝（可在应用内「设置」页踢掉旧设备释放名额）。",
        "initValue": "keep",
        "options": [
          {
            "label": "保持不变",
            "value": "keep"
          },
          {
            "label": "开启",
            "value": "on"
          },
          {
            "label": "关闭（不限设备数）",
            "value": "off"
          }
        ]
      },
      {
        "type": "text",
        "field": "wizard_max_devices",
        "label": "新账号默认设备上限",
        "helpText": "新增账号时默认沿用的设备数上限。取值 1~999，留空表示保持不变；单个账号的实际上限可在应用内「设置」页单独调整。",
        "initValue": "",
        "rules": [
          {
            "pattern": "^$|^[0-9]{1,3}$",
            "message": "请填写 1~999 的整数，或留空保持不变"
          }
        ]
      }
    ]
  },
  {
    "stepTitle": "IP 名单（可选）",
    "items": [
      {
        "type": "select",
        "field": "wizard_ip_filter",
        "label": "启用登录 IP 黑白名单",
        "helpText": "只作用于「提交登录」这一步，浏览页面不受影响；回环地址（本机）始终放行，避免把自己关在外面。",
        "initValue": "keep",
        "options": [
          {
            "label": "保持不变",
            "value": "keep"
          },
          {
            "label": "开启",
            "value": "on"
          },
          {
            "label": "关闭",
            "value": "off"
          }
        ]
      },
      {
        "type": "text",
        "field": "wizard_ip_blacklist",
        "label": "IP 黑名单",
        "helpText": "命中即拒绝登录。支持单个 IP 与网段（如 192.168.1.50、10.0.0.0/8），多个用逗号分隔。留空表示保持不变；填一个空格可清空。",
        "initValue": ""
      },
      {
        "type": "text",
        "field": "wizard_ip_whitelist",
        "label": "IP 白名单",
        "helpText": "填写后只有名单内的来源可以提交登录（回环除外）；黑名单优先级更高。留空表示保持不变；填一个空格可清空。",
        "initValue": ""
      }
    ]
  }
]
```

`wizard/config` 与 `wizard/install` **使用完全相同的字段名**，因此：
向导值在升级、重装之间可以平滑迁移；`cmd/config_callback` 里「留空 = 保持原值」的语义也因此成立。

字段与落到 `settings.json` 的映射：

| 向导字段 | 类型 | → `settings.json` |
| --- | --- | --- |
| `wizard_port` | text | `port`（整数） |
| `wizard_auth_enable` | select `on`/`off` | `auth.enabled`（布尔） |
| `wizard_auth_user` | text | `accounts[].user`（唯一账号时是重命名，否则新增） |
| `wizard_auth_pass` | password | `accounts[].password`（**PBKDF2 哈希**，非明文） |
| `wizard_max_fail` | text（留空=不变） | `login_security.max_fail` |
| `wizard_lock_seconds` | text（留空=不变） | `login_security.lock_seconds` |
| `wizard_device_limit` | select `keep`/`on`/`off` | `login_security.device_limit_enabled` |
| `wizard_max_devices` | text（留空=不变） | `login_security.default_max_devices` |
| `wizard_ip_filter` | select `keep`/`on`/`off` | `login_security.ip_filter_enabled` |
| `wizard_ip_blacklist` | text（留空=不变，空格=清空） | `login_security.ip_blacklist`（数组） |
| `wizard_ip_whitelist` | text（留空=不变，空格=清空） | `login_security.ip_whitelist`（数组） |

> v1.1.0 新增的三组字段全部遵循「**留空 / keep = 保持原值**」，
> 这样设置向导不必回填当前值也能安全保存。
> 账号的**列表级操作**（新增第 2 个以后的账号、逐个改设备上限、踢设备）静态表单表达不了，
> 统一放在**应用内 `/settings` 页**。

### 4.7 `wizard/uninstall` —— 卸载向导

```json

[
  {
    "stepTitle": "卸载选项",
    "items": [
      {
        "type": "select",
        "field": "wizard_data_action",
        "label": "应用配置",
        "helpText": "这里指的是本应用的配置文件（服务端口、登录用户名和密码哈希）。二维码内容只在浏览器里处理、不会写盘，所以两种选择都不会影响你的数据。",
        "initValue": "keep",
        "options": [
          {
            "label": "保留配置（推荐，重新安装后端口和账号还在）",
            "value": "keep"
          },
          {
            "label": "删除配置（彻底清除端口和登录设置）",
            "value": "delete"
          }
        ]
      }
    ]
  }
]
```

### 4.8 `app/server/app.py` —— 服务端

纯标准库实现。命令行接口：

```
python3 app.py                      启动服务（端口读 settings.json）
python3 app.py --port 8080          以指定端口启动（调试用，不写配置）
python3 app.py --print-port         打印当前配置端口
python3 app.py --check              探测当前端口是否在监听（供 cmd/main status 用）
python3 app.py --set [选项]          合并写入 settings.json
    --port N            设置端口
    --auth on|off       设置是否开启用户认证
    --user U            设置登录用户名（留空表示不改）
    --pass P            设置登录密码（留空表示不改）
    --pass-stdin        从标准输入读密码（避免出现在 ps 命令行里）
```

HTTP 路由：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/login` | 认证开启且未登录 → 登录页；否则跳 `/` |
| POST | `/login` | 校验账号密码，成功下发 HMAC 签名 Cookie |
| GET | `/logout` | 清 Cookie 并跳转 |
| GET | `/*` | 静态资源；认证开启且未登录 → `302 /login` |
| GET | `/favicon.ico` | 映射到 `images/icon_64.png` |

```python
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
APPVERSION = '1.2.1'
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
```

### 4.9 `cmd/main` —— 启停入口

```bash

#!/bin/bash
# fnos-qrcode 生命周期入口：start / stop / status
#
# 约定（见飞牛应用开发文档）：
#   start  成功返回 0，失败返回 1
#   stop   成功返回 0，失败返回 1
#   status 运行中返回 0，未运行返回 3
#   未知动作返回 1
set -uo pipefail

# ---------- 路径 ----------
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(dirname "$SELF_DIR")"
: "${TRIM_APPDEST:=$PKG_DIR/app}"
: "${TRIM_PKGVAR:=$PKG_DIR/var}"
: "${TRIM_PKGETC:=$PKG_DIR/etc}"
export TRIM_APPDEST TRIM_PKGVAR TRIM_PKGETC

APP_PY="$TRIM_APPDEST/server/app.py"
PIDFILE="$TRIM_PKGVAR/app.pid"
LOGFILE="$TRIM_PKGVAR/app.log"

mkdir -p "$TRIM_PKGVAR" "$TRIM_PKGETC" 2>/dev/null || true

fail() {
  echo "$1" >&2
  [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "fnos-qrcode: $1" >>"$TRIM_TEMP_LOGFILE" 2>/dev/null
  exit 1
}

# ---------- 找 Python ----------
find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  # 应用中心安装的 Python 运行时
  for candidate in /var/apps/python312/target/bin/python3 /var/apps/python312/target/bin/python; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# ---------- 进程探测 ----------
# $TRIM_APPDEST 通常是 /var/apps/<app>/target 这类软链，而进程命令行里记的是解析后的真实路径，
# 所以两种形态都要能匹配到，否则 status/stop 会误判为「未运行」。
APP_PY_REAL=""
if command -v readlink >/dev/null 2>&1; then
  APP_PY_REAL="$(readlink -f "$APP_PY" 2>/dev/null || true)"
  [ "$APP_PY_REAL" = "$APP_PY" ] && APP_PY_REAL=""
fi

# 通过命令行匹配 app.py，避免依赖 pidfile 的正确性（setsid / PID 复用都可能让它失真）
# 额外要求命令行里出现 python，否则 awk/ps 自身的命令行也会被匹配到
list_pids() {
  local out
  out="$(ps -eo pid=,args= 2>/dev/null | awk -v a="$APP_PY" -v b="$APP_PY_REAL" '
    $0 ~ /python/ && ((a != "" && index($0, a) > 0) || (b != "" && index($0, b) > 0)) { print $1 }')"
  if [ -z "$out" ]; then
    out="$(ps -ef 2>/dev/null | awk -v a="$APP_PY" -v b="$APP_PY_REAL" '
      $0 ~ /python/ && ((a != "" && index($0, a) > 0) || (b != "" && index($0, b) > 0)) { print $2 }')"
  fi
  printf '%s\n' "$out" | sed '/^[[:space:]]*$/d'
}

is_running() {
  local pid
  for pid in $(list_pids); do
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

current_port() {
  "$1" "$APP_PY" --print-port 2>/dev/null | tr -d '[:space:]'
}

# ---------- 动作 ----------
do_start() {
  if is_running; then
    echo "fnos-qrcode 已在运行"
    return 0
  fi

  [ -f "$APP_PY" ] || fail "找不到服务端文件：$APP_PY"

  local py
  py="$(find_python)" || fail "未找到可用的 Python3（需要 3.6 及以上）"

  local port
  port="$(current_port "$py")"
  [ -n "$port" ] || fail "无法读取服务端口配置"

  echo "启动 fnos-qrcode：port=$port python=$py"

  if command -v setsid >/dev/null 2>&1; then
    setsid "$py" "$APP_PY" >>"$LOGFILE" 2>&1 </dev/null &
  else
    nohup "$py" "$APP_PY" >>"$LOGFILE" 2>&1 </dev/null &
  fi
  local launcher_pid=$!

  # 等待端口真正就绪（最多 15 秒）
  local i
  for i in $(seq 1 30); do
    sleep 0.5
    if "$py" "$APP_PY" --check >/dev/null 2>&1; then
      list_pids >"$PIDFILE" 2>/dev/null || echo "$launcher_pid" >"$PIDFILE"
      echo "fnos-qrcode 已启动（port=$port）"
      return 0
    fi
    if ! kill -0 "$launcher_pid" 2>/dev/null && ! is_running; then
      break
    fi
  done

  echo "fnos-qrcode 启动失败，最近日志：" >&2
  tail -n 20 "$LOGFILE" >&2 2>/dev/null || true
  fail "服务未能在端口 $port 上就绪（端口可能被占用）"
}

do_stop() {
  local pids
  pids="$(list_pids)"
  if [ -z "$pids" ]; then
    rm -f "$PIDFILE" 2>/dev/null || true
    echo "fnos-qrcode 未在运行"
    return 0
  fi

  local pid
  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || true
  done

  local i
  for i in $(seq 1 20); do
    sleep 0.5
    is_running || break
  done

  if is_running; then
    for pid in $(list_pids); do
      kill -KILL "$pid" 2>/dev/null || true
    done
    sleep 1
  fi

  rm -f "$PIDFILE" 2>/dev/null || true
  if is_running; then
    fail "无法停止 fnos-qrcode 进程"
  fi
  echo "fnos-qrcode 已停止"
  return 0
}

do_status() {
  if is_running; then
    echo "running"
    return 0
  fi
  echo "stopped"
  return 3
}

case "${1:-}" in
  start)  do_start ;;
  stop)   do_stop ;;
  status) do_status ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 1
    ;;
esac
```

### 4.10 生命周期脚本

飞牛会在安装 / 配置 / 升级 / 卸载的不同阶段调用这些脚本，向导里填的值以**同名环境变量**传入
（如 `wizard_port`）。

#### `cmd/install_init` —— 安装前校验

```bash

#!/bin/bash
# 安装前：提前校验运行环境与安装参数，问题在安装阶段就暴露出来
set -uo pipefail

fail() {
  echo "fnos-qrcode[install_init] 失败：$1" >&2
  [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "fnos-qrcode 安装失败：$1" >>"$TRIM_TEMP_LOGFILE" 2>/dev/null
  exit 1
}

# 1. Python3
found=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; then
    found="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$found" ]; then
  for candidate in /var/apps/python312/target/bin/python3 /var/apps/python312/target/bin/python; do
    [ -x "$candidate" ] && { found="$candidate"; break; }
  done
fi
[ -n "$found" ] || fail "系统未找到 Python3（需要 3.6 及以上），无法运行本应用"

# 2. 端口
PORT="${wizard_port:-10001}"
PORT="$(printf '%s' "$PORT" | tr -d '[:space:]')"
case "$PORT" in
  ''|*[!0-9]*) fail "服务端口必须是数字，收到：${wizard_port:-（空）}" ;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  fail "服务端口必须在 1024~65535 之间，收到：$PORT"
fi

# 3. 认证参数
case "$(printf '%s' "${wizard_auth_enable:-off}" | tr 'A-Z' 'a-z')" in
  on|true|1|yes|enable|enabled)
    [ -n "${wizard_auth_pass:-}" ] || fail "已选择开启用户认证，但没有填写登录密码"
    [ "${#wizard_auth_pass}" -ge 6 ] || fail "登录密码至少需要 6 位"
    ;;
esac

echo "fnos-qrcode[install_init] 环境检查通过（python=$found port=$PORT）"
exit 0
```

#### `cmd/install_callback` —— 安装后落盘配置

```bash
#!/bin/bash
# 安装完成后：准备运行目录，并把安装向导里填的「服务端口 / 用户认证」落到 settings.json
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(dirname "$SELF_DIR")"
: "${TRIM_APPDEST:=$PKG_DIR/app}"
: "${TRIM_PKGVAR:=$PKG_DIR/var}"
: "${TRIM_PKGETC:=$PKG_DIR/etc}"
: "${TRIM_PKGTMP:=$PKG_DIR/tmp}"
export TRIM_APPDEST TRIM_PKGVAR TRIM_PKGETC TRIM_PKGTMP

APP_PY="$TRIM_APPDEST/server/app.py"
SETTINGS="$TRIM_PKGETC/settings.json"
APP_USER="fnos-qrcode"
APP_GROUP="fnos-qrcode"

log() {
  echo "fnos-qrcode[install] $1"
  [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "fnos-qrcode: $1" >>"$TRIM_TEMP_LOGFILE" 2>/dev/null
  return 0
}

fail() {
  echo "fnos-qrcode[install] 失败：$1" >&2
  [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "fnos-qrcode 安装失败：$1" >>"$TRIM_TEMP_LOGFILE" 2>/dev/null
  exit 1
}

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in /var/apps/python312/target/bin/python3 /var/apps/python312/target/bin/python; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

# ---------- manifest 端口同步 ----------
# 应用中心在「启用 / 启动」前会检查 manifest 里 service_port 声明的端口是否被占用。
# 该字段是打包时的静态值，不跟随安装向导或「应用设置」里改的端口，
# 于是换了端口之后仍会按旧端口报「端口被占用」。这里把已安装的 manifest 同步成真实端口。
MANIFEST="$(dirname "$TRIM_APPDEST")/manifest"

read_manifest_port() {
  sed -n 's/^service_port[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$1" 2>/dev/null | head -1
}

sync_manifest_port() {
  local port="$1" cur tmp
  [ -n "$port" ] || return 0
  if [ ! -f "$MANIFEST" ]; then
    log "未找到 $MANIFEST，跳过 manifest 端口同步"
    return 0
  fi

  cur="$(read_manifest_port "$MANIFEST")"
  if [ "$cur" = "$port" ]; then
    log "manifest 端口已是 $port，无需同步"
    return 0
  fi

  if grep -q '^service_port' "$MANIFEST" 2>/dev/null; then
    tmp="$MANIFEST.tmp.$$"
    if sed "s|^service_port[[:space:]]*=.*|service_port          = ${port}|" "$MANIFEST" >"$tmp" 2>/dev/null; then
      # 用 cat 回写以保留原 inode 与权限
      cat "$tmp" >"$MANIFEST" 2>/dev/null || true
    fi
    rm -f "$tmp" 2>/dev/null || true
  else
    printf '\nservice_port          = %s\n' "$port" >>"$MANIFEST" 2>/dev/null || true
  fi

  cur="$(read_manifest_port "$MANIFEST")"
  if [ "$cur" = "$port" ]; then
    log "已同步 manifest 的 service_port = $port（应用中心按此端口做占用检查）"
  else
    log "警告：无法写入 $MANIFEST，应用中心仍会按旧端口检查；如提示端口被占用，请先确认该端口空闲"
  fi
  return 0
}

# ---------- 1. 目录 ----------
mkdir -p "$TRIM_PKGVAR" "$TRIM_PKGETC" "$TRIM_PKGTMP" 2>/dev/null || fail "创建运行目录失败"
log "运行目录就绪：$TRIM_PKGVAR"

# ---------- 2. 解析并校验安装向导参数 ----------
PORT="${wizard_port:-10001}"
PORT="$(printf '%s' "$PORT" | tr -d '[:space:]')"
case "$PORT" in
  ''|*[!0-9]*) fail "服务端口必须是数字，收到：${wizard_port:-（空）}" ;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  fail "服务端口必须在 1024~65535 之间，收到：$PORT"
fi

case "$(printf '%s' "${wizard_auth_enable:-off}" | tr 'A-Z' 'a-z')" in
  on|true|1|yes|enable|enabled) AUTH="on" ;;
  *)                             AUTH="off" ;;
esac

AUTH_USER="${wizard_auth_user:-admin}"
AUTH_PASS="${wizard_auth_pass:-}"

if [ "$AUTH" = "on" ]; then
  [ -n "$AUTH_PASS" ] || fail "已选择开启用户认证，但没有填写登录密码"
  if [ "${#AUTH_PASS}" -lt 6 ]; then
    fail "登录密码至少需要 6 位"
  fi
fi

# ---------- 3. 落盘配置 ----------
PY="$(find_python)" || fail "未找到可用的 Python3（需要 3.6 及以上）"

ARGS=(--set --port "$PORT" --auth "$AUTH")
[ -n "$AUTH_USER" ] && ARGS+=(--user "$AUTH_USER")

if [ -n "$AUTH_PASS" ]; then
  # 用管道传密码，避免出现在进程命令行里
  printf '%s' "$AUTH_PASS" | "$PY" "$APP_PY" "${ARGS[@]}" --pass-stdin >/dev/null || fail "写入配置失败"
else
  "$PY" "$APP_PY" "${ARGS[@]}" >/dev/null || fail "写入配置失败"
fi

# ---------- 4. 权限 ----------
# 生命周期脚本可能以 root 运行，而应用以 package 身份运行，需要把配置交给应用用户
if [ -f "$SETTINGS" ]; then
  if id "$APP_USER" >/dev/null 2>&1 \
     && chown "$APP_USER:$APP_GROUP" "$SETTINGS" 2>/dev/null; then
    chmod 600 "$SETTINGS" 2>/dev/null || true
  else
    chmod 644 "$SETTINGS" 2>/dev/null || true
  fi
fi

log "配置完成：端口=$PORT 用户认证=$AUTH 用户名=$AUTH_USER 配置文件=$SETTINGS"

# ---------- 5. 让应用中心的端口检查对准真实端口 ----------
sync_manifest_port "$PORT"

exit 0
```

#### `cmd/config_init` —— 配置前（空操作，保持幂等）

```bash

#!/bin/bash
# 配置向导调起前：无需准备动作，保持幂等
set -uo pipefail
echo "fnos-qrcode[config_init] ok"
exit 0
```

#### `cmd/config_callback` —— 设置保存后写配置并重启

```bash
#!/bin/bash
# 应用中心「设置」保存后：把新的端口 / 用户认证写入配置，并按需重启应用
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(dirname "$SELF_DIR")"
: "${TRIM_APPDEST:=$PKG_DIR/app}"
: "${TRIM_PKGVAR:=$PKG_DIR/var}"
: "${TRIM_PKGETC:=$PKG_DIR/etc}"
export TRIM_APPDEST TRIM_PKGVAR TRIM_PKGETC

APP_PY="$TRIM_APPDEST/server/app.py"
SETTINGS="$TRIM_PKGETC/settings.json"
APP_USER="fnos-qrcode"
APP_GROUP="fnos-qrcode"

log() {
  echo "fnos-qrcode[config] $1"
  [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "fnos-qrcode: $1" >>"$TRIM_TEMP_LOGFILE" 2>/dev/null
  return 0
}

fail() {
  echo "fnos-qrcode[config] 失败：$1" >&2
  [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "fnos-qrcode 设置失败：$1" >>"$TRIM_TEMP_LOGFILE" 2>/dev/null
  exit 1
}

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in /var/apps/python312/target/bin/python3 /var/apps/python312/target/bin/python; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

# ---------- manifest 端口同步 ----------
# 应用中心在「启用 / 启动」前会检查 manifest 里 service_port 声明的端口是否被占用。
# 该字段是打包时的静态值，不跟随安装向导或「应用设置」里改的端口，
# 于是换了端口之后仍会按旧端口报「端口被占用」。这里把已安装的 manifest 同步成真实端口。
MANIFEST="$(dirname "$TRIM_APPDEST")/manifest"

read_manifest_port() {
  sed -n 's/^service_port[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$1" 2>/dev/null | head -1
}

sync_manifest_port() {
  local port="$1" cur tmp
  [ -n "$port" ] || return 0
  if [ ! -f "$MANIFEST" ]; then
    log "未找到 $MANIFEST，跳过 manifest 端口同步"
    return 0
  fi

  cur="$(read_manifest_port "$MANIFEST")"
  if [ "$cur" = "$port" ]; then
    log "manifest 端口已是 $port，无需同步"
    return 0
  fi

  if grep -q '^service_port' "$MANIFEST" 2>/dev/null; then
    tmp="$MANIFEST.tmp.$$"
    if sed "s|^service_port[[:space:]]*=.*|service_port          = ${port}|" "$MANIFEST" >"$tmp" 2>/dev/null; then
      # 用 cat 回写以保留原 inode 与权限
      cat "$tmp" >"$MANIFEST" 2>/dev/null || true
    fi
    rm -f "$tmp" 2>/dev/null || true
  else
    printf '\nservice_port          = %s\n' "$port" >>"$MANIFEST" 2>/dev/null || true
  fi

  cur="$(read_manifest_port "$MANIFEST")"
  if [ "$cur" = "$port" ]; then
    log "已同步 manifest 的 service_port = $port（应用中心按此端口做占用检查）"
  else
    log "警告：无法写入 $MANIFEST，应用中心仍会按旧端口检查；如提示端口被占用，请先确认该端口空闲"
  fi
  return 0
}

mkdir -p "$TRIM_PKGVAR" "$TRIM_PKGETC" 2>/dev/null || true

# ---------- 1. 校验 ----------
PORT="${wizard_port:-}"
if [ -n "$PORT" ]; then
  PORT="$(printf '%s' "$PORT" | tr -d '[:space:]')"
  case "$PORT" in
    ''|*[!0-9]*) fail "服务端口必须是数字，收到：$PORT" ;;
  esac
  if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
    fail "服务端口必须在 1024~65535 之间，收到：$PORT"
  fi
fi

case "$(printf '%s' "${wizard_auth_enable:-}" | tr 'A-Z' 'a-z')" in
  on|true|1|yes|enable|enabled) AUTH="on" ;;
  off|false|0|no|disable|disabled) AUTH="off" ;;
  *) AUTH="" ;;   # 未提供则保持原值
esac

AUTH_USER="${wizard_auth_user:-}"
AUTH_PASS="${wizard_auth_pass:-}"

if [ "$AUTH" = "on" ] && [ -n "$AUTH_PASS" ] && [ "${#AUTH_PASS}" -lt 6 ]; then
  fail "登录密码至少需要 6 位"
fi

# ---------- 1.5 登录安全与 IP 名单（留空 / keep 表示保持原值） ----------
SEC_ARGS=()

num_arg() {   # $1=值 $2=最小 $3=最大 $4=参数名 $5=中文名
  local v
  v="$(printf '%s' "$1" | tr -d '[:space:]')"
  [ -z "$v" ] && return 0
  case "$v" in
    *[!0-9]*) fail "$5 必须是数字，收到：$1" ;;
  esac
  if [ "$v" -lt "$2" ] || [ "$v" -gt "$3" ]; then
    fail "$5 必须在 $2~$3 之间，收到：$1"
  fi
  SEC_ARGS+=("$4" "$v")
}

onoff() {     # $1=值 $2=参数名
  case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" in
    on|true|1|yes|enable|enabled) SEC_ARGS+=("$2" "on") ;;
    off|false|0|no|disable|disabled) SEC_ARGS+=("$2" "off") ;;
    *) return 0 ;;
  esac
}

num_arg "${wizard_max_fail:-}"     1 999   --max-fail     "连续失败次数"
num_arg "${wizard_lock_seconds:-}" 5 86400 --lock-seconds "锁定时长"
num_arg "${wizard_max_devices:-}"  1 999   --max-devices  "设备数上限"
onoff   "${wizard_device_limit:-}" --device-limit
onoff   "${wizard_ip_filter:-}"    --ip-filter

[ -n "${wizard_ip_blacklist:-}" ] && SEC_ARGS+=(--ip-blacklist "$wizard_ip_blacklist")
[ -n "${wizard_ip_whitelist:-}" ] && SEC_ARGS+=(--ip-whitelist "$wizard_ip_whitelist")

# bash 3/4 兼容：空数组在 set -u 下直接展开会报错，统一用这个写法
EXTRA=(${SEC_ARGS[@]+"${SEC_ARGS[@]}"})

# ---------- 2. 写入 ----------
PY="$(find_python)" || fail "未找到可用的 Python3（需要 3.6 及以上）"

ARGS=(--set)
[ -n "$PORT" ] && ARGS+=(--port "$PORT")
[ -n "$AUTH" ] && ARGS+=(--auth "$AUTH")
[ -n "$AUTH_USER" ] && ARGS+=(--user "$AUTH_USER")

if [ -n "$AUTH_PASS" ]; then
  printf '%s' "$AUTH_PASS" | "$PY" "$APP_PY" "${ARGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} --pass-stdin >/dev/null || fail "写入配置失败（若开启认证，请确认已设置过登录密码）"
else
  "$PY" "$APP_PY" "${ARGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} >/dev/null || fail "写入配置失败（若开启认证，请确认已设置过登录密码）"
fi

if [ -f "$SETTINGS" ]; then
  if id "$APP_USER" >/dev/null 2>&1 \
     && chown "$APP_USER:$APP_GROUP" "$SETTINGS" 2>/dev/null; then
    chmod 600 "$SETTINGS" 2>/dev/null || true
  else
    chmod 644 "$SETTINGS" 2>/dev/null || true
  fi
fi

# ---------- 3. 让应用中心的端口检查对准真实端口 ----------
# 没传端口说明端口未变，取当前生效的端口再同步
EFFECTIVE_PORT="$PORT"
[ -n "$EFFECTIVE_PORT" ] || EFFECTIVE_PORT="$("$PY" "$APP_PY" --print-port 2>/dev/null | tr -d '[:space:]')"
sync_manifest_port "$EFFECTIVE_PORT"

# ---------- 4. 需要时重启 ----------
# 端口与认证都是启动时读取的，改动后必须重启才生效
if "$SELF_DIR/main" status >/dev/null 2>&1; then
  log "检测到应用正在运行，重启以应用新配置"
  "$SELF_DIR/main" stop >/dev/null 2>&1 || true
  "$SELF_DIR/main" start >/dev/null 2>&1 || fail "配置已保存，但重启失败，请在应用中心手动启动"
  log "已重启完成"
else
  log "应用当前未运行，配置将在下次启动时生效"
fi

log "设置已保存：端口=${PORT:-未变} 用户认证=${AUTH:-未变} 高级项参数 ${#EXTRA[@]} 个"
exit 0
```

#### `cmd/upgrade_init` —— 升级前停旧进程

```bash

#!/bin/bash
# 升级前：停掉旧版本进程
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -x "$SELF_DIR/main" ]; then
  "$SELF_DIR/main" stop || true
fi

echo "fnos-qrcode[upgrade_init] 已停止旧版本"
exit 0
```

#### `cmd/upgrade_callback` —— 升级后保留配置

```bash
#!/bin/bash
# 升级后：补齐运行目录、保证配置文件可解析；绝不覆盖用户已改过的端口与认证设置
set -uo pipefail

PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
: "${TRIM_APPDEST:=$PKG_DIR/app}"
: "${TRIM_PKGVAR:=$PKG_DIR/var}"
: "${TRIM_PKGETC:=$PKG_DIR/etc}"
export TRIM_APPDEST TRIM_PKGVAR TRIM_PKGETC

APP_PY="$TRIM_APPDEST/server/app.py"
SETTINGS="$TRIM_PKGETC/settings.json"

mkdir -p "$TRIM_PKGVAR" "$TRIM_PKGETC" 2>/dev/null || true

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; then
      command -v "$candidate"; return 0
    fi
  done
  for candidate in /var/apps/python312/target/bin/python3 /var/apps/python312/target/bin/python; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

# ---------- manifest 端口同步 ----------
# 应用中心启动前按 manifest 的 service_port 做占用检查，见 install_callback 中的说明。
MANIFEST="$(dirname "$TRIM_APPDEST")/manifest"

log() {
  echo "fnos-qrcode[upgrade] $1"
}

sync_manifest_port() {
  local port="$1" cur tmp
  [ -n "$port" ] || return 0
  if [ ! -f "$MANIFEST" ]; then
    log "未找到 $MANIFEST，跳过 manifest 端口同步"
    return 0
  fi

  cur="$(sed -n 's/^service_port[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$MANIFEST" 2>/dev/null | head -1)"
  if [ "$cur" = "$port" ]; then
    log "manifest 端口已是 $port，无需同步"
    return 0
  fi

  if grep -q '^service_port' "$MANIFEST" 2>/dev/null; then
    tmp="$MANIFEST.tmp.$$"
    if sed "s|^service_port[[:space:]]*=.*|service_port          = ${port}|" "$MANIFEST" >"$tmp" 2>/dev/null; then
      # 用 cat 回写以保留原 inode 与权限
      cat "$tmp" >"$MANIFEST" 2>/dev/null || true
    fi
    rm -f "$tmp" 2>/dev/null || true
  else
    printf '\nservice_port          = %s\n' "$port" >>"$MANIFEST" 2>/dev/null || true
  fi

  cur="$(sed -n 's/^service_port[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$MANIFEST" 2>/dev/null | head -1)"
  if [ "$cur" = "$port" ]; then
    log "已同步 manifest 的 service_port = $port"
  else
    log "警告：无法写入 $MANIFEST，应用中心仍会按旧端口检查"
  fi
  return 0
}

PY="$(find_python)" || exit 0

PORT=""
if [ -f "$SETTINGS" ]; then
  # --print-port 会解析并规范化配置；解析失败时返回默认端口
  PORT="$("$PY" "$APP_PY" --print-port 2>/dev/null | tr -d '[:space:]')" || true
  if [ -z "$PORT" ]; then
    echo "fnos-qrcode[upgrade] 警告：配置文件解析异常，应用启动时会回退到默认端口"
  fi
  echo "fnos-qrcode[upgrade] 已保留原有配置：$SETTINGS"
else
  # 全新安装缺少配置（例如升级自不含配置的旧版本），补一份默认值
  "$PY" "$APP_PY" --set --port 10001 --auth off >/dev/null 2>&1 || true
  PORT=10001
  echo "fnos-qrcode[upgrade] 未发现配置文件，已写入默认配置（端口 10001、不开启认证）"
fi

sync_manifest_port "$PORT"

exit 0
```

#### `cmd/uninstall_init` —— 卸载前停进程

```bash

#!/bin/bash
# 卸载前：先停掉应用进程，避免卸载后残留孤儿进程
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -x "$SELF_DIR/main" ]; then
  "$SELF_DIR/main" stop || true
fi

echo "fnos-qrcode[uninstall_init] 已停止应用"
exit 0
```

#### `cmd/uninstall_callback` —— 卸载后按向导处理配置

```bash

#!/bin/bash
# 卸载后：按卸载向导里选择的数据动作处理配置文件
#   keep   —— 保留 settings.json（重新安装后端口与账号还在）
#   delete —— 删除 settings.json
# 二维码内容只在浏览器里处理、从不写盘，所以这里没有用户数据要保护。
set -uo pipefail

PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
: "${TRIM_PKGVAR:=$PKG_DIR/var}"
: "${TRIM_PKGETC:=$PKG_DIR/etc}"
export TRIM_PKGVAR TRIM_PKGETC

SETTINGS="$TRIM_PKGETC/settings.json"
PIDFILE="$TRIM_PKGVAR/app.pid"

case "$(printf '%s' "${wizard_data_action:-keep}" | tr 'A-Z' 'a-z')" in
  delete|remove|clear)
    rm -f "$SETTINGS" 2>/dev/null || true
    echo "fnos-qrcode[uninstall] 已删除应用配置（端口与登录设置）"
    ;;
  *)
    echo "fnos-qrcode[uninstall] 已保留应用配置：$SETTINGS"
    ;;
esac

rm -f "$PIDFILE" 2>/dev/null || true
exit 0
```

### 4.11 `build-fpk.sh` —— 一键构建脚本

```bash

#!/bin/bash
# 构建 fnos-qrcode 的 .fpk 安装包
#
#   bash build-fpk.sh          构建（优先调用官方 fnpack，找不到则用 tools/build_fpk.py）
#   bash build-fpk.sh --list    只列出打包清单，不产出文件
#
# 在飞牛设备上构建时，fnpack 位于 /usr/local/bin/fnpack；
# 没有 fnOS 环境时，脚本会自动回退到同目录下的 Python 打包器。
set -euo pipefail

cd "$(cd "$(dirname "$0")" && pwd)"
FPK_DIR="$PWD"
ROOT_DIR="$(dirname "$FPK_DIR")"

LIST_ONLY=0
[ "${1:-}" = "--list" ] && LIST_ONLY=1

echo "==> 1/4 同步前端页面"
SRC="$ROOT_DIR/../index.html"
if [ ! -f "$SRC" ]; then
  echo "找不到前端源文件：$SRC" >&2
  exit 1
fi
mkdir -p "$FPK_DIR/app/ui"
cp "$SRC" "$FPK_DIR/app/ui/index.html"
echo "    index.html -> app/ui/index.html ($(wc -c <"$FPK_DIR/app/ui/index.html") bytes)"

echo "==> 2/4 整理命令权限"
chmod 755 "$FPK_DIR"/cmd/* 2>/dev/null || true
chmod 644 "$FPK_DIR/manifest" "$FPK_DIR"/config/* "$FPK_DIR"/wizard/* 2>/dev/null || true

echo "==> 3/4 清理构建垃圾"
find "$FPK_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$FPK_DIR" -name '*.pyc' -delete 2>/dev/null || true
find "$FPK_DIR" -name '.DS_Store' -delete 2>/dev/null || true
rm -f "$FPK_DIR"/*.fpk 2>/dev/null || true

if [ "$LIST_ONLY" = "1" ]; then
  echo "==> 4/4 打包清单"
else
  echo "==> 4/4 打包"
fi

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 && { PY="$candidate"; break; }
  done
fi
[ -n "$PY" ] || { echo "未找到 python3，无法构建" >&2; exit 1; }

if command -v fnpack >/dev/null 2>&1 && [ "$LIST_ONLY" = "0" ]; then
  echo "    使用官方 fnpack 打包"
  fnpack build
  echo
  echo "产物："
  ls -1 "$FPK_DIR"/*.fpk 2>/dev/null || true
else
  [ "$LIST_ONLY" = "0" ] && echo "    未检测到 fnpack，使用内置打包器（结构依据 fnpack 1.2.3 实测产物）"
  if [ "$LIST_ONLY" = "1" ]; then
    "$PY" "$FPK_DIR/tools/build_fpk.py" --list
  else
    "$PY" "$FPK_DIR/tools/build_fpk.py"
  fi
fi
```

### 4.12 `tools/make_icons.py` —— 图标生成器

构建期工具，需要在有 Pillow 的环境里跑一次；产物已提交进仓库，日常构建无需重跑。

```python

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
```

### 4.13 `tools/build_fpk.py` —— 备用打包器

在没有官方 `fnpack` 的机器上，按 fnpack 产物结构生成等效的 `.fpk`。

```python

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
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != '.':
            items.append((dirpath, rel_dir, True))
        for name in sorted(filenames):
            if should_skip(name):
                continue
            full = os.path.join(dirpath, name)
            rel = name if rel_dir == '.' else os.path.join(rel_dir, name)
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
```

### 4.14 `app/ui/index.html`

前端页面，**不在 `fpk/` 里手工维护**：`build-fpk.sh` 每次构建都会从仓库根 `index.html`
复制覆盖。所以仓库中这份前端只有**一个源**，改页面只改根目录的 `index.html`。

---

## 5. 构建过程

### 5.1 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| Python3 | 3.6+ | 打包与图标生成都需要；运行时也用系统 Python |
| 官方 `fnpack` | 可选 | 飞牛系统自带 `/usr/local/bin/fnpack`，会做完整校验，**推荐用它打包** |
| Pillow | 可选 | 仅重生成图标时需要（`tools/make_icons.py`） |
| bash | 任意 | 执行 `build-fpk.sh` 与 `cmd/` 脚本 |

### 5.2 方式一：在飞牛设备上构建（推荐）

```bash
# 1) 取源码
git clone <你的仓库地址> fnos-qrcode-repo
cd fnos-qrcode-repo
git checkout fnos-qrcode

# 2) 把整个 fpk/ 目录拷到飞牛设备（任意位置，例如 /tmp/qrbuild）
scp -r fnos-qrcode/fpk/ firlsy@<NAS_IP>:/tmp/qrbuild

# 3) 在设备上打包
ssh firlsy@<NAS_IP>
cd /tmp/qrbuild/fpk
fnpack build
# => 生成 fnos-qrcode.fpk
```

`fnpack build` 实际做了这些事：

1. 读取 `manifest`，校验必填字段
2. 把 `app/` 目录打成内层 `app.tgz`（**不带 `app/` 前缀**）
3. 计算 `app.tgz` 的 **MD5**，写回 `manifest` 的 `checksum` 字段
4. 组装外层 gzip tar：`manifest` + `app.tgz` + `cmd/` + `config/` + `wizard/` + 两个图标
5. 产出 `<appname>.fpk`

实测环境：飞牛 fnOS（`fnos-7500`，内核 6.18），`fnpack 1.2.4`，`appcenter-cli 1.0.2`。

### 5.3 方式二：没有 fnOS 环境

用仓库内置打包器产出同构的 `.fpk`：

```bash
cd fnos-qrcode/fpk
python3 tools/build_fpk.py            # 产出 fnos-qrcode_1.0.0_all.fpk
python3 tools/build_fpk.py --list     # 只打印打包清单，不写文件
```

它与官方 `fnpack` 的产物结构一致；`build-fpk.sh` 会在找不到 `fnpack` 时自动回退到它。

### 5.4 一键构建：`build-fpk.sh`

```bash
cd fnos-qrcode/fpk

# 指定 Python 解释器（可选；不指定则自动找 python3 / python）
PYTHON=python3 bash build-fpk.sh

# 只看清单
bash build-fpk.sh --list
```

脚本四步：

| 步骤 | 动作 |
| --- | --- |
| 1/4 | 同步前端：`../index.html` → `app/ui/index.html`（保证页面只有一份源） |
| 2/4 | 整理权限：`cmd/*` → 755，`manifest`/`config/*`/`wizard/*` → 644 |
| 3/4 | 清理构建垃圾：`__pycache__`、`*.pyc`、`.DS_Store`、旧 `*.fpk` |
| 4/4 | 打包：有 `fnpack` 用 `fnpack build`，否则用 `tools/build_fpk.py` |

### 5.5 FPK 容器结构

```
fnos-qrcode.fpk                    ← gzip 压缩的 tar
├── manifest                       ← 含 checksum = app.tgz 的 MD5
├── app.tgz                        ← 内层 tar.gz，内容是 app/ 下的文件
│   ├── ui/                        ← 静态页面 + 图标 + 入口配置
│   │   ├── index.html
│   │   ├── config
│   │   └── images/icon_64.png、icon_256.png
│   └── server/app.py
├── cmd/                           ← 生命周期脚本（755）
├── config/                        ← privilege、resource
├── wizard/                        ← install、config、uninstall
├── ICON.PNG                       ← 64x64
└── ICON_256.PNG                   ← 256x256
```

> 关键点：`desktop_uidir = ui` 指向的 `ui/` 位于 **`app.tgz` 内部**（即 `app/ui/`），
> 不在外层；`app.tgz` 内**不带 `app/` 前缀**。

---

## 6. 构建产物与校验

当前提交内已包含构建产物 `fnos-qrcode/fnos-qrcode.fpk`：

| 属性 | 值 |
| --- | --- |
| 大小 | `102688` B |
| MD5 | `c580a125ef28c7ab910f00f590a19c81` |
| SHA-256 | `ca133ad28ac5afb7625e395cb7aa04d2f46dad09df6d870c009191a5d0c3e937` |

### 校验脚本

构建后务必确认 `manifest.checksum` 与实际 `app.tgz` 的 MD5 一致 —— 这是包能否被接受的关键：

```python
import io, gzip, hashlib, tarfile

path = 'fnos-qrcode.fpk'
with gzip.open(path, 'rb') as gz:
    raw = gz.read()

outer = tarfile.open(fileobj=io.BytesIO(raw))
print('外层条目:', sorted(outer.getnames()))

manifest = outer.extractfile('manifest').read().decode('utf-8')
checksum = {}
for line in manifest.splitlines():
    if '=' in line:
        key, _, value = line.partition('=')
        checksum[key.strip()] = value.strip()

app_tgz = outer.extractfile('app.tgz').read()
real = hashlib.md5(app_tgz).hexdigest()
print('app.tgz md5 实际 =', real)
print('manifest checksum =', checksum.get('checksum'))
print('一致:', real == checksum.get('checksum'))

inner = tarfile.open(fileobj=io.BytesIO(app_tgz), mode='r:gz')
for item in inner.getmembers():
    print('  %-40s %s' % (item.name, 'dir' if item.isdir() else item.size))
```

期望输出：`一致: True`，且 `app.tgz` 内含 `ui/index.html`、`ui/config`、`server/app.py` 等。

### 验证结果（实测）

- `fnpack build` 打包成功，`manifest.checksum` 与 `app.tgz` 的 MD5 一致
- 外层条目与飞牛真机已安装应用的结构完全一致
- 包内 `app/server/app.py` 的 `APPVERSION` 与 `manifest` 的 `version` 一致（当前均为 `1.0.3`）
- 包内 `app/ui/index.html` 与仓库根 `index.html` 字节一致（构建脚本第 1 步同步）

---

## 7. 安装与验证

完整的安装步骤、向导字段说明、装后调整方式与实测清单见
**[`fnos-qrcode/fpk/README.md`](./fnos-qrcode/fpk/README.md)**，这里只给最简路径：

**Web UI（推荐，能看到向导）**

1. 应用中心 → 左下角「设置」→ 打开「手动安装」
2. 应用中心 → 左下角「手动安装」→ 选择 `fnos-qrcode.fpk`
3. 向导里填写服务端口（默认 `10001`）与是否开启用户认证
4. 装完点「启用」，桌面出现「二维码批量生成器」，点击即打开

**命令行**

```bash
cat > /tmp/install.env <<'ENV'
wizard_port=10001
wizard_auth_enable=off
wizard_auth_user=admin
wizard_auth_pass=
ENV

sudo appcenter-cli manual-install enable
sudo appcenter-cli install-fpk fnos-qrcode.fpk -e /tmp/install.env -v 1
sudo appcenter-cli start fnos-qrcode
```

**在测试飞牛机上实测通过的项目**

| 项目 | 结果 |
| --- | --- |
| 安装 + 启用 | 进程起来，按配置的端口监听，`GET /` 返回 200 |
| 桌面图标 | 出现「二维码批量生成器」，点击打开入口端口 |
| 实际生成二维码 | 4 条内容全部出码，写入「最近生成」 |
| 装后改端口（UI 改 10005） | 自动重启、旧端口关闭、入口端口同步跟随 |
| 装后开启认证 | 未登录 `302 /login`；密码错提示剩余次数；正确后进入 |
| 状态检测 | `cmd/main status` 运行中返回 0、停止返回 3 |

**v1.0.1 针对「端口被占用」误报的回归验证**（在一台 **10001 已被别的服务占用**
的测试机上做，用占位进程模拟）：

| 步骤 | 结果 |
| --- | --- |
| 复现旧版问题：manifest 固定 10001 + 10001 被占 | 启用返回 `code 11000`，弹「端口被占用」 |
| 跑新版 `cmd/install_callback`（wizard_port=12990） | 日志「已同步 manifest 的 service_port = 12990」，manifest 变为 12990 |
| 再点「启用」 | `code 0`，服务监听 `0.0.0.0:12990`，`GET /` 返回 200（10001 仍被占） |
| 跑新版 `cmd/config_callback`（把端口改成 12991） | manifest 同步为 12991，settings.json 同步为 12991 |
| 再点「启用」 | `code 0`，服务监听 `0.0.0.0:12991`，`GET /` 返回 200 |
| `checkport = false` 时 manifest 固定 10001 + 10001 被占 | 启用 `code 0`（不再误报） |

**v1.0.2 针对「登录限流」两处毛病的回归验证**（本机 Chrome 无头浏览器跑真实页面，
再在测试飞牛机的系统 `python3.11` 上跑一遍同样的 HTTP 序列）：

| 步骤 | 期望 | 结果 |
| --- | --- | --- |
| 连续失败 4 次 | 不锁定，逐次提示剩余次数 | ✅ `401`，「还可尝试 4 / 3 / 2 / 1 次」 |
| 第 5 次失败 | 立即锁定 120 秒 | ✅ `429`，锁定提示出现，`data-sec="120"` |
| 锁定页停留 3 秒 | 秒数按秒递减 | ✅ 120 → 117，登录按钮置灰禁用 |
| 锁定期内再提交 | 不延长窗口、页面不跳转 | ✅ 前端拦截；剩余秒数继续减少，未回到 120 |
| 刷新 `GET /login` | 延续同一窗口的剩余时间 | ✅ 仍为 117 秒（服务端重新算出，非固定值） |
| 锁定到期后刷新 | 提示自动消失、按钮恢复 | ✅ 倒计时归零 → 提示隐藏、按钮可点 |
| 到期后再失败一次 | 计数清零、重新给满额机会 | ✅ 「还可尝试 4 次」 |
| 正确密码 | 登录成功并清零 | ✅ `302` + 下发 `qr_session`；`blocked() == 0` |

**v1.0.3 依据第三轮渗透测试报告的修复验证**（真机 `192.168.x.x:12990`，
原始 TCP socket 协议测试 + Chrome 无头浏览器；锁相关用例临时关闭私网豁免，验完已还原）

| 报告条目 | 验证内容 | 结果 |
| --- | --- | --- |
| 前提 | `/`、`/index.html`、`/config`、`/favicon.ico`、不存在的路径 | ✅ 全部 `302 → /login`（鉴权先于文件解析） |
| 绕过测试 | 12 个路径规范化变体 + 7 种请求头伪造 + 6 种 Cookie 伪造 | ✅ 无一绕过 |
| A-01 | 8 个假用户名连续失败 | ✅ 真实用户名仍可提交，未被拖累 |
| A-01 | 同一用户名第 5 次失败 | ✅ `429`，`data-sec="120"` |
| A-01 | 锁定期内再提交 | ✅ 剩余 117 秒，**未重置回 120** |
| A-01 | 锁定后换用户名 | ✅ `401`（锁只针对该用户名） |
| A-01 | 等 116 秒后提交 | ✅ 计数清零、重新 `401`；页面提示消失、按钮恢复 |
| A-02 | 失败提示文案 | ✅ 统一「用户名或密码不正确」，无「还可尝试」 |
| A-03 | 无 token / token 二次使用 | ✅ 均 `403`；登录页下发一次性 token 且不下发 Cookie |
| §5 | 登出后用旧 Cookie | ✅ `302 → /login` |
| F-02 | JSZip SRI | ✅ 带 `integrity` 加载成功 |
| F-04 | 安全响应头 | ✅ CSP / X-Frame-Options / Referrer-Policy / nosniff 齐全 |
| R2-01 | 单行 9 KB 请求头 | ✅ `431`（原来是静默丢弃） |
| R2-02 | HTTP/0.9 简写请求 | ✅ `505`（原来会被正常处理） |
| R2-03 | 非法 / 缺失 Host、`Content-Length: 100000` | ✅ `400` / `400` / `413` |
| 入口 | fnOS 桌面图标点击 | ✅ 新窗口打开 `…:12990/login`，`X-Frame-Options: DENY` 不影响入口 |
| CSP | 页面加载 / JSZip / 出码 / zip / 历史缩略图 | ✅ 无 CSP 拦截、无 JS 错误 |
| 代理 | `/qr` 正常 / 缓存 / 参数校验 / 上游 502 回落 | ✅ 缺参 `400`、超长 `400`、注入被中和、缓存字节一致 |
| 下载 | 打包下载 zip | ✅ 修复前超时（CORS）→ 修复后 **3374 B 成功**，zip 内 3 个 PNG 正常 |

> 说明：上表 A-01 那几行记录的是 v1.0.3 当次（按 IP + 用户名记账、豁免私网）的结果。
> v1.0.4 已把粒度改回纯来源 IP 并取消内网豁免，以 v1.0.4 表格为准。

**v1.0.4 锁定逻辑调整的验证**（`importlib` 直接加载发布包里的真实 `app.py`，
本地起实例走 HTTP + Chrome 无头浏览器；阈值 5 次 / 窗口 120 秒 / 锁定 120 秒不变）

| 场景 | 期望 | 结果 |
| --- | --- | --- |
| 连续失败 4 次 | 不锁定 | ✅ 全部 `401` |
| 第 5 次连续失败 | 锁定 120 秒 | ✅ `429`，`data-sec="120"` |
| 锁定期内再提交 | 不延长窗口 | ✅ 剩余 105~118 秒递减，未重置回 120 |
| 同一 IP 换用户名失败 | 计数不洗掉 | ✅ 4 个用户名各失败 1 次后，第 5 个用户名失败即 `429` |
| 锁定期间用正确凭据 | 一并被挡 | ✅ `429`，不下发会话 |
| 刷新 `GET /login` | 延续同一窗口 | ✅ 服务端现算剩余秒数，非重新计数 |
| 倒计时归零 | 提示隐藏、按钮恢复 | ✅ 均生效 |
| 到期后再失败 1 次 | 计数归零 | ✅ `401`，内部状态计数为 1、锁定时间戳为 0 |
| 成功登录 | 计数清零 | ✅ `302`；随后失败回到 `401` |
| 连续窗口断裂 | 计数清零 | ✅ 回拨最近失败时间 121 秒后再失败 → `401`（未被锁） |
| 回环来源（出厂配置） | 同样受限 | ✅ 连续 6 次 → 第 5、6 次 `429`（豁免确已取消） |
| 放行名单机制 | 仍可用 | ✅ 临时加 `203.0.113.` 后该网段不锁，名单外照常锁定（验完已还原） |

锁定相关 13 条用例全部通过；全量 86 条通过 85，唯一失败项为 `F-21`
（`Server` 头仍带 `Python/3.x.y`，属 v1.0.3 的 F-06 修复不完整，与本轮改动无关）。

**v1.1.0（多账号 + 可配置登录安全）的验证**（`importlib` 直接加载发布包里的真实 `app.py`，
本地起 4 个实例；全量 **108 条用例全部通过**）

| 场景 | 期望 | 结果 |
| --- | --- | --- |
| 多账号 | 两个账号各自可登录；不存在账号 401；密码错 401 且提示统一 | ✅ |
| 设备上限 | 上限 2 时第 3 台设备登录被拒 | ✅ `403` 且提示「已达到设备上限」 |
| 多设备令牌 | 同账号同秒登录两台设备，token 不同、计数为 2 | ✅（回归第 12 条修复） |
| 不限设备 | 标记不限设备的账号连续登录 3 次 | ✅ 全部 `302` |
| 设置页鉴权 | 未登录访问 `/settings` | ✅ `302 → /login` |
| 设置页渲染 | 账号列表 / 在线设备 / 各表单齐全 | ✅ |
| 新增账号 | 设置页新增后立即可登录 | ✅ |
| 防自锁 | 删除当前登录账号 | ✅ 被拒，重定向带错误提示 |
| 设置页 CSRF | 缺令牌提交 | ✅ 配置不变并报错 |
| 失败次数可配置 | 改为 2 后第 2 次失败 | ✅ `429` |
| 锁定时长可配置 | 改为 9 秒 | ✅ `data-sec="9"` |
| IP 黑名单 | 命中来源 / 名单外来源 | ✅ `403`（含提示）/ `302` |
| IP 白名单 | 名单外 / 网段内 | ✅ `403` / `302` |
| 回环豁免 | 白名单不含回环，从回环登录 | ✅ `302`（防自锁） |
| 配置迁移 | v1.0.x `auth.username/password` | ✅ 迁移为 `accounts[0]` 并补全默认值 |
| 设置页转义 | 账号名含 `<script>` | ✅ 被转义，无存储型 XSS |
| 中文输入 | 中文 / emoji 用户名 | ✅ 正常 401，不再空响应 |
| `Server` 头 | 重写 `version_string()` | ✅ `fnos-qrcode/1.1.0`，无 `Python/3.x.y` |

---

## 8. 实现要点与踩坑记录

### 8.1 端口可配置的实现链路

```
向导 wizard_port
   └─> cmd/install_callback、cmd/config_callback
          ├─> app.py --set --port N
          │      └─> $TRIM_PKGETC/settings.json   ← 运行时唯一真实来源
          │             ├─> cmd/main 启动时 app.py --print-port 读取
          │             └─> app/ui/config 的 ${wizard_port} 由应用中心运行时替换
          └─> 同步已安装 manifest 的 service_port = N
                 └─> /var/apps/<app>/manifest     ← 应用中心据此做端口占用检查
```

**为什么 manifest 也要同步**：`/var/apps/<app>/manifest` 是安装时从 FPK 解出来的**静态副本**，
应用中心在「启用 / 启动」前只查这个文件里的 `service_port`，它不会跟随向导或「应用设置」里改的端口。
不同步的话，用户把端口从 10001 改成 12990 之后，检查的仍然是 10001。
详见 8.3 第 5 条。

### 8.2 认证实现

- 密码以 `pbkdf2_sha256`（20 万次迭代 + 16 字节随机盐）存哈希，**明文不落盘、不进日志**
- **多账号**（v1.1.0）：账号存在 `accounts` 数组里（上限 `MAX_ACCOUNTS=10`），
  每项含 `user / password / max_devices / unlimited_devices`。
  登录时遍历账号做**定时安全比较**；用户名不存在时也会跑一次 PBKDF2（拿首个账号当诱饵），
  避免从响应时间判断「某账号是否存在」。**每个请求最多一次哈希**，账号变多也不拖慢登录
- **配置迁移**：`coerce_settings()` 同时被 `load_settings()` 与 `build_server()` 使用，
  v1.0.x 的 `auth.username / auth.password` 会自动迁移成 `accounts[0]`，并补全默认值；
  手工写了一半的 `settings.json` 也不会让服务起不来
- 登录成功下发 HMAC-SHA256 签名 Cookie（`qr_session`，7 天有效），带 `HttpOnly` + `SameSite=Lax`
- 会话密钥存在 `settings.json` 的 `session_key`（首次启动生成并落盘）。
  早期版本由密码哈希派生，结果是「一改密码 / 一加账号，全员被踢下线」——
  这会直接抵消「每账号最多 N 台设备」的意义，因此 v1.1.0 改成持久化密钥；
  想强制所有人重新登录，删掉该字段再重启即可
- **会话登记表**（`SessionStore`，v1.1.0 起同时负责吊销与设备计数）：
  记录 `token -> {user, issued, exp}`，用来统计每个账号的在线设备数。
  有效性判定仍是「签名 + 未被吊销」，所以**重启不会把已登录设备强制下线**，
  代价是设备计数重启后从 0 重新累计
- **每账号设备数限制**（v1.1.0）：达到上限时**拒绝新登录**（403），不挤掉旧设备；
  标了 `unlimited_devices` 的账号不受限。逃生通道是应用中心「设置」（无需登录）
  与应用内「设置」页的踢设备
- 登出会把该 token 记进吊销表，复制走的 Cookie 立刻失效
- 登录表单带**一次性 CSRF 令牌**（`CsrfGuard`，30 分钟有效，提交即作废），
  服务端保存、不引入预认证 Cookie，登录页依然不下发任何 Cookie；
  应用内设置页的**每个表单各自领一个令牌**（否则一次性语义会让同一页面只能提交一次）
- 登录失败限流（`LoginGuard`）：计数键是**来源 IP**（不区分用户名），
  在 `lock_seconds` 内**连续**失败 `max_fail` 次即锁定该来源
  （时长 60→120 秒是 v1.0.2；v1.0.3 一度改成 (IP, 用户名) 并豁免私网；
  **v1.0.4 改回纯来源 IP 并取消内网豁免**，同时新增连续失败窗口；
  **v1.1.0 把次数与时长改为读设置**，`_limits()` 每次调用现取，改配置即时生效）；
  锁定提示里的秒数由页面内联脚本按秒递减，归零后自动隐藏提示并恢复登录按钮
- **登录 IP 黑白名单**（v1.1.0）：`_ip_allowed()` 只挂在 `POST /login` 上，
  页面浏览不受影响；回环地址始终放行；支持单 IP 与 CIDR（`ipaddress` 标准库）。
  判定用的是 `self.client_address[0]`（封装在 `_client_ip()` 里，测试可替换），
  **不读 `X-Forwarded-For`** —— 那种头可伪造
- 错误提示统一为「用户名或密码不正确」，不透露剩余可尝试次数
- `LOGIN_LOCK_TRUSTED_PREFIXES` 默认为**空元组 —— 任何来源都不豁免**（含回环与私网）；
  确实需要放行时（例如前置反向代理）才把前缀加进去
- 生命周期脚本用 `--pass-stdin` 通过管道传密码，避免密码出现在 `ps` 命令行里

### 8.3 踩坑记录（都已修复）

1. **`config/resource` 格式**
   `data-share` 必须是对象 `{"shares": []}`。最初写成数组，`fnpack build` 直接失败。

2. **`cmd/main` 进程探测误判**
   应用中心传入的 `TRIM_APPDEST` 是 `/var/apps/<app>/target` 这类**软链**，
   而进程命令行里记录的是**解析后的真实路径** `/vol1/@appcenter/<app>`。
   只匹配一种会导致 `status` 误报「未运行」、进而 `config_callback` 不重启。
   现在 `list_pids()` 同时匹配两种形态（`APP_PY` 与 `readlink -f` 结果）。

3. **换行符必须 LF**
   `cmd/` 下的脚本会被飞牛**直接执行**。若在 Windows 上被检出为 CRLF，
   shebang 会变成 `#!/bin/bash\r`，报 `bad interpreter`。
   仓库根加了 `.gitattributes`：`* text=auto eol=lf`，二进制（png/fpk/tgz）声明为 `binary`。

4. **带向导的应用不能用命令行卸载**
   `appcenter-cli uninstall` 会返回 `please uninstall it from Web UI`。
   卸载必须走 Web UI（这样才能弹出 `wizard/uninstall`）。升级同理：先卸载再安装。

5. **「端口被占用」误报：应用中心的检查读的是 manifest 里的静态端口**（v1.0.1 修复，最花时间的一个）

   **现象**：装好后点「启用」弹「无法启用 fnos-qrcode / 端口被占用」，
   在「应用设置」里把访问端口改成任意值都仍然报占用，而那个端口其实是空闲的。

   **根因**：应用中心启动前的端口占用检查，只用 `/var/apps/<app>/manifest` 里
   `service_port` 声明的那一个端口（是否执行该检查由 `checkport` 控制）。
   这个字段是**打包时的静态值**，安装后固定不变，既不跟随安装向导的
   `wizard_port`，也不跟随「应用设置」里改的端口。本包 1.0.0 里写死
   `service_port = 10001`，于是：

   - 目标机器上 10001 已被别的服务占用时 → 界面上把「访问端口」改成什么
     都没用，检查的永远是 10001 → 永远「端口被占用」
   - 只有在 10001 恰好空闲的机器上才装得上（所以早期测试机一直是好的）

   注意 `${wizard_port}` 这个运行时替换**只作用于 `app/ui/config` 的桌面入口**，
   manifest 里写 `${wizard_port}` 不会被替换（实测：仍然不做检查）。

   **实测取证**（真机，走 `/var/run/com.trim.app.center.sock` 的内部 RPC）：

   | manifest 的 service_port | 10001 实际占用 | 应用设置里的端口 | 启用的返回 |
   | --- | --- | --- | --- |
   | `10001` | 被占用 | 10009（空闲） | `code 11000` → 前端「端口被占用」 |
   | `10009` | 被占用 | 10009 | `code 0`，启动成功并监听 10009 |
   | `10001` | 被占用 | 10009 | `code 0`（`checkport = false`） |
   | 空 | 被占用 | 10009 | `code 0`，入口端口仍取 10009 |

   接口是 `POST /app-center/v1/start/start`，`code 11000` 就是「端口被占用」；
   `POST /app-center/v1/start/check` 只校验系统版本，与端口无关。

   **修复**（v1.0.1）：

   - manifest 里改 `checkport = false`：静态值本来就追不上用户改的端口，
     开着这个检查只会误报，干脆交给应用自己负责
   - `cmd/install_callback` / `cmd/config_callback` / `cmd/upgrade_callback`
     在写入 `settings.json` 的同一处调用 `sync_manifest_port()`，
     把已安装 manifest 的 `service_port` 同步成真实端口，声明与实际保持一致
     （万一这句没写成，日志里会留一条警告，且不再有误报）

   **真实的冲突**怎么表现：端口真被别人占用时，服务端绑定失败，
   `$TRIM_PKGVAR/app.log` 里会有 `OSError: [Errno 98] Address already in use`，
   换个空闲端口重新启用即可。

6. **`checkport = true` 时手工起过进程也会报占用**
   如果绕开应用中心手工起过本应用的进程，端口被自己占着，
   再次启用也会提示「端口被占用」——先停用或结束该进程即可。
   v1.0.1 起 `checkport = false`，这种情况不再触发该提示。

7. **不要绕过应用中心启停服务**
   直接用 `cmd/main start` 会让应用中心状态与实际不同步（应用中心认为已停用，
   桌面图标随之隐藏）。测试时请通过应用中心「启用 / 停用」操作。

8. **登录锁定：倒计时不动 + 等待后仍是同一秒**（v1.0.2 修复）

   **现象**（用户反馈，附截图）：连续输错密码后提示「56 秒后重试（登录失败次数过多）」，
   等一段时间刷新再登录，仍显示「56 秒」——像是秒数被钉住了。

   **根因有两层**，正好一条对应一个现象：

   | 现象 | 根因 |
   | --- | --- |
   | 秒数不动 | `GET /login` 不渲染锁定提示，只有 `POST` 失败时才由 `_fail()` 渲染一次；页面里没有任何脚本，那个数字就是**服务端渲染时刻的快照** |
   | 刷新后仍是同一秒 | 旧 `fail()` 每命中一次就执行 `until = time.time() + LOGIN_LOCK_SECONDS`，**把整轮锁定重新推后**；用户每次「等一会儿再试」都在重置自己的等待 |

   第二层尤其阴险：从用户视角看是「倒计时永远卡住」，实际是每次尝试都在给它续命。

   **修复**（v1.0.2）：

   - `LoginGuard.fail()` 只在**首次越界**（`count` 刚好从 4 变 5）时设置 `until`；
     锁定期内再次 `fail()` 只累加计数、保持原窗口，语义变成「从第 5 次失败起算 120 秒后放行」
   - `LoginGuard.info(ip)` 统一返回 `(剩余秒数, 失败次数)`，锁定到期时顺手把计数清零，
     所以「等满 120 秒」之后是真的恢复满额机会，而不是下一次错误立刻又锁
   - `GET /login` 也检查 `blocked()`，刷新页面能看到**同一窗口**的剩余秒数
   - 新增 `LOGIN_LOCK_NOTICE` 模板：把一个 `data-sec` 交给页面内联脚本按秒递减，
     归零后自动隐藏提示、恢复登录按钮；锁定期间拦截 `submit` 事件（禁用按钮不够，
     回车提交在部分浏览器仍会发请求）
   - 锁定时间 `LOGIN_LOCK_SECONDS` 按需求由 60 秒改为 **120 秒**

   客户端倒计时只负责「好看」，真实判定始终在服务端：`do_POST` 每次都会先查
   `blocked()`，即使脚本被绕过也照样拒绝。锁定状态存在进程内存里（重启即清空），
   这是为了不写盘、不依赖运行用户对配置目录的写权限。

9. **「打包下载 zip」在任何真实部署上都不工作**（v1.0.3 修复，报告里没有、验证时发现的）

   **现象**：在 NAS 上（`http://192.168.x.x:12990/`）生成二维码，预览正常出图，
   但点「打包下载」没有任何反应；控制台报：
   `Access to fetch at 'https://api.2dcode.biz/v1/create-qr-code…' from origin
   'http://192.168.x.x:12990' has been blocked by CORS policy:
   No 'Access-Control-Allow-Origin' header is present`，
   页面提示「当前环境拦截了跨域下载，已在新窗口打开图片，右键另存即可」。

   **根因**：草料开放平台那个接口的 CORS 策略是**只对 `localhost` / `127.0.0.1`
   回 `Access-Control-Allow-Origin`**。实测（带不同 `Origin` 请求头）：

   | 请求携带的 Origin | 上游返回的 `Access-Control-Allow-Origin` |
   | --- | --- |
   | `http://127.0.0.1:18752` | `http://127.0.0.1:18752`（放行） |
   | `http://localhost:5173` | `http://localhost:5173`（放行） |
   | `http://192.168.1.x:12990` | 无 |
   | `http://192.168.3.x:12990` | 无 |
   | `https://example.com` | 无 |

   也就是说：**本机开发时一切正常，一旦装到 NAS 上从局域网访问，`fetch` 必定被拦**。
   （`<img>` 加载不受 CORS 限制，所以「预览出图」一直是好的，掩盖了这个问题；
   单张下载靠 `window.open` 兜底也看不出来，只有 zip 打包需要真正读字节时才会暴露。）
   早期为解决「opaque 响应」问题加的 `cache: 'no-store'` + 换 URL 重试，
   治的是另一个病，对 CORS 无能为力。

   **修复**（v1.0.3）：加同源代理 `GET /qr`，由服务端转发上游：

   - 上游主机**写死在代码里**（`QR_UPSTREAM`），请求参数无法指定地址 → 杜绝 SSRF
   - `data` 限长 1024、`size` 必须匹配 `\d{1,4}x\d{1,4}` 且 ≤1024，
     再用 `urlencode` 拼装 → 杜绝参数注入
   - 响应限类型（必须 `image/*`）、限体积（2 MB），按 `(data, size)` 做进程内缓存
   - 前端优先走 `/qr`，任一张图失败就回落到直连（每张只回落一次），
     静态部署 / NAS 无外网时行为与以前一致
   - `/qr` 位于鉴权之后，未登录同样 `302 /login`

   顺带把 CSP 的 `connect-src` 收紧为 `'self'`（`img-src` 保留第三方域给回落用）。

10. **锁定粒度与「内网豁免」的来回调整**（v1.0.4）

   v1.0.3 依据第三轮报告把计数粒度从「纯 IP」改成「(IP, 用户名)」，
   并把回环 / 私网前缀加进 `LOGIN_LOCK_TRUSTED_PREFIXES`。随后发现两个副作用：

   | 现象 | 根因 |
   | --- | --- |
   | 局域网内怎么都触发不了锁定 | 私网来源被豁免 —— 而本应用的主要使用场景恰恰就是局域网 |
   | 同一 IP 换用户名反复失败也不锁 | 计数按 (IP, 用户名) 分开记账，换个用户名就是一份新配额 |

   v1.0.4 按实际需求调整回**纯来源 IP**，并把放行名单清空（默认谁都不豁免）：

   - `LoginGuard.key()` 退化为来源 IP；状态变为 `_state[ip] = [失败次数, 最近失败时间, 锁定截止时间]`
   - `blocked(ip)` / `fail(ip)` / `reset(ip)` 不再接收用户名参数
   - `GET /login` 直接取 `blocked(ip)`（原来要靠 `blocked_any(ip)` 在所有用户名里取最大值，
     因为刷新页面时还不知道用户会填哪个用户名）
   - 新增 `LOGIN_FAIL_WINDOW = 120`：`fail()` 里若距上次失败已超过该窗口则计数清零，
     避免「隔几天各失败一两次、累计 5 次」被误锁
   - `trusted()` 改为**只依赖放行名单**（去掉硬编码的 `::1` 特判）；名单为空时任何来源都受限

   **取舍（重要）**：纯 IP 粒度在共享出口 IP（反向代理 / NAT）时会「一人失败、全 IP 被锁」，
   正是报告 A-01 描述的场景 —— 本次是**有意接受**这一取舍，换取局域网内锁定真实可用。
   部署在反向代理之后时，应把代理出口 IP 加进 `LOGIN_LOCK_TRUSTED_PREFIXES`，或在代理层限流。

   验证：86 条用例通过 85（唯一失败项是 `Server` 头仍带 `Python/3.x.y`，属 F-06 未修完，与本次改动无关）。
   锁定相关 13 条全部通过，含「换用户名不洗掉计数」「回环来源同样锁定」「连续窗口断裂清零」。
   实现说明见 `workbuddy开发项目测试/pentest-12990/登录锁定逻辑-实现说明.html`。

11. **登录框输入中文 → `ERR_EMPTY_RESPONSE`**（v1.1.0 修复）

   **现象**：在 `http://192.168.x.x:12990/login` 的用户名框里输入中文后提交，
   页面变成「该网页无法正常运作 / 192.168.x.x 未发送任何数据 / ERR_EMPTY_RESPONSE」。

   **定位**：用原始 socket 复现，中文 / emoji 用户名得到的是**0 字节响应**（连接被直接关闭），
   ASCII 用户名正常 401。服务端日志给出确切位置：

   ```
   File "app.py", line 581, in do_POST
       ok = (hmac.compare_digest(username, auth['username'])
   TypeError: comparing strings with non-ASCII characters is not supported
   ```

   **根因**：`hmac.compare_digest()` 对**含非 ASCII 的 `str`** 会直接抛 `TypeError`
   （它只接受 ASCII-only 的 str 或 bytes）。异常从 `do_POST` 冒泡到 `handle_one_request`，
   `socketserver` 打印 traceback 后**关闭连接、一个字节都不写** —— 浏览器只能显示 ERR_EMPTY_RESPONSE。
   注意 `and` 短路：只有**用户名**非 ASCII 才会触发，纯中文密码不会（已验证）。

   **修复**（三层）：

   - `verify_login()` 里两侧都先 `encode('utf-8')` 再比较；
   - `verify_password()` 同样改成按 bytes 比较（防御性，防止配置里出现异常字符）；
   - `handle_one_request()` 把 `getattr(self, name)()` 包进 try/except，异常时回 **500** ——
     这条是兜底：**任何**未预期异常都不该表现为「空响应」，否则排查成本极高。

   回归用例 F-26/F-27/F-28 分别覆盖中文用户名、emoji 用户名、中文密码。

   > 同类隐患已排查：`parse_session()` 的比较在 try 内（安全）；
   > `verify_password()` 已加固。**教训：任何用户可控字符串做定时安全比较前必须先 encode。**

12. **同一账号在同一秒登录两台设备会拿到完全相同的 token**（v1.1.0 修复）

   做「每账号最多 N 台设备」时暴露出来的旧问题：`make_session()` 的 payload 是
   `{'u': user, 'exp': int(time.time()) + SESSION_TTL}` —— **没有随机量**，
   于是同一账号在同一秒内签发的 token 完全一致。后果有两条：

   - 会话登记表被同一个 key 覆盖，「在线设备数」永远算不对（本轮冒烟测试就是这么撞出来的）；
   - 登出一台设备会把同秒登录的另一台**一起**登出。

   修复：payload 里加一个 `secrets.token_hex(8)` 的随机 `n`。
   回归用例 V-04 断言同账号两设备的 token 不相同且登记计数为 2。

   **实测**：修复前 zip 下载超时（落到 `window.open` 兜底）；修复后同一台机器
   4 张图出图 + **打包下载成功 3374 B**，无失败请求。

10. **登录锁定的粒度与信息泄露**（第三轮渗透测试 A-01 / A-02 / A-03，v1.0.3 修复）

    **A-01（中危）失败计数不区分用户名**：计数只绑来源 IP，
    攻击者**不需要知道任何有效用户名**，用 `nobody-0…nobody-7` 这样的假用户名连续失败 5 次，
    就能把该来源的登录入口整个锁死。修复：计数键改为 `(IP, 用户名)` 二元组 ——
    假用户名只消耗自己的配额；恶意刷某个具体账号仍会锁该账号，但不再「一人被刷、全员被挡」。
    同时按用户选择加了**私网来源豁免**（`LOGIN_LOCK_TRUSTED_PREFIXES`），
    避免家用 NAS 把自己锁在门外。

    > 副作用：局域网内测不出锁定效果（这是设计如此）。
    > 本次验证是把该常量临时改成 `()` 后重启应用做的，验完已还原。

    **A-02（低危）提示泄露剩余次数**：原来会回「还可尝试 4 次」「59 秒后重试」。
    修复：错误提示统一为「用户名或密码不正确」，不再透露次数。
    **锁定倒计时按用户要求保留并改为动态递减**——这里与报告建议存在取舍：
    报告主张完全模糊化，用户明确要求看到秒数，最终只收敛掉「剩余次数」这部分泄露，
    倒计时作为可用性提示保留。

    **A-03（低危）登录表单无 CSRF Token**：单纯登录 CSRF 危害有限，
    但结合 A-01 可以「借受害者浏览器不断触发失败把他锁死」。
    修复：加服务端一次性令牌（`CsrfGuard`），`GET /login` 下发隐藏字段，
    提交即作废；不引入预认证 Cookie，登录页依然不下发任何 Cookie。

    **§5 待测项「登出后会话是否真正失效」**：实测**旧 Cookie 登出后仍可访问**（无状态签名的固有问题）。
    修复：加进程内吊销表（`SessionStore`），登出即吊销该 token。

### 8.4 前端相关（`index.html`）

- 二维码由草料开放平台公开接口生成，**无需 API key**：
  `GET https://api.2dcode.biz/v1/create-qr-code?data=<内容>&size=400x400`（返回 PNG）
- **v1.0.3 起改走同源代理 `/qr`**（原因见 8.3 第 9 条）：前端优先请求 `qr?data=…&size=…`，
  由服务端转发；代理不可用时自动回落到直连（每张图只回落一次，之后本会话都用直连）
- 批量下载曾失败：预览用 `<img>` 加载会往缓存写一条 *opaque* 响应，
  而下载用 `fetch(url, {cache:'force-cache'})` 会复用那条响应，opaque 响应通不过 CORS 校验。
  修复：改用 `cache: 'no-store'`，失败时追加随机参数换 URL 重试，最后兜底 `window.open`
- 第三方脚本 JSZip（jsDelivr）带 `integrity`（SRI）+ `crossorigin` + `referrerpolicy`
- 支持 `.txt / .csv / .tsv` 导入，CSV 自动识别分隔符、支持引号字段、
  UTF-8 失败回退 GBK、自动去 BOM；多列时可按列导入并忽略表头

---

## 9. 上传到 GitHub

### 9.1 首次推送

仓库目前是本地仓库，尚未配置远端：

```bash
git remote add origin git@github.com:<你的账号>/<你的仓库>.git

# 推送开发主版本
git push -u origin main

# 推送飞牛部署分支（FPK + 静态部署包 + 本文档）
git push -u origin fnos-qrcode
```

如果希望 `main` 也带上 FPK，可以在 GitHub 上从 `fnos-qrcode` 开 PR 合入 `main`，
或本地执行 `git checkout main && git merge fnos-qrcode && git push origin main`。

### 9.2 上传前检查清单

- [ ] `.gitignore` 已排除 `.workbuddy/`、`__pycache__/`、`*.pyc`、`_t*.html`
      （临时验证页与本地 IDE 数据不会进仓库）
- [ ] `.gitattributes` 存在，确保 `cmd/` 脚本以 LF 检出
      —— 可用 `git show HEAD:fnos-qrcode/fpk/cmd/main | head -c 20 | od -c` 确认
- [ ] 验证 `cmd/main` 首行是 `#!/bin/bash\n`（不是 `\r\n`）
- [ ] 确认没有把密码、口令、内网 IP 等敏感信息写进源码
      （`settings.json` 只存在于设备本地，不入库）
- [ ] `fnos-qrcode.fpk`（约 75 KB）作为二进制随仓库提交是安全的；
      若不想提交产物，可把 `*.fpk` 加入 `.gitignore`，让使用者自行构建

### 9.3 提交历史

```
d82abf0  1.1.0：多账号 + 可配置的登录安全（失败次数/锁定时长/IP 名单/每账号设备数）
7a8f605  登录锁定逻辑调整 1.0.4：按来源 IP 记账、取消内网豁免、新增 120 秒连续失败窗口
83c1c8c  安全加固 1.0.3：按第三轮渗透测试报告修复，并修掉 zip 下载的 CORS 缺陷
110e559  DEPLOY.md：同步 v1.0.2 登录限流修复的源码、指纹与踩坑记录
100f538  修复登录限流：倒计时不动 / 等待后仍锁在同一秒，锁定 60s→120s
38a36f6  DEPLOY.md：同步提交历史
e9aa7d8  修复启用时误报「端口被占用」：manifest 的 service_port 不跟随用户改的端口
9b2903f  新增 DEPLOY.md：FPK 构建源码与完整构建过程
d512b38  fnos-qrcode: README 补充 FPK 与静态部署两种形态的说明
d96d195  统一换行符为 LF（cmd/ 脚本在飞牛上需以 LF 检出）
b37afa9  fnos-qrcode: 新增飞牛 fnOS 原生应用包（FPK）
f7815f9  新增飞牛 fnOS 静态部署包（分支 fnos-qrcode）
52f8b4c  二维码批量生成器：单文件静态版（开发主版本）
```

---

## 附：许可与出处

- 二维码生成能力来自草料开放平台公开接口（`api.2dcode.biz`），本仓库不包含其代码
- FPK 规范与生命周期约定参考飞牛（fnOS）应用开发文档
- 本工程源码可自由用于学习与自用；二次分发请自行确认所依赖的第三方接口条款

