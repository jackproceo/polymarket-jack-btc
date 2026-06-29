"""测试代理配置是否生效"""
import os
from pathlib import Path

# 加载 .env
from dotenv import load_dotenv
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
    print(f"已加载 .env 文件: {_env_file}")
else:
    print(f".env 文件不存在: {_env_file}")

# 检查代理配置
proxy_url = os.getenv("PROXY_URL", "").strip()
print(f"PROXY_URL: '{proxy_url}'")

if proxy_url:
    proxies = {"http": proxy_url, "https": proxy_url}
    print(f"代理配置: {proxies}")
    
    # 测试代理
    import requests
    try:
        resp = requests.get("https://gamma-api.polymarket.com/markets?tag=BTC&limit=1", 
                          proxies=proxies, timeout=10)
        print(f"API 请求成功! 状态码: {resp.status_code}")
        print(f"返回数据: {resp.text[:200]}")
    except Exception as e:
        print(f"API 请求失败: {e}")
else:
    print("代理未配置，将直连")
    import requests
    try:
        resp = requests.get("https://gamma-api.polymarket.com/markets?tag=BTC&limit=1", 
                          timeout=10)
        print(f"API 请求成功! 状态码: {resp.status_code}")
    except Exception as e:
        print(f"API 请求失败: {e}")
