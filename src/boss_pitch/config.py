"""boss-pitch 配置管理。

只保留浏览器和路径配置，不涉及 LLM。

配置来源优先级：环境变量 > config.yaml > 默认值。
环境变量前缀：BOSS_PITCH_
配置文件路径：~/.boss-pitch/config.yaml
"""

from pathlib import Path

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """boss-pitch 配置。"""

    # ── 浏览器配置 ──
    cdp_host: str = "localhost"
    cdp_port: int = 9222
    chrome_path: str = ""  # 空=自动检测
    user_data_dir: str = ""  # 空=~/.boss-pitch/chrome-data

    # ── 路径配置 ──
    base_dir: Path = Path("~/.boss-pitch").expanduser()
    profile_path: Path = Path("~/.boss-pitch/profile.yaml").expanduser()

    # ── 搜索配置 ──
    search_delay: float = 3.0  # 翻页间隔（秒）

    # ── 发送配置 ──
    send_delay: float = 10.0  # 发送间隔（秒）

    model_config = {"env_prefix": "BOSS_PITCH_"}


def get_config() -> Config:
    """获取配置单例。

    Returns:
        Config: 配置对象
    """
    return Config()
