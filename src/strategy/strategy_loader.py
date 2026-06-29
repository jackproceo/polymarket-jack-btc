"""
策略加载器 - 自动发现并加载 plugins/ 目录下的策略插件
"""
import os
import sys
import importlib
import inspect
from src.utils.logger import get_logger

logger = get_logger()

# 插件目录
PLUGINS_DIR = os.path.dirname(__file__) + "/plugins"

# 全局策略注册表 {name: strategy_class}
_strategy_registry = {}


def discover_strategies() -> dict:
    """
    扫描 plugins/ 目录，自动发现所有继承 BaseStrategy 的类
    返回: {name: {"class": class_obj, "module": module_path, ...}}
    """
    global _strategy_registry
    _strategy_registry.clear()

    if not os.path.exists(PLUGINS_DIR):
        logger.warning(f"策略插件目录不存在: {PLUGINS_DIR}")
        return {}

    # 确保 plugins 目录在 Python 路径中
    plugins_parent = os.path.dirname(PLUGINS_DIR)
    if plugins_parent not in sys.path:
        sys.path.insert(0, plugins_parent)

    for filename in os.listdir(PLUGINS_DIR):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        module_name = f"src.strategy.plugins.{filename[:-3]}"
        try:
            # 如果已导入则重载，否则导入
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)

            module = sys.modules[module_name]

            # 查找模块中所有继承 BaseStrategy 的类
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module_name and \
                   hasattr(obj, "name") and obj.name:
                    _strategy_registry[obj.name] = {
                        "class": obj,
                        "display_name": obj.display_name,
                        "description": obj.description,
                        "default_params": obj.default_params,
                        "module_path": module_name,
                        "class_name": name,
                    }
                    logger.info(f"发现策略插件: {obj.name} ({obj.display_name})")

        except Exception as e:
            logger.error(f"加载策略插件失败 {filename}: {e}")

    logger.info(f"共发现 {len(_strategy_registry)} 个策略插件")
    return _strategy_registry


def get_strategy_class(name: str):
    """根据策略名称获取策略类"""
    if name in _strategy_registry:
        return _strategy_registry[name]["class"]
    return None


def get_all_strategy_info() -> list:
    """获取所有已发现策略的信息"""
    return [
        {
            "name": info["class"].name,
            "display_name": info["display_name"],
            "description": info["description"],
            "default_params": info["default_params"],
            "module_path": info["module_path"],
            "class_name": info["class_name"],
        }
        for info in _strategy_registry.values()
    ]


def create_strategy_instance(name: str, params: dict = None):
    """
    创建策略实例
    :param name: 策略名称（如 "MACD"）
    :param params: 策略参数（覆盖默认参数）
    :return: 策略实例 或 None
    """
    info = _strategy_registry.get(name)
    if not info:
        logger.error(f"未找到策略: {name}")
        return None

    try:
        instance = info["class"](params=params)
        return instance
    except Exception as e:
        logger.error(f"创建策略实例失败 {name}: {e}")
        return None
