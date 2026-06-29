# Docker 部署教程 — Polymarket Jack BTC

## 1. 环境要求

| 项目 | 最低版本 |
|------|----------|
| Docker | 20.10+ |
| Docker Compose | 2.0+ |
| 操作系统 | Linux (Ubuntu 20.04+ / Debian 11+ / CentOS 8+) |

**海外服务器**：推荐（Polymarket API 可直接访问，无需代理）  
**国内服务器**：需要配置 SOCKS5/HTTP 代理

---

## 2. 部署步骤

### 2.1 服务器准备

```bash
# 安装 Docker（Ubuntu/Debian）
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker --now

# 安装 Docker（CentOS 8+/Rocky）
sudo dnf install -y docker docker-compose
sudo systemctl enable docker --now

# 验证安装
docker --version
docker compose version
```

### 2.2 克隆项目

```bash
git clone git@github.com:你的用户名/polymarket-jack-btc.git
cd polymarket-jack-btc
```

### 2.3 配置环境变量

```bash
# 从模板创建 .env 文件
cp .env.example .env
```

根据服务器位置编辑 `.env`：

**海外服务器**（无需代理）：
```env
PROXY_URL=
```

**国内服务器**（需要代理）：
```env
PROXY_URL=socks5://你的代理IP:端口
```

### 2.4 拉取基础镜像并构建

```bash
# 拉取 Python 基础镜像
docker pull python:3.11-slim

# 构建项目镜像
docker compose build
```

### 2.5 启动服务

```bash
# 后台启动
docker compose up -d

# 查看日志
docker compose logs -f

# 看到以下输出表示启动成功：
# [INFO] Polymarket Jack BTC 套利模拟程序 启动
# [INFO] Web Dashboard 启动: http://0.0.0.0:5000
```

### 2.6 访问 Dashboard

```
http://服务器公网IP:5000
```

如果使用云服务器，确保安全组/防火墙已开放 **5000 端口**。

---

## 3. 常用运维命令

```bash
# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f

# 查看最近 100 行日志
docker compose logs --tail 100

# 重启服务（修改配置后）
docker compose restart

# 停止服务
docker compose stop

# 启动服务
docker compose start

# 完全停止并删除容器
docker compose down

# 重新构建并启动（修改代码后）
docker compose up -d --build
```

---

## 4. 数据持久化

`docker-compose.yml` 已将以下目录映射到宿主机：

```yaml
volumes:
  - ./data:/app/data    # SQLite 数据库（trades.db）
  - ./logs:/app/logs    # 运行日志（bot.log）
```

这意味着：
- 容器删除/重建后，**交易记录和账户数据不会丢失**
- 可通过宿主机直接查看日志：`tail -f logs/bot.log`
- 数据库文件位于宿主机 `data/trades.db`

### 备份数据库

```bash
# 复制数据库文件
cp data/trades.db data/trades.db.backup.$(date +%Y%m%d)

# 或使用 SQLite 命令导出
sqlite3 data/trades.db ".dump" > backup_$(date +%Y%m%d).sql
```

---

## 5. 反向代理（可选）

### Nginx 配置

如果希望使用域名访问并配置 HTTPS：

```nginx
server {
    listen 80;
    server_name btc-bot.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

配合 Certbot 自动配置 SSL：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d btc-bot.your-domain.com
```

---

## 6. 设置开机自启

`docker-compose.yml` 已配置 `restart: unless-stopped`，服务重启会自动恢复。

但 Docker 服务本身需要开机启动：

```bash
sudo systemctl enable docker
```

---

## 7. 升级更新

```bash
cd polymarket-jack-btc

# 拉取最新代码
git pull

# 重新构建并重启
docker compose up -d --build

# 确认运行正常
docker compose logs --tail 20
```

---

## 8. 多账户 / 多实例

如需同时追踪不同市场：

```bash
# 复制项目目录
cp -r polymarket-jack-btc polymarket-jack-btc-2
cd polymarket-jack-btc-2

# 修改 docker-compose.yml 中的端口和服务名
# container_name: polymarket-btc-bot-2
# ports: "5001:5000"

# 修改 config.py 中的端口
# WEB.port = 5001

docker compose up -d
```

---

## 9. 常见问题排查

### 容器启动后立即退出

```bash
# 查看退出日志
docker compose logs

# 常见原因：
# - .env 文件不存在 → cp .env.example .env
# - Python 依赖安装失败 → 检查网络
```

### 端口被占用

```bash
# 检查 5000 端口
sudo lsof -i :5000

# 修改 docker-compose.yml 端口映射
# ports:
#   - "8080:5000"   # 宿主机端口改为8080
```

### 代理连接失败

```bash
# 进入容器测试代理
docker compose exec polymarket-btc-bot python -c "
import os
os.environ['PROXY_URL']='socks5://代理IP:端口'
import requests
r = requests.get('https://api.github.com', proxies={'https': os.environ['PROXY_URL']}, timeout=10)
print(r.status_code)
"

# 如果失败，检查代理是否可从容器内访问（使用宿主机网络：host.docker.internal）
```

### 内存/CPU 占用过高

正常占用：内存 ~100MB，CPU ~1%。如果异常：
```bash
docker stats polymarket-btc-bot
```
检查 `config.py` 中 `fetch_interval` 是否设置过小（建议 ≥30 秒）。
