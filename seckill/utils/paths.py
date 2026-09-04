"""路径与资源定位。

把「只读资源」（icons、config 默认配置）和「可写数据」（登录态、日志、用户配置）
分开管理，二者都不依赖当前工作目录，打包成 exe 后同样有效。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

APP_NAME: Final = "SecKill"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """随程序分发的资源目录（icons/、config/）。

    打包后优先用 exe 同级目录里的 config/——那个是给用户改的；
    只有当它不存在时才回落到 PyInstaller 解包出来的临时目录。
    """
    if is_frozen():
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "config").exists() or (exe_dir / "icons").exists():
            return exe_dir
        return Path(getattr(sys, "_MEIPASS", exe_dir))
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """可写数据目录：登录态、日志、用户配置覆盖。

    Windows 下放 %LOCALAPPDATA%\\SecKill，其它平台放 ~/.seckill。
    """
    override = os.environ.get("SECKILL_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    else:
        base = Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sessions_dir() -> Path:
    p = data_dir() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_config_path() -> Path:
    return data_dir() / "config.yaml"


def asset(*parts: str) -> str:
    """定位一个随程序分发的资源，返回字符串路径（Qt 接口常用）。"""
    return str(resource_dir().joinpath(*parts))
