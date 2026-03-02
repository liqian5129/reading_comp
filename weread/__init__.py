"""
微信读书集成模块
"""
from .client import WeReadClient
from .models import WeReadBook, WeReadHighlight, WeReadNote, WeReadProgress
from .storage import WeReadStorage

__all__ = [
    "WeReadClient",
    "WeReadStorage",
    "WeReadBook",
    "WeReadHighlight",
    "WeReadNote",
    "WeReadProgress",
]
