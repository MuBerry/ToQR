# 二维码批量生成器 · 飞牛 fnOS 静态部署包

## 这个目录是什么

- `index.html` —— 应用本体，**纯静态单文件**，无后端、无构建、无本地依赖，把文件放上去就能跑。
- 与项目根目录的 `index.html` **内容完全一致**（同一份代码，两个部署位置）。
- 页面内没有任何绝对路径，也**不引用任何外部资源**（二维码编码器与 zip 写入器都内嵌在页面里），因此放在**任意子路径**或**子域名**下都能正常工作。

## 运行前提

- **完全离线**：二维码由页面内置的编码器（QR Code Model 2，字节模式 / 纠错等级 M）在本机生成，不请求任何第三方接口，浏览器不需要外网；内容不出本机。

- 「最近生成」记录存在浏览器 localStorage 里，**不会写入 NAS**。

---

## 访问方式一：追加路径（最简单）

1. 打开 fnOS 的「文件管理」，进入 Web 服务目录（如 `trim_web/`、`www/`，或你自建站点的根目录）。
2. 新建目录 `fnos-index`，把本目录的 `index.html` 放进去。
3. 访问：`http://<飞牛IP>:<端口>/fnos-index/`

> 想让 URL 是 `http://<飞牛IP>/fnos-index.html`：把文件改名为 `fnos-index.html`，直接丢到站点根目录即可。

如果出现 404 或目录列表：

- 确认服务器开启了目录索引 `index index.html;`（nginx 默认开启）；
- 确认权限：文件 `644`、目录 `755`（`chmod 644 index.html`）。

---

## 访问方式二：下级域名（子域名）

前提：有自己的域名，并把 `fnos-index.你的域名` 解析到飞牛所在 IP（公网解析，或内网 DNS / hosts）。

nginx 片段：

```nginx
server {
    listen 80;
    server_name fnos-index.example.com;

    root  /vol1/1000/web/fnos-index;   # 换成你的实际路径
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

保存后执行 `nginx -s reload`，访问 `http://fnos-index.example.com/`。

也可以直接用 fnOS 自带的「反向代理 / 网站」功能新建站点，把域名指向本页实际监听的地址。

---

## 访问方式三：Docker（不想动系统自带 Web 服务）

```yaml
# docker-compose.yml
services:
  qr-fnos:
    image: nginx:alpine
    container_name: qr-fnos
    ports:
      - "8090:80"
    volumes:
      - ./index.html:/usr/share/nginx/html/index.html:ro
    restart: unless-stopped
```

在 fnOS 的 Docker / Compose 里启动后访问 `http://<飞牛IP>:8090/`。
若要绑子域名，把 `ports` 改成 `expose`，再用反向代理指向 `http://127.0.0.1:8090`。

---

## 常见问题

**1. 下载不生效**

二维码是页面自己生成的 data URL，再打包成 zip 下载，全程不出浏览器，**子路径、子域名访问都能正常下载**。
只有把页面嵌在**沙箱 iframe**（origin 为 `null`）里、或浏览器策略禁止多文件下载时才会被拦——换独立标签页打开即可。

**2. HTTPS**

页面不引用任何外部资源，http 或 https 下都一样；不存在混合内容问题。
若用 https 子域名访问，需先配好证书，否则浏览器会拦截。

**3. 扫码打不开？**

生成的是内容本身的**静态码**（文本/网址直接编码），不依赖本页在线状态，扫码即可用。

**4. 怎么更新页面**

直接覆盖 `index.html`，然后 Ctrl+F5 强制刷新清缓存即可。

**5. 单次能生成多少条**

单次上限 200 条；超量会提示并截取前 200 条。批量导入支持 `.txt / .csv / .tsv`，也支持把文件直接拖到输入框。

---

## 目录结构

```
fnos-index/
├── index.html    # 应用本体（与项目根目录 index.html 保持一致）
└── README.md     # 本文档
```

> 维护提示：根目录的 `index.html` 是主版本，改完后记得重新复制一份到 `fnos-index/index.html`，两边保持同步。
