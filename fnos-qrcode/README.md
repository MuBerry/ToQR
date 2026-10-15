# 二维码批量生成器 · 飞牛 fnOS 部署包

> 对应 git 分支：`fnos-qrcode`（`main` 分支是开发主版本，本分支只多出这个部署包）

纯静态单文件页面，无后端、无构建、无本地依赖。放上去就能用，最终目标是拿到两个地址：

| 访问方式 | 地址示例 |
| --- | --- |
| **加飞牛链接的后缀** | `http://192.168.1.10:8090/fnos-qrcode/` |
| **下级域名** | `https://qrcode.你的域名.com/` |

---

## 0. 先纠正一个常见误解（很重要）

飞牛桌面本身跑在**系统内置的 nginx** 上（默认 `5666` 端口），但这个 nginx **官方不开放给用户管理**——配置在 `/usr/trim/nginx/conf/nginx.conf`，改它属于动系统，且系统升级可能被覆盖。

所以下面这条路是**走不通**的：

```
http://192.168.1.10:5666/fnos-qrcode/     ← 不要这样试
```

正确做法只有两条：

1. **另起一个端口**跑静态服务（方案 A，最省事，5 分钟）
2. 再用**反向代理**把域名/子域名指到那个端口（方案 B，想要「好看地址 + HTTPS」时再做）

方案 A 做完就已经能用了，方案 B 是可选的锦上添花。

---

## 1. 部署包结构

```
fnos-qrcode/
├── README.md                          # 本文档
├── docker-compose.yml                 # 方案 A：飞牛「容器管理 → 项目」直接用它
├── index.html                         # 应用本体（单文件版，想丢进已有 Web 目录时用这个）
├── nginx/
│   ├── default.conf                   # 容器内配置：根路径 + /fnos-qrcode/ 两个入口都通
│   └── reverse-proxy.conf             # 方案 B：已有 nginx 的反代片段
├── html/
│   └── fnos-qrcode/
│       └── index.html                 # 容器挂载用的副本（内容与上面完全一致）
└── sync.sh                            # 改完根目录主版本后，一键同步两份副本
```

> `index.html` 有三份（含仓库根目录那份），内容完全一致。维护时只改仓库根目录的 `index.html`，然后执行 `bash fnos-qrcode/sync.sh`。

---

## 2. 方案 A：起一个静态容器（推荐，先做这步）

### A1. 把文件传到飞牛

飞牛桌面 →「文件管理」，进到某个卷下面（社区习惯放在 `/vol1/1000/docker/`，`1000` 是你的用户 ID），新建目录 `fnos-qrcode`，把本目录的 **`html/`、`nginx/`、`docker-compose.yml`** 三样传进去。

也可以用 SSH / SMB 直接拷，最终目录长这样：

```
/vol1/1000/docker/fnos-qrcode/
├── docker-compose.yml
├── html/fnos-qrcode/index.html
└── nginx/default.conf
```

### A2. 创建项目

飞牛桌面 →「**容器管理**」→ 左侧「**项目**」→「**创建项目**」：

- **项目名称**：`fnos-qrcode`
- **项目路径**：选到刚上传的 `/vol1/1000/docker/fnos-qrcode`
- **内容**：把 `docker-compose.yml` 的内容整段粘进去
- 点「完成 / 构建」，等日志出现 `Container fnos-qrcode Started` 之类字样

> 如果容器起不来、报找不到 `./html`：把 compose 里的相对路径改成绝对路径，
> 例如 `- /vol1/1000/docker/fnos-qrcode/html:/usr/share/nginx/html:ro`。

### A3. 修权限（不做容易 403）

飞牛上通过 SMB / 文件管理上传的文件，权限经常不是 nginx 容器能读的。SSH 执行：

```bash
sudo setfacl -Rb /vol1/1000/docker/fnos-qrcode
sudo find /vol1/1000/docker/fnos-qrcode -type d -exec chmod 755 {} \;
sudo find /vol1/1000/docker/fnos-qrcode -type f -exec chmod 644 {} \;
```

### A4. 放行端口（开了防火墙才需要）

飞牛「系统设置 → 安全性 / 防火墙」里放行 `8090`（TCP）。

### A5. 访问

```
http://<飞牛IP>:8090/fnos-qrcode/
```

同时这两个地址也是通的（给后面的下级域名用）：

```
http://<飞牛IP>:8090/            ← 根路径直接出应用
http://<飞牛IP>:8090/index.html
```

> 端口冲突就把 `docker-compose.yml` 里 `"8090:80"` 左边的 `8090` 换掉，注意避开飞牛自己占用的 `5666`、`8000`、`16601` 等。

**做到这里，内网就已经能用了。** 下面是想用「干净域名 + HTTPS」时再做。

---

## 3. 方案 B：下级域名（反向代理）

### B1. 域名解析

给你的下级域名加解析，指向飞牛的地址：

| 类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| A | `qrcode` | 飞牛的公网 IPv4 |
| 或 AAAA | `qrcode` | 飞牛的公网 IPv6 |

如果用泛解析（`*.nas.你的域名.com`），就加一条 CNAME：

| 类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| CNAME | `*.nas` | `nas.你的域名.com` |

