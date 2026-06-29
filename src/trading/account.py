"""
模拟账户类 - 多账户支持
"""
from src.utils.logger import get_logger

logger = get_logger()


class Account:
    """单个模拟账户"""

    def __init__(self, account_id: int, name: str,
                 initial_balance: float = 1000.0,
                 balance: float = 1000.0,
                 position: float = 0.0,
                 avg_cost: float = 0.0,
                 order_amount: float = 100.0):
        self.account_id = account_id
        self.name = name
        self.initial_balance = initial_balance
        self.balance = balance          # 可用余额（USDC）
        self.position = position        # 持仓量（BTC份额，0~1）
        self.avg_cost = avg_cost       # 持仓均价
        self.order_amount = order_amount  # 每次下单金额

    def buy(self, price: float, value: float, fee_rate: float = 0.005) -> dict:
        """
        买入操作
        :param price: 买入价格（预测市场份额价格，0~1）
        :param value: 买入金额（USDC）
        :param fee_rate: 手续费率
        :return: {"success": bool, "amount": float, "cost": float, "fee": float, "message": str}
        """
        if self.balance < value:
            return {
                "success": False,
                "message": f"余额不足: 需要 {value:.2f} USDC, 可用 {self.balance:.2f} USDC",
            }

        fee = value * fee_rate
        cost = value + fee
        actual_value = value - fee
        amount = actual_value / price if price > 0 else 0  # 买入的份额数量

        # 更新持仓（加权平均成本）
        old_total_cost = self.avg_cost * self.position
        new_total_cost = old_total_cost + actual_value
        new_position = self.position + amount

        self.balance -= cost
        self.avg_cost = new_total_cost / new_position if new_position > 0 else 0
        self.position = new_position

        logger.info(
            f"[账户 {self.name}] 买入: 价格={price:.4f}, "
            f"金额={value:.2f}, 份额={amount:.4f}, "
            f"手续费={fee:.4f}, 新持仓={self.position:.4f}"
        )

        return {
            "success": True,
            "action": "BUY",
            "price": price,
            "amount": amount,
            "value": value,
            "fee": fee,
            "cost": cost,
            "new_balance": self.balance,
            "new_position": self.position,
            "new_avg_cost": self.avg_cost,
        }

    def sell(self, price: float, amount: float = None,
              fee_rate: float = 0.005) -> dict:
        """
        卖出操作
        :param price: 卖出价格
        :param amount: 卖出份额数量（None=全部卖出）
        :param fee_rate: 手续费率
        :return: {"success": bool, "pnl": float, ...}
        """
        if amount is None:
            amount = self.position

        if self.position < amount:
            return {
                "success": False,
                "message": f"持仓不足: 需要 {amount:.4f}, 持仓 {self.position:.4f}",
            }

        sell_value = amount * price
        fee = sell_value * fee_rate
        net_value = sell_value - fee
        pnl = (price - self.avg_cost) * amount - fee

        # 更新持仓
        self.balance += net_value
        self.position -= amount

        # 如果全部卖出，重置均价
        if abs(self.position) < 1e-8:
            self.position = 0.0
            self.avg_cost = 0.0

        logger.info(
            f"[账户 {self.name}] 卖出: 价格={price:.4f}, "
            f"份额={amount:.4f}, 收入={net_value:.2f}, "
            f"盈亏={pnl:.4f}, 新余额={self.balance:.2f}"
        )

        return {
            "success": True,
            "action": "SELL",
            "price": price,
            "amount": amount,
            "value": sell_value,
            "fee": fee,
            "pnl": pnl,
            "net_value": net_value,
            "new_balance": self.balance,
            "new_position": self.position,
        }

    def get_unrealized_pnl(self, current_price: float) -> float:
        """计算未实现盈亏"""
        if self.position <= 0 or current_price <= 0:
            return 0.0
        return (current_price - self.avg_cost) * self.position

    def get_total_pnl(self, current_price: float) -> float:
        """计算总盈亏（已实现 + 未实现）"""
        unrealized = self.get_unrealized_pnl(current_price)
        # 已实现盈亏通过 trades 表计算，这里简化为余额变化
        return self.balance + self.position * current_price - self.initial_balance

    def get_equity(self, current_price: float) -> float:
        """计算账户总权益"""
        return self.balance + self.position * current_price

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "initial_balance": self.initial_balance,
            "balance": self.balance,
            "position": self.position,
            "avg_cost": self.avg_cost,
        }


class AccountManager:
    """多账户管理器"""

    def __init__(self, db_manager):
        self.db = db_manager
        self._accounts = {}  # {account_id: Account}

    def load_accounts(self):
        """从数据库加载所有账户到内存"""
        accounts_data = self.db.get_accounts()
        self._accounts.clear()
        for ad in accounts_data:
            self._accounts[ad["id"]] = Account(
                account_id=ad["id"],
                name=ad["name"],
                initial_balance=ad["initial_balance"],
                balance=ad["balance"],
                position=ad["position"],
                avg_cost=ad["avg_cost"],
                order_amount=ad.get("order_amount", 100.0),
            )
        logger.info(f"已加载 {len(self._accounts)} 个账户")

    def get_account(self, account_id: int) -> Account:
        """获取账户对象"""
        if account_id not in self._accounts:
            self.load_accounts()  # 重新加载
        return self._accounts.get(account_id)

    def get_all_accounts(self) -> list:
        """获取所有账户"""
        return list(self._accounts.values())

    def sync_to_db(self):
        """将内存中的账户状态同步到数据库"""
        for account in self._accounts.values():
            self.db.update_account(
                account.account_id,
                balance=account.balance,
                position=account.position,
                avg_cost=account.avg_cost,
            )
