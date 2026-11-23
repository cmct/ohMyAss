"""应用元信息与资源路径。"""

import os
import re
import sys

APP_NAME = 'ohMyAss Noto'
APP_VERSION = 'v0.7.0'


def resource_path(relative_path: str) -> str:
    """返回打包后或源码运行时的资源绝对路径。"""
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def version_tuple(value: str):
    """把 v0.5.8 之类的版本号转成可比较的元组。"""
    numbers = re.findall(r'\d+', str(value or ''))
    return tuple(int(number) for number in numbers[:4]) or (0,)
