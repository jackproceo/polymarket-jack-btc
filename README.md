# Polymarket Jack BTC

> 基于 Polymarket 预测市场的 BTC 价格追踪模拟交易系统

Polymarket Jack BTC 是一个自动化的 BTC 价格预测市场套利模拟程序。它从 Polymarket（加密预测市场平台）获取 BTC 价格相关的二元期权市场数据，通过插件化策略引擎（MACD / 双均线）生成买卖信号，执行模拟交易，并提供 Web Dashboard 实时监控。

---

## ✨ 功能特性

- 🔍 **自动发现 BTC 市场** — 启动时自动搜索 Polymarket 上活跃的 BTC 价格预测市场（如 "Bitcoin above $100K"）
- 📊 **实时K线数据** — 从 Polymarket Data API 获取 15 分钟级别 K 线数据
- 🧠 **插件化策略引擎** — 内置 MACD 和双均线策略，支持自定义策略扩展
- 💰 **多账户模拟交易** — 创建多个模拟账户，独立设置初始金额、下单金额
- 📈 **Web Dashboard** — 实时K线图（Chart.js）、账户统计、策略管理、交易历史、系统日志
- 🐳 **Docker 一键部署** — 提供 Dockerfile 和 docker-compose.yml

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- （可选）SOCKS5/HTTP 代理（国内环境需要）

### 本地运行

```bash
# 1. 克隆项目
git clone git@github.com:你的用户名/polymarket-jack-btc.git
cd polymarket-jack-btc

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量（海外服务器可跳过）
cp .env.example .env
# 编辑 .env，设置 PROXY_URL（国内环境必需）

# 4. 启动
python main.py

# 5. 访问 Dashboard
# 浏览器打开 http://127.0.0.1:5000
```

### Docker 部署

```bash
cp .env.example .env   # 按需配置 PROXY_URL
docker compose up -d
# 访问 http://服务器IP:5000
```

> 详细部署教程见 [Docker部署教程.md](Docker部署教程.md)

---

## 📁 项目结构

```
polymarket-jack-btc/
├── main.py                  # 程序入口
├── config.py                # 全局配置
├── requirements.txt         # Python 依赖
├── Dockerfile               # Docker 镜像
├── docker-compose.yml       # Docker 编排
├── .env.example             # 环境变量模板
├── data/                    # SQLite 数据目录
├── logs/                    # 日志目录
└── src/
    ├── api/                 # Polymarket API 封装
    ├── database/            # 数据库管理
    ├── strategy/            # 策略引擎（插件化）
    │   └── plugins/         # 策略实现
    ├── trading/             # 账户管理 + 交易模拟
    ├── utils/               # 日志工具
    └── web/                 # Flask Web Dashboard
        └── templates/       # 前端界面
```

---

## 🧠 内置策略

| 策略 | 名称 | 逻辑 |
|------|------|------|
| MACD 趋势跟踪 | `MACDStrategy` | MACD 金叉买入，死叉卖出；含趋势过滤（价格需高于 MA50） |
| 双均线趋势跟踪 | `MAStrategy` | 快线上穿慢线买入，下穿卖出；支持 SMA/EMA；含距离过滤 |

> 想要添加自定义策略？只需在 `src/strategy/plugins/` 下新建文件，继承 `BaseStrategy` 并实现 `generate_signal()` 方法即可，程序会自动发现并注册。

---

## 📖 文档

| 文档 | 说明 |
|------|------|
| [系统架构说明](系统架构说明.md) | 模块设计、数据库结构、API 路由、数据流 |
| [系统使用说明](系统使用说明.md) | Web Dashboard 操作指南 |
| [Docker部署教程](Docker部署教程.md) | 服务器部署完整教程 |

---

## ⚙️ 配置说明

核心配置在 `config.py` 中，关键项：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `BTC_MARKET.fetch_interval` | `60` | 数据获取间隔（秒） |
| `BTC_MARKET.interval` | `"15m"` | K线级别 |
| `ACCOUNT_DEFAULTS.initial_balance` | `1000` | 默认初始余额（USDC） |
| `ACCOUNT_DEFAULTS.trade_amount` | `50` | 默认每次交易金额（USDC） |
| `WEB.host` / `WEB.port` | `0.0.0.0:5000` | Web 服务监听地址 |

---

## 📄 许可证

MIT
