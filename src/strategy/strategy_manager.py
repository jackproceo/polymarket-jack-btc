"""
策略管理器 - 管理账户-策略绑定关系的执行
"""
import time
import pandas as pd
from src.utils.logger import get_logger
from src.strategy.strategy_loader import discover_strategies, create_strategy_instance

logger = get_logger()


class StrategyManager:
    """策略管理器：为每个账户-策略绑定创建策略实例并执行"""

    def __init__(self, db_manager, polymarket_api):
        self.db = db_manager
        self.api = polymarket_api
        self._strategy_instances = {}
        self._market_cache = {}

        discover_strategies()
        logger.info("策略管理器初始化完成")

    def sync_strategies_to_db(self):
        """将发现的策略插件同步到数据库 strategies 表"""
        from src.strategy.strategy_loader import get_all_strategy_info
        strategies = get_all_strategy_info()
        for s in strategies:
            self.db.register_strategy(
                name=s["name"],
                display_name=s["display_name"],
                class_name=s["class_name"],
                module_path=s["module_path"],
                default_params=s["default_params"],
            )
        logger.info(f"已同步 {len(strategies)} 个策略到数据库")

    def _get_or_create_instance(self, account_id, strategy_id, custom_params=None):
        key = (account_id, strategy_id)
        if key not in self._strategy_instances:
            strategy_info = self.db.get_strategy(strategy_id)
            if not strategy_info:
                return None
            instance = create_strategy_instance(
                strategy_info["name"],
                params=custom_params or strategy_info["default_params"]
            )
            if instance:
                self._strategy_instances[key] = instance
            return instance

        instance = self._strategy_instances[key]
        if custom_params:
            instance.update_params(custom_params)
        return instance

    def run_all_enabled(self, market_id: str, btc_market_id: str = "BTCUSDT") -> list:
        """执行所有已启用的账户-策略绑定
        
        :param market_id: Polymarket 市场数据ID (slug)
        :param btc_market_id: BTC真实价格数据ID（用于技术指标计算）
        """
        bindings = self.db.get_enabled_bindings()
        if not bindings:
            logger.debug("没有已启用的策略绑定")
            return []

        # 加载 Polymarket 价格数据
        kline_data = self.db.get_market_data(market_id, limit=100)
        if not kline_data:
            logger.warning("没有市场数据，跳过策略执行")
            return []

        df = pd.DataFrame(kline_data)

        # 加载 BTC 真实价格数据（策略用此计算 MACD/MA）
        btc_data = self.db.get_market_data(btc_market_id, limit=100)
        btc_df = pd.DataFrame(btc_data) if btc_data else None

        results = []

        for binding in bindings:
            try:
                account_id = binding["account_id"]
                strategy_id = binding["strategy_id"]
                custom_params = binding.get("custom_params", {})

                instance = self._get_or_create_instance(
                    account_id, strategy_id, custom_params
                )
                if not instance or not instance.is_enabled():
                    continue

                # 传入 BTC 真实价格数据
                signal = instance.generate_signal(df, btc_df=btc_df)
                instance.last_signal = signal["action"]

                logger.info(
                    f"[策略信号] 账户={binding['account_name']} "
                    f"策略={binding['strategy_name']} "
                    f"信号={signal['action']} 原因={signal.get('reason', '')}"
                )

                results.append({
                    "account_id": account_id,
                    "strategy_id": strategy_id,
                    "binding_id": binding["id"],
                    "signal": signal,
                    "account_name": binding["account_name"],
                    "strategy_name": binding["strategy_name"],
                })

            except Exception as e:
                logger.error(f"执行策略绑定失败: {e}")

        return results

    def execute_signals(self, signals: list, simulator) -> list:
        """根据信号执行交易"""
        results = []
        for s in signals:
            signal = s["signal"]
            action = signal["action"]
            if action == "HOLD":
                continue
            try:
                if action == "BUY":
                    result = simulator.buy(
                        account_id=s["account_id"],
                        strategy_id=s["strategy_id"],
                        price=signal["price"],
                        reason=signal.get("reason", ""),
                    )
                elif action == "SELL":
                    result = simulator.sell(
                        account_id=s["account_id"],
                        strategy_id=s["strategy_id"],
                        price=signal["price"],
                        reason=signal.get("reason", ""),
                    )
                else:
                    continue

                if result:
                    results.append({"signal": s, "trade_result": result})
                    logger.info(f"[交易执行] {action} 账户={s['account_name']} 价格={signal['price']:.4f}")

            except Exception as e:
                logger.error(f"执行交易失败: {e}")

        return results

    def get_strategy_status(self, account_id: int = None) -> list:
        """获取策略运行状态"""
        if account_id:
            bindings = self.db.get_account_strategies(account_id)
        else:
            bindings = self.db.get_enabled_bindings()

        status = []
        for b in bindings:
            key = (b["account_id"], b["strategy_id"])
            instance = self._strategy_instances.get(key)
            status.append({
                "binding_id": b["id"],
                "account_id": b["account_id"],
                "account_name": b.get("account_name", ""),
                "strategy_id": b["strategy_id"],
                "strategy_name": b.get("strategy_name", b["name"]),
                "strategy_display_name": b.get("display_name", ""),
                "enabled": bool(b["enabled"]),
                "custom_params": b.get("custom_params", {}),
                "default_params": b.get("default_params", {}),
                "last_signal": instance.last_signal if instance else "HOLD",
                "trades_count": instance.trades_count if instance else 0,
            })
        return status
