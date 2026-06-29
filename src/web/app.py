"""
Flask Web Dashboard 后端
"""
import json
import time
from flask import Flask, render_template, request, jsonify
from src.utils.logger import get_logger

logger = get_logger()

# 全局引用（由 main.py 注入）
_db = None
_strategy_mgr = None
_simulator = None
_api = None
_current_price = 0.0
_market_id = ""
_market_slug = ""  # 当前市场slug，用于前端链接


def create_app(db_manager, strategy_manager, simulator, polymarket_api,
                market_id: str = "", market_slug: str = ""):
    """创建 Flask 应用"""
    global _db, _strategy_mgr, _simulator, _api, _market_id, _market_slug
    _db = db_manager
    _strategy_mgr = strategy_manager
    _simulator = simulator
    _api = polymarket_api
    _market_id = market_id
    _market_slug = market_slug

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ==================== 页面路由 ====================

    @app.route("/")
    def index():
        return render_template("index.html")

    # ==================== API 端点 ====================

    @app.route("/api/status")
    def api_status():
        """获取程序状态"""
        return jsonify({
            "status": "running",
            "current_price": _get_current_price(),
            "market_id": _market_id,
            "market_slug": _market_slug,
            "timestamp": int(time.time()),
        })

    # ---- 账户 API ----

    @app.route("/api/accounts", methods=["GET"])
    def api_get_accounts():
        accounts = _db.get_accounts()
        # 附加摘要信息
        price = _get_current_price()
        for a in accounts:
            summary = _simulator.get_account_summary(a["id"], price)
            if summary:
                a.update(summary)
        return jsonify(accounts)

    @app.route("/api/accounts", methods=["POST"])
    def api_create_account():
        data = request.json or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "账户名称不能为空"}), 400

        # 检查是否已存在
        existing = _db.get_accounts()
        if any(a["name"] == name for a in existing):
            return jsonify({"error": f"账户 '{name}' 已存在"}), 400

        balance = float(data.get("initial_balance", 1000.0))
        order_amount = float(data.get("order_amount", 100.0))
        account_id = _db.create_account(name, balance, order_amount)

        # 重新加载账户管理器
        _simulator.account_mgr.load_accounts()

        return jsonify({"success": True, "account_id": account_id})

    @app.route("/api/accounts/<int:account_id>")
    def api_get_account(account_id):
        account = _db.get_account(account_id)
        if not account:
            return jsonify({"error": "账户不存在"}), 404

        price = _get_current_price()
        summary = _simulator.get_account_summary(account_id, price)
        if summary:
            account.update(summary)
        return jsonify(account)

    # ---- 策略 API ----

    @app.route("/api/strategies", methods=["GET"])
    def api_get_strategies():
        """获取所有策略（从数据库）"""
        strategies = _db.get_strategies()
        return jsonify(strategies)

    @app.route("/api/account_strategy", methods=["POST"])
    def api_bind_strategy():
        """绑定账户-策略"""
        data = request.json or {}
        account_id = data.get("account_id")
        strategy_id = data.get("strategy_id")
        if not account_id or not strategy_id:
            return jsonify({"error": "缺少 account_id 或 strategy_id"}), 400

        try:
            bind_id = _db.bind_strategy(
                account_id=account_id,
                strategy_id=strategy_id,
                custom_params=data.get("custom_params"),
                allocated_funds=data.get("allocated_funds"),
            )
            return jsonify({"success": True, "binding_id": bind_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/account_strategy/<int:bind_id>", methods=["PUT"])
    def api_update_binding(bind_id):
        """更新账户-策略绑定（启用/参数/资金）"""
        data = request.json or {}
        update_fields = {}
        for key in ["enabled", "custom_params", "allocated_funds"]:
            if key in data:
                update_fields[key] = data[key]

        if not update_fields:
            return jsonify({"error": "没有可更新的字段"}), 400

        _db.update_account_strategy(bind_id, **update_fields)

        # 同步策略实例状态
        _sync_strategy_enabled(bind_id, update_fields.get("enabled"))

        return jsonify({"success": True})

    @app.route("/api/account_strategy/<int:bind_id>", methods=["DELETE"])
    def api_unbind_strategy(bind_id):
        """解绑账户-策略"""
        _db.unbind_strategy(bind_id)
        return jsonify({"success": True})

    @app.route("/api/account_strategy/status")
    def api_strategy_status():
        """获取所有策略运行状态"""
        account_id = request.args.get("account_id", type=int)
        status = _strategy_mgr.get_strategy_status(account_id)
        return jsonify(status)

    # ---- 市场数据 API ----

    @app.route("/api/prices")
    def api_get_prices():
        limit = request.args.get("limit", 200, type=int)
        data = _db.get_market_data(_market_id, limit=limit)
        return jsonify(data)

    @app.route("/api/current_price")
    def api_current_price():
        return jsonify({"price": _get_current_price()})

    # ---- 交易历史 API ----

    @app.route("/api/trades")
    def api_get_trades():
        account_id = request.args.get("account_id", type=int)
        limit = request.args.get("limit", 100, type=int)
        trades = _db.get_trades(account_id=account_id, limit=limit)
        return jsonify(trades)

    # ---- 日志 API ----

    @app.route("/api/logs")
    def api_get_logs():
        limit = request.args.get("limit", 200, type=int)
        level = request.args.get("level", "")
        logs = _get_recent_logs(limit=limit, level=level)
        return jsonify(logs)

    # ==================== 辅助函数 ====================

    def _get_current_price() -> float:
        global _current_price
        return _current_price

    def _sync_strategy_enabled(bind_id: int, enabled: int):
        """同步策略实例的启用状态"""
        bindings = _db.get_enabled_bindings()
        for b in bindings:
            if b["id"] == bind_id:
                # 找到对应的策略实例，更新状态
                from src.strategy.strategy_manager import StrategyManager
                key = (b["account_id"], b["strategy_id"])
                instances = _strategy_mgr._strategy_instances
                if key in instances:
                    instances[key].set_enabled(bool(enabled))
                break

    return app


def _get_recent_logs(limit: int = 200, level: str = "") -> list:
    """读取最近的日志行"""
    import config
    log_file = config.LOGGING["file"]
    if not log_file or not __import__("os").path.exists(log_file):
        return []

    logs = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            # 从后往前取
            for line in lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                if level and f"[{level.upper()}]" not in line:
                    continue
                logs.append(line)
    except Exception:
        pass
    return logs