> 只有内网用的话，跳过解析，直接在内网 DNS 或各设备的 hosts 里把 `qrcode.home.lan` 指到飞牛内网 IP 也行。

### B2. 选一个反向代理工具

#### 方式一：Lucky（推荐，DDNS + 反代 + 证书一站式）

飞牛「应用中心」搜 Lucky 安装，或用「容器管理」加一个项目：

```yaml
services:
  lucky:
    image: gdy666/lucky
    container_name: lucky
    volumes:
      - ./lucky:/goodluck
    network_mode: host
    restart: always
```

然后：

1. 浏览器打开 `http://<飞牛IP>:16601`，账号密码默认都是 `666`，**进去第一件事改密码**
2. 想上 HTTPS：先进「安全管理 → SSL 证书」申请（ACME / DNS 验证）或导入证书
3. 进「**Web 服务**」→「添加 Web 服务规则」：监听端口填一个空闲高位端口（如 `56666`），勾上 TLS 并选证书
4. 在该规则下点「**添加子规则**」：
   - 服务类型：**反向代理**
   - 前端地址：`qrcode.你的域名.com`（不要带端口）
   - 后端地址：`http://127.0.0.1:8090`（**必须带 `http://`**）
5. 保存，访问 `https://qrcode.你的域名.com:56666`

> 想免端口访问，就把监听端口设成 `443`（前提是飞牛自身的 443 已经让出来）。

#### 方式二：1Panel + OpenResty

应用商店安装 OpenResty → 新建一个「反向代理」网站 → 域名填 `qrcode.你的域名.com` → 目标地址填 `http://127.0.0.1:8090` → 用 1Panel 的 SSL 功能申请证书。

#### 方式三：nginx-web-fnos（FPK 小应用）

到 GitHub Releases 下载对应架构的 `.fpk`（x86_64 / arm64）→ 飞牛「应用中心 → 手动安装」上传安装 → 打开 nginx-web →「HTTP(S) 代理」→ 添加规则：域名 `qrcode.你的域名.com`，监听端口如 `9080`，后端主机 `127.0.0.1`，后端端口 `8090`。

#### 方式四：你已经有 nginx

直接用本包里的 `nginx/reverse-proxy.conf` 片段，把 `root` 指向部署包里的 `html` 目录即可（里面两种写法都给了：子域名整站 / 子路径挂载）。

### B3. 结果

```
https://qrcode.你的域名.com/          ← 下级域名，地址栏干净
https://qrcode.你的域名.com/fnos-qrcode/   ← 同一份内容，两种地址都能进
```

---

## 4. 访问方式对照

| 场景 | 需要做 | 最终地址 |
| --- | --- | --- |
| 自己内网用 | 只做方案 A | `http://192.168.1.10:8090/fnos-qrcode/` |
| 内网 + 好记的名字 | 方案 A + 内网 DNS / hosts | `http://qrcode.home.lan/` |
| 公网 + HTTPS + 无端口 | 方案 A + 方案 B | `https://qrcode.你的域名.com/` |
| 只想临时看一眼 | 双击本地 `index.html` | `file:///.../index.html` |

---

## 5. 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| **403 Forbidden** | 文件权限没给对，回到 A3 执行那三条命令 |
| **404** | 地址写少了结尾斜杠；必须是 `/fnos-qrcode/`，不是 `/fnos-qrcode` |
| **打不开 / 超时** | 容器没起来（看容器日志），或防火墙没放行端口 |
| **页面出来了但二维码一直转圈** | 运行页面的浏览器必须能访问外网，二维码由草料接口实时生成：`https://api.2dcode.biz/v1/create-qr-code` |
| **点「下载全部」提示"当前环境拦截了跨域下载"** | 说明页面被嵌在沙箱 iframe（origin 为 null）里。用独立标签页访问就没有这个问题，正常环境会直接打包成 zip 下载 |
| **HTTPS 下有没有混合内容问题** | 没有。二维码接口和 JSZip 的 CDN 都是 https |
| **改了 index.html 不生效** | 强刷 Ctrl+F5。页面已设置 `Cache-Control: no-cache` |

---

## 6. 更新页面

```bash
# 1. 改仓库根目录的 index.html（开发主版本）
# 2. 同步到部署包
bash fnos-qrcode/sync.sh

# 3. 传回飞牛后：
#    改的是 html/ 目录里的文件 → 立刻生效（目录是实时挂载的），刷新浏览器即可
#    改的是 nginx/default.conf → 需要重启容器：
#    docker restart fnos-qrcode
```

---

## 7. 运行前提与数据说明

- 运行页面的浏览器需能访问外网——二维码由草料开放平台公开接口实时生成，无需 API Key：
  ```
  GET https://api.2dcode.biz/v1/create-qr-code?data=<内容>&size=400x400
  ```
- 「最近生成」记录只存在浏览器 `localStorage` 里，**不会写入 NAS**，也不会上传任何内容。
- 生成的是内容本身的**静态码**（文本/网址直接编码），不依赖本页面在线状态，扫码即可用。
- 单次上限 200 条；批量导入支持 `.txt / .csv / .tsv`，也支持把文件直接拖到输入框。
