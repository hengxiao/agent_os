"""runtime 组装(DESIGN.md §14.2;M0 配置):KernelBuilder 链式组装 + TOML 配置文件等价组装。"""

from .builder import KernelBuilder
from .config import load_config

__all__ = ["KernelBuilder", "load_config"]
