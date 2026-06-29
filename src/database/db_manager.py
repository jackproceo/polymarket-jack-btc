"""
数据库管理模块 - SQLite
多账户、多策略、市场价格、交易记录
"""
import sqlite3
import json
import time
import os
import threading
from contextlib import contextmanager

from src.utils.logger import get_logger

logger = get_logger()


class DBManager:
    """SQLite 数据库管理器（线程安全）"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接的上下文管理器"""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_db(self):
        """创建所有表"""
        with self._get_conn() as conn:
            conn.executescript("""
            -- 账户表
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                initial_balance REAL DEFAULT 1000.0,
                balance REAL DEFAULT 1000.0,
                position REAL DEFAULT 0.0,
                avg_cost REAL DEFAULT 0.0,
                order_amount REAL DEFAULT 100.0,
                created_at INTEGER,
                updated_at INTEGER
            );

            -- 策略注册表（插件化自动注册）
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT,
                class_name TEXT NOT NULL,
                module_path TEXT NOT NULL,
                default_params TEXT,
                created_at INTEGER
            );

            -- 账户-策略关联表（多对多核心表）
            CREATE TABLE IF NOT EXISTS account_strategy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                strategy_id INTEGER NOT NULL,
                enabled INTEGER DEFAULT 0,
                custom_params TEXT,
                allocated_funds REAL,
                created_at INTEGER,
                updated_at INTEGER,
                FOREIGN KEY (account_id) REFERENCES accounts(id),
                FOREIGN KEY (strategy_id) REFERENCES strategies(id),
                UNIQUE(account_id, strategy_id)
            );

            -- 市场价格数据
            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                UNIQUE(market_id, timestamp)
            );

            -- 交易记录
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                strategy_id INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                value REAL NOT NULL,
                pnl REAL,
                reason TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id),
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            );

            -- 账户快照
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                balance REAL NOT NULL,
                position REAL NOT NULL,
                unrealized_pnl REAL,
                total_pnl REAL,
                equity REAL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );
            """)

            # 数据库迁移：为已存在的表添加新字段
            self._migrate_db(conn)

    def _migrate_db(self, conn):
        """数据库迁移：为已存在的表添加新字段"""
        # 检查并添加 accounts 表的 order_amount 字段
        cursor = conn.execute("PRAGMA table_info(accounts)")
        columns = [row[1] for row in cursor.fetchall()]
        if "order_amount" not in columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN order_amount REAL DEFAULT 100.0")
            logger.info("已迁移数据库: 添加 accounts.order_amount 字段")

        # 检查并添加 account_strategy 表的 allocated_funds 字段
        cursor = conn.execute("PRAGMA table_info(account_strategy)")
        columns = [row[1] for row in cursor.fetchall()]
        if "allocated_funds" not in columns:
            conn.execute("ALTER TABLE account_strategy ADD COLUMN allocated_funds REAL")
            logger.info("已迁移数据库: 添加 account_strategy.allocated_funds 字段")

    # ==================== 账户操作 ====================

    def create_account(self, name: str, initial_balance: float = 1000.0, order_amount: float = 100.0) -> int:
        """创建账户，返回账户ID"""
        now = int(time.time())
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO accounts (name, initial_balance, balance, order_amount, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (name, initial_balance, initial_balance, order_amount, now, now)
            )
            return cur.lastrowid

    def get_accounts(self) -> list:
        """获取所有账户"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def get_account(self, account_id: int) -> dict:
        """获取单个账户"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            return dict(row) if row else None

    def update_account(self, account_id: int, **kwargs):
        """更新账户字段"""
        now = int(time.time())
        fields = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [now, account_id]
        with self._get_conn() as conn:
            conn.execute(f"UPDATE accounts SET {fields}, updated_at=? WHERE id=?", values)

    # ==================== 策略操作 ====================

    def register_strategy(self, name: str, display_name: str,
                         class_name: str, module_path: str, default_params: dict) -> int:
        """注册策略插件（已存在则更新）"""
        now = int(time.time())
        params_json = json.dumps(default_params)
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM strategies WHERE name=?", (name,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE strategies SET display_name=?, class_name=?, module_path=?, default_params=?, created_at=? WHERE name=?",
                    (display_name, class_name, module_path, params_json, now, name)
                )
                return row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO strategies (name, display_name, class_name, module_path, default_params, created_at) VALUES (?,?,?,?,?,?)",
                    (name, display_name, class_name, module_path, params_json, now)
                )
                return cur.lastrowid

    def get_strategies(self) -> list:
        """获取所有策略"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM strategies ORDER BY id").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["default_params"] = json.loads(d["default_params"]) if d["default_params"] else {}
                result.append(d)
            return result

    def get_strategy(self, strategy_id: int) -> dict:
        """获取单个策略"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,)).fetchone()
            if row:
                d = dict(row)
                d["default_params"] = json.loads(d["default_params"]) if d["default_params"] else {}
                return d
            return None

    # ==================== 账户-策略绑定 ====================

    def bind_strategy(self, account_id: int, strategy_id: int,
                      custom_params: dict = None, allocated_funds: float = None) -> int:
        """绑定账户-策略关系"""
        now = int(time.time())
        params_json = json.dumps(custom_params) if custom_params else None
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO account_strategy
                   (account_id, strategy_id, custom_params, allocated_funds, created_at, updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (account_id, strategy_id, params_json, allocated_funds, now, now)
            )
            return cur.lastrowid

    def update_account_strategy(self, bind_id: int, **kwargs):
        """更新账户-策略绑定（启用状态、参数、资金）"""
        now = int(time.time())
        fields = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [now, bind_id]
        with self._get_conn() as conn:
            conn.execute(f"UPDATE account_strategy SET {fields}, updated_at=? WHERE id=?", values)

    def get_account_strategies(self, account_id: int) -> list:
        """获取账户绑定的所有策略"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT ac.*, s.name, s.display_name, s.class_name, s.module_path, s.default_params
                   FROM account_strategy ac
                   JOIN strategies s ON s.id = ac.strategy_id
                   WHERE ac.account_id=?""",
                (account_id,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["default_params"] = json.loads(d["default_params"]) if d["default_params"] else {}
                d["custom_params"] = json.loads(d["custom_params"]) if d["custom_params"] else {}
                result.append(d)
            return result

    def get_enabled_bindings(self) -> list:
        """获取所有已启用的账户-策略绑定"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT ac.*, a.name as account_name, s.name as strategy_name,
                          s.class_name, s.module_path, s.default_params
                   FROM account_strategy ac
                   JOIN accounts a ON a.id = ac.account_id
                   JOIN strategies s ON s.id = ac.strategy_id
                   WHERE ac.enabled=1"""
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["default_params"] = json.loads(d["default_params"]) if d["default_params"] else {}
                d["custom_params"] = json.loads(d["custom_params"]) if d["custom_params"] else {}
                result.append(d)
            return result

    def unbind_strategy(self, bind_id: int):
        """解绑账户-策略"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM account_strategy WHERE id=?", (bind_id,))

    # ==================== 市场数据 ====================

    def insert_market_data(self, market_id: str, data: list) -> int:
        """
        批量插入K线数据，去重
        data: [{"timestamp": int, "open": float, "high": float, "low": float, "close": float, "volume": float}]
        """
        inserted = 0
        with self._get_conn() as conn:
            for d in data:
                try:
                    conn.execute(
                        """INSERT INTO market_data (market_id, timestamp, open, high, low, close, volume)
                           VALUES (?,?,?,?,?,?,?)""",
                        (market_id, d["timestamp"], d["open"], d["high"], d["low"], d["close"], d.get("volume", 0))
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass  # 已存在，跳过
        return inserted

    def get_market_data(self, market_id: str, limit: int = 200) -> list:
        """获取最新K线数据"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM market_data WHERE market_id=? ORDER BY timestamp DESC LIMIT ?",
                (market_id, limit)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # ==================== 交易记录 ====================

    def insert_trade(self, account_id: int, strategy_id: int,
                     action: str, price: float, amount: float,
                     value: float, pnl: float = None, reason: str = "") -> int:
        """记录交易"""
        now = int(time.time())
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades (account_id, strategy_id, timestamp, action, price, amount, value, pnl, reason)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (account_id, strategy_id, now, action, price, amount, value, pnl, reason)
            )
            return cur.lastrowid

    def get_trades(self, account_id: int = None, limit: int = 50) -> list:
        """获取交易历史"""
        with self._get_conn() as conn:
            if account_id:
                rows = conn.execute(
                    """SELECT t.*, a.name as account_name, s.display_name as strategy_name
                       FROM trades t
                       JOIN accounts a ON a.id = t.account_id
                       JOIN strategies s ON s.id = t.strategy_id
                       WHERE t.account_id=?
                       ORDER BY t.timestamp DESC LIMIT ?""",
                    (account_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT t.*, a.name as account_name, s.display_name as strategy_name
                       FROM trades t
                       JOIN accounts a ON a.id = t.account_id
                       JOIN strategies s ON s.id = t.strategy_id
                       ORDER BY t.timestamp DESC LIMIT ?""",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ==================== 账户快照 ====================

    def insert_snapshot(self, account_id: int, balance: float, position: float,
                       unrealized_pnl: float, total_pnl: float, equity: float):
        """记录账户快照"""
        now = int(time.time())
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO account_snapshots (account_id, timestamp, balance, position, unrealized_pnl, total_pnl, equity)
                   VALUES (?,?,?,?,?,?,?)""",
                (account_id, now, balance, position, unrealized_pnl, total_pnl, equity)
            )

    def get_latest_snapshot(self, account_id: int) -> dict:
        """获取最新账户快照"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM account_snapshots WHERE account_id=? ORDER BY timestamp DESC LIMIT 1",
                (account_id,)
            ).fetchone()
            return dict(row) if row else None
