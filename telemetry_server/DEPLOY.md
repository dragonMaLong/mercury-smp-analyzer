# MIP 压汞软件使用统计

此服务移植 BET 的统计后台，数据库、环境变量和协议应用标识独立。没有附带生产地址、密码、已有数据库或地域数据包；代码不会自动部署。

## 客户端

新安装默认勾选“启用使用统计”，状态栏“隐私设置”和齿轮中的统计标签暂时隐藏，设置功能保留。配置有效服务地址后才会记录和发送。用户已有的关闭选择会保留，并清空本机安装标识和待发送队列；重新开启会生成新标识。更换已有接收地址需要重新启用。

将根目录 `telemetry_config.example.json` 复制为 `telemetry_config.json`，设置 `endpoint` 为压汞独立接口，例如 `https://stats.example.org/mercury/api/v1/events`。环境变量 `MERCURY_TELEMETRY_URL` 优先于配置文件，EXE 旁的配置优先于包内配置；空地址明确禁用。生产环境只接受 HTTPS，本地测试可用 `http://127.0.0.1:端口/api/v1/events`。不会读取 BET 的环境变量、安装标识或设置。

客户端发送 `app_id=mercury_smp`。后台会拒绝其他应用以及缺少标识的批次，因此不能直接复用未升级的 BET 接口。统计只允许固定字段：启动环境、版本、随机标识、导入格式/仪器类别/结果/耗时、分析页面、Excel/PDF 导出结果、打开和活跃秒数。不发送样品名、文件名/路径、实验数值、异常正文或输入内容。

## 服务端

在独立的服务环境安装：

```sh
python -m pip install -r telemetry_server/requirements.txt
```

运行前设置 `MERCURY_TELEMETRY_DB` 为压汞独立 SQLite 路径，设置 `MERCURY_TELEMETRY_ADMIN_USER` 和 `MERCURY_TELEMETRY_ADMIN_PASSWORD` 为后台账号。管理密码只保存在服务器。Linux 可使用：

```sh
gunicorn --bind 127.0.0.1:8766 --workers 2 'telemetry_server:create_app()'
```

由 HTTPS 反向代理公开接口，后台 `/admin` 使用 HTTP Basic 认证，须置于 HTTPS 后。`/api/v1/events` 为客户端接口，`/admin/summary` 提供需认证的汇总 JSON，`/healthz` 为健康检查。反向代理应限流、限制请求体，并覆盖而非追加 `X-Forwarded-For`；只有单层本机反向代理才能设置 `MERCURY_TELEMETRY_TRUST_PROXY=1`。不要将此服务与 BET 指向同一个数据库。

后台保留 BET 的活跃安装量、版本/平台/语言/屏幕、导入与导出成功率、页面访问、按日/小时使用量、会话打开和活跃时长、IP 与地域统计及时间筛选。随机安装量不等于真实人数。服务端会收到连接 IP，沿用 BET 的长期保留策略；客户端详情页明确说明。数据库上限 512 MiB，应监控容量并自行安排受限访问的备份。`python -m telemetry_server purge` 只整理 SQLite WAL，不删除历史记录。

可选安装本地 `ip2region_v4.xdb` 和 `ip2region_v6.xdb`，通过 `MERCURY_TELEMETRY_IP_DATABASE_DIR` 指定目录。数据文件需由维护者自行获取和更新。地域查询只使用本地文件，不会将 IP 发送给第三方；缺少文件时显示未知。

## 验证与发布

`python -m pytest tests/test_telemetry.py tests/test_telemetry_schema.py tests/test_telemetry_server.py tests/test_telemetry_ip_location.py tests/test_telemetry_ui.py -q` 使用临时存储、内存 Flask 客户端和本机模拟传输，不发送真实用户统计。桌面打包需要包含 `mercury_telemetry` 包；服务端及其 Flask 依赖不属于桌面运行依赖。生产使用前须单独部署此服务并配置压汞接收地址。
