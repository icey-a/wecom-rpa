#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量坐标工具:按提示量好后,直接写入 config.json(保留 key 等其他配置)。
Windows 下会自动记录企业微信窗口位置,之后窗口挪动输入框点击自动跟随。
Mac 下输入框用绝对坐标。
"""
import json
import os
import sys
import time

import pyautogui

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")


def get_wecom_rect():
    if sys.platform != "win32":
        return None
    try:
        import win32gui
    except ImportError:
        print("没装 pywin32,跳过窗口自动定位(pip install pywin32 可补)。")
        return None
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "企业微信" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception as e:
        print("取窗口失败:", e)
        return None
    if not found:
        print("没找到企业微信窗口,请先打开并登录。")
        return None
    return list(win32gui.GetWindowRect(found[0]))


def wait_point(prompt):
    input(prompt + " —— 鼠标悬停好后按回车…")
    time.sleep(0.2)
    return pyautogui.position()


def main():
    cfg = {}
    if os.path.exists(CFG_PATH):
        cfg = json.load(open(CFG_PATH, encoding="utf-8"))

    rect = get_wecom_rect()
    if rect:
        print(f"检测到企业微信窗口: {rect}")
        print("注意:量坐标时别缩放窗口,挪动位置没关系。")
    else:
        print("将使用绝对屏幕坐标(窗口挪动后需重跑)。")

    print("\n依次量三个区域:")
    x1, y1 = wait_point("1) 聊天消息区左上角")
    x2, y2 = wait_point("2) 聊天消息区右下角")
    x3, y3 = wait_point("3) 顶部标题栏(显示会话名的那一行)左上角")
    x4, y4 = wait_point("4) 标题栏右下角")
    x5, y5 = wait_point("5) 聊天输入框中心点")

    cfg["chat_region"] = [x1, y1, x2 - x1, y2 - y1]
    cfg["title_region"] = [x3, y3, x4 - x3, y4 - y3]
    if rect:
        cfg["window_rect"] = rect
        cfg["input_box"] = [x5 - rect[0], y5 - rect[1]]  # 记相对坐标,窗口挪动自动跟随
        print("输入框已记为相对坐标。")
    else:
        cfg["input_box"] = [x5, y5]

    json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n已写入 config.json。")
    if not cfg.get("service_chats"):
        print("下一步:用记事本打开 config.json,在 service_chats 里填客服会话名,")
        print('例如: "service_chats": ["张三", "售后群"]')
        print("只有这些会话会被看/学/回,私人聊天自动跳过,一个字都不记。")


if __name__ == "__main__":
    main()
