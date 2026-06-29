"""
交易模拟器 - 按账户执行交易
"""
from src.utils.logger import get_logger
from src.trading.account import AccountManager

logger = get_logger()


class TradingSimulator:
    """交易模拟器：按账户执行买卖操作，记录交易"""

    def __init__(self, db_manager, account_manager, config: dict):
        """
        :param db_manager: DBManager 实例
        :param account_manager: AccountManager 实例
        :param config: 交易配置（来自 config.py）
        """
        self.db = db_manager
        self.account_mgr = account_manager
        self.config = config
        self.fee_rate = config.get("fee_rate", 0.005)
        self.default_trade_amount = config.get("trade_amount", 100.0)  # 默认每次交易金额

    def buy(self, account_id: int, strategy_id: int,
             price: float, reason: str = "") -> dict:
        """
        执行买入
        :return: 交易结果 dict 或 None
        """
        account = self.account_mgr.get_account(account_id)
        if not account:
            logger.error(f"账户不存在: {account_id}")
            return None

        # 使用账户的下单金额，如果没有则使用默认值
        value = getattr(account, 'order_amount', self.default_trade_amount)

        # 检查余额
        if account.balance < value:
            logger.warning(
                f"账户 {account.name} 余额不足: "
                f"需要 {value:.2f}, 可用 {account.balance:.2f}"
            )
            return None

        result = account.buy(price, value, self.fee_rate)
        if not result["success"]:
            logger.warning(f"买入失败: {result['message']}")
            return result

        # 记录交易到数据库
        self.db.insert_trade(
            account_id=account_id,
            strategy_id=strategy_id,
            action="BUY",
            price=price,
            amount=result["amount"],
            value=value,
            pnl=None,
            reason=reason,
        )

        # 同步账户状态到数据库
        self.account_mgr.sync_to_db()

        return result

    def sell(self, account_id: int, strategy_id: int,
              price: float, reason: str = "") -> dict:
        """
        执行卖出
        :return: 交易结果 dict 或 None
        """
        account = self.account_mgr.get_account(account_id)
        if not account:
            logger.error(f"账户不存在: {account_id}")
            return None

        if account.position <= 0:
            logger.debug(f"账户 {account.name} 无持仓，跳过卖出")
            return None

        # 全部卖出
        result = account.sell(price, fee_rate=self.fee_rate)
        if not result["success"]:
            logger.warning(f"卖出失败: {result['message']}")
            return result

        # 记录交易到数据库
        self.db.insert_trade(
            account_id=account_id,
            strategy_id=strategy_id,
            action="SELL",
            price=price,
            amount=result["amount"],
            value=result["value"],
            pnl=result["pnl"],
            reason=reason,
        )

        # 同步账户状态到数据库
        self.account_mgr.sync_to_db()

        return result

    def update_snapshots(self, current_price: float):
        """为所有账户创建快照"""
        accounts = self.account_mgr.get_all_accounts()
        for account in accounts:
            unrealized_pnl = account.get_unrealized_pnl(current_price)
            total_pnl = account.get_total_pnl(current_price)
            equity = account.get_equity(current_price)

            self.db.insert_snapshot(
                account_id=account.account_id,
                balance=account.balance,
                position=account.position,
                unrealized_pnl=unrealized_pnl,
                total_pnl=total_pnl,
                equity=equity,
            )

    def get_account_summary(self, account_id: int, current_price: float) -> dict:
        """获取账户摘要（用于Web API）"""
        account = self.account_mgr.get_account(account_id)
        if not account:
            return None

        unrealized_pnl = account.get_unrealized_pnl(current_price)
        total_pnl = account.get_total_pnl(current_price)
        equity = account.get_equity(current_price)

        return {
            "account_id": account.account_id,
            "name": account.name,
            "initial_balance": account.initial_balance,
            "balance": round(account.balance, 4),
            "position": round(account.position, 4),
            "avg_cost": round(account.avg_cost, 6),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "equity": round(equity, 4),
            "pnl_pct": round(total_pnl / account.initial_balance * 100, 2),
        }

    def get_all_summaries(self, current_price: float) -> list:
        """获取所有账户摘要"""
        accounts = self.account_mgr.get_all_accounts()
        return [self.get_account_summary(a.account_id, current_price) for a in accounts]
