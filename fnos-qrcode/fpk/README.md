# fnos-qrcode —— 飞牛 fnOS 原生应用包（FPK）

把「二维码批量生成器」打包成飞牛 fnOS 的原生应用：应用中心里手动安装 `.fpk` 即可，
装完在桌面出现图标，点开就是二维码批量生成页面。

- 默认端口 **10001**，**安装时可指定**，**装好后可随时改**
- 用户认证**可选**（安装时和装好后都能开关）
- 纯 Python3 标准库实现，不需要 node、不需要 Docker、不需要联网装依赖

## 成品

| 文件 | 说明 |
| --- | --- |
| `../fnos-qrcode.fpk` | 可直接安装的安装包（由官方 `fnpack 1.2.4` 构建） |
| `manifest` | 应用清单：appname、版本、入口、端口声明 |
| `cmd/` | 生命周期脚本（安装 / 启动 / 停止 / 配置 / 升级 / 卸载） |
| `wizard/` | 安装、配置、卸载三个向导的字段定义 |
| `config/privilege`、`config/resource` | 运行身份（package 专用用户）与资源声明 |
| `app/ui/` | 前端页面 + 图标 + 桌面入口配置 |
| `app/server/app.py` | 内置 HTTP 服务：静态托管 + 可选登录认证 |
| `tools/` | 图标生成器与备用打包器（无 fnOS 环境时用） |
| `build-fpk.sh` | 一键构建脚本 |

## 构建

**推荐：在飞牛设备上构建**（官方 `fnpack` 会做完整校验）

```bash
# 把 fpk/ 整个目录拷到 NAS，然后在 fpk/ 里执行
fnpack build
# 产出 fnos-qrcode.fpk
```

fnOS 自带 `fnpack`（`/usr/local/bin/fnpack`），无需额外安装。

**没有 fnOS 时**，`build-fpk.sh` 会自动回退到内置打包器（`tools/build_fpk.py`，
按 fnpack 产物结构生成同构的 `.fpk`）：

```bash
bash build-fpk.sh          # 构建
bash build-fpk.sh --list    # 只列打包清单
```

两者都会先把仓库根目录的 `index.html` 同步到 `app/ui/index.html`，保证页面只有一份源。

## 安装

### 方式一：Web UI（推荐，能看到向导）

1. 应用中心 → 左下角**设置** → 打开「**手动安装**」
2. 应用中心 → 左下角「**手动安装**」→ 选择 `fnos-qrcode.fpk`
3. 按向导填写：

   | 向导项 | 默认 | 说明 |
   | --- | --- | --- |
   | 服务端口 | `10001` | 1024~65535，不能与其他应用冲突 |
   | 是否开启用户认证 | 不开启 | 开启后访问本应用需先登录 |
   | 登录用户名 | `admin` | 仅在开启认证时生效 |
   | 登录密码 | 空 | 至少 6 位；开启认证时必填 |

4. 安装完成后在应用中心点「启用」；桌面随即出现「二维码批量生成器」图标

### 方式二：命令行

```bash
cat > /tmp/install.env <<'ENV'
wizard_port=10001
wizard_auth_enable=off
wizard_auth_user=admin
wizard_auth_pass=
ENV

sudo appcenter-cli manual-install enable       # 首次需要打开手动安装开关
sudo appcenter-cli install-fpk fnos-qrcode.fpk -e /tmp/install.env -v 1
sudo appcenter-cli start fnos-qrcode
```

> 带向导的应用**卸载**必须走 Web UI（命令行会提示 `please uninstall it from Web UI`）；
> 升级同样是卸载后重新安装。

## 装好后调整配置

桌面图标**右键 → 管理应用 → 应用设置 → 运行设置 → 编辑**：

- **服务端口**：改完点「保存并重启」，应用自动重启并用新端口；桌面图标的访问端口会同步跟随
- **用户认证**：下拉切换开启/关闭；用户名、密码留空表示保持原值，填写则覆盖

