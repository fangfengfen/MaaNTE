"""
后台点击工具 - 通过 PostMessage 直接发送鼠标消息
不移动窗口、不抢占鼠标，真正的后台点击
"""

import ctypes
import ctypes.wintypes as wt
import json

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

user32 = ctypes.windll.user32

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001


def _make_lparam(x: int, y: int) -> int:
    """构造 lParam: LOWORD=x, HIWORD=y"""
    return (y << 16) | (x & 0xFFFF)


def _get_hwnd_from_controller(context: Context):
    """从 controller 获取窗口句柄"""
    # MaaFramework 的 Win32Controller 内部持有 hwnd
    # 通过 find_desktop_windows 重新查找
    from maa.toolkit import Toolkit
    windows = Toolkit.find_desktop_windows()
    for w in windows:
        if getattr(w, 'class_name', '') == 'UnrealWindow':
            return w.hwnd
    return None


def bg_click_at(context: Context, x: int, y: int):
    """后台点击指定坐标（客户区坐标）"""
    hwnd = _get_hwnd_from_controller(context)
    if not hwnd:
        return False

    lparam = _make_lparam(x, y)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    import time
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    return True


@AgentServer.custom_action("pinkpaw_bg_click")
class PinkPawBGClick(CustomAction):
    """
    后台点击 custom action
    通过 pipeline_override 传入坐标: {"custom_action_param": {"x": 950, "y": 340}}
    """

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        # 解析参数
        param = {}
        if hasattr(argv, 'custom_action_param') and argv.custom_action_param:
            try:
                param = json.loads(argv.custom_action_param) if isinstance(argv.custom_action_param, str) else argv.custom_action_param
            except (json.JSONDecodeError, TypeError):
                pass

        x = param.get("x", 640)
        y = param.get("y", 360)

        success = bg_click_at(context, x, y)
        return CustomAction.RunResult(success=success)