这两个字段来自 `wizard/config`，与安装向导用的是同一套字段名（`wizard_port`、
`wizard_auth_enable`、`wizard_auth_user`、`wizard_auth_pass`），所以升级时可以平滑迁移。

## 实现要点

**端口如何做到「安装时可指定、装后可改」**

- 真实端口只存在一处：`$TRIM_PKGETC/settings.json`（`$TRIM_PKGETC` 走应用配置目录，升级不丢）
- 向导字段由生命周期脚本 `install_callback` / `config_callback` 写进该文件，写完按需重启
- `app/ui/config` 里入口端口写成 `${wizard_port}` —— 这是应用中心的运行时变量，
  所以桌面图标的地址会自动跟着向导里的端口走，**不需要重建包**
- `cmd/main` 启动时用 `app.py --print-port` 读取实际端口，`--check` 用来确认端口真的起来了

**认证如何实现**

- 密码以 `pbkdf2_sha256`（20 万次迭代 + 随机盐）存哈希，**明文不落盘、不进日志**
- 登录成功后下发 HMAC 签名的 Cookie；会话密钥由密码哈希派生，
  因此服务进程不需要写配置目录，避开安装脚本（root）与运行用户（package）权限不一致的坑
- 未登录访问任意路径 → `302 /login`；登录失败有次数限制提示

**为什么把界面打包进去**

桌面入口 `type=url` 指向服务自身端口，前端页面、图标都在 `app/ui/` 里，
所以这个应用**只依赖 NAS 本地**，不依赖外部站点存活。

## 在测试机上实测通过的项目

测试环境：飞牛 fnOS（`fnos-7500`，内核 6.18，`fnpack 1.2.4`，`appcenter-cli 1.0.2`），
安装位置 存储空间 1。

| 项目 | 结果 |
| --- | --- |
| `fnpack build` | ✅ 打包成功，`manifest.checksum` 与 `app.tgz` 的 MD5 一致 |
| 命令行安装（向导参数 port=10001 / auth=off / user=admin） | ✅ 装完 `settings.json` 按向导写入 |
| 应用中心「启用」 | ✅ 进程起来，`0.0.0.0:10001` 监听，`GET /` 返回 200（40997B） |
| 桌面图标 | ✅ 出现「二维码批量生成器」，点击打开 `http://<NAS>:10001/` |
| 实际生成二维码 | ✅ 4 条内容全部出码、预览正常、写入「最近生成」 |
| 应用中心显示 | ✅ 「运行设置」里正确渲染服务端口 + 用户认证（含说明文案） |
| 装后改端口（UI 改成 10005） | ✅ 自动重启、旧端口关闭、新端口监听；入口端口同步变为 10005 |
| 装后开启认证（UI 开启 + 设密码） | ✅ 未登录 302 → `/login`；密码错误提示「还可尝试 4 次」；密码正确进入应用 |
| 改回默认（10001 / 关闭认证） | ✅ 生效 |
| 状态检测 | ✅ `cmd/main status` 运行中返回 0、停止返回 3；`appcenter-cli status` 一致 |

## 已知限制与注意事项

1. **依赖系统 Python3**：需要 3.6+（飞牛自带 `/usr/bin/python3` 3.11）。
   `install_init` 会先检查，缺失则在安装阶段就失败并给出提示。
2. **`checkport = true`**：应用中心启动前会检查端口占用。
   如果你在应用中心之外手工起过进程，可能出现「端口被占用」提示，
   先在应用中心停用或结束该进程再启用即可。
3. **不要用 CSV/命令行卸载带向导的应用**，会提示改从 Web UI 卸载。
4. **二维码内容不落盘**：只在浏览器和草料开放平台公开接口之间流转，服务端只做静态托管与鉴权。
5. 升级路径：飞牛当前对第三方应用按「卸载 → 重新安装」处理；`upgrade_*` 脚本已实现，
   且升级不会覆盖你改过的端口与认证设置。
