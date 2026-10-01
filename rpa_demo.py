#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
企业微信客服 RPA Demo（v2）
- 自动定位企业微信窗口:挪动位置自动跟随;被遮挡也能截(Windows);
  最小化时自动暂停并提示,恢复后继续。
- 隐私:只从 config.json 里 service_chats 列出的客服会话学习你的话术风格,
  私人聊天一个字不进风格库;看消息和生成话术对所有会话都生效。
- 学习:在客服会话里,把你手动回的话按气泡左右位置识别出来,攒成风格库,
  下次生成话术时附上最近几条做范例,越用越像你。
- 流程:发现客户新消息(排队) -> AI 生成话术 -> 弹窗确认(可改字)
  -> 发送 -> 记录写入 replies.log。常驻控制窗可随时暂停/恢复/退出。
"""

import json as _json
import os as _os
import sys as _sys
import time
import traceback
from datetime import datetime

import pyautogui

_IS_WIN = _sys.platform == "win32"

# 强制 stdout/stderr 用 UTF-8:在英文 Windows 上 stdout 被重定向到文件时,
# 默认编码是 cp1252,中文 print 会直接抛 UnicodeEncodeError 导致闪退。
# (控制台运行时走 WriteConsoleW 不受影响,所以之前没暴露。)
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass
del _s

# ================= CONFIG 默认值 =================
CONFIG_DEFAULTS = {
    # 聊天消息区域 (左, 上, 宽, 高),calibrate.py 会自动写入
    "chat_region": (100, 200, 600, 500),
    # 会话标题栏区域 (左, 上, 宽, 高),用来识别当前是哪个会话,calibrate.py 写入
    "title_region": (100, 100, 300, 40),
    # 输入框位置:Windows 下是相对窗口左上角的坐标(窗口挪动自动跟随),
    # Mac 下是绝对坐标。calibrate.py 写入。
    "input_box": (400, 800),
    # calibrate 时企业微信窗口的位置 [左,上,右,下],Windows 用
    "window_rect": None,
    # 客服会话名列表:只从这些会话学习你的话术风格(私人聊天不记);
    # 看消息和生成话术对所有会话都生效,不受此限制。
    # 例: ["张三", "售后群", "充电宝咨询"]
    "service_chats": [],
    # 生成话术时附带几条你的历史真实回复做范例
    "learn_examples": 3,
    # 截图间隔(秒)
    "interval": 5,
    # ---- 大模型(OpenAI 兼容接口) ----
    "llm_base_url": "https://api.deepseek.com/v1",  # 智谱换成 https://open.bigmodel.cn/api/paas/v4
    "llm_api_key": "填你的key",
    "llm_model": "deepseek-chat",  # 智谱用 glm-4-flash
    # ---- 业务话术 ----
    "system_prompt": (
        "你是共享充电宝品牌的微信客服,语气亲切简洁,一次只回答客户当前的问题。"
        "业务规则:充电宝丢失或未归还会扣费,费用为XX元(请替换成实际金额);"
        "用户说'充电宝丢了'时,先安抚,再告知扣费规则和处理方式,不要答非所问。"
    ),
}


def _base_dir():
    if getattr(_sys, "frozen", False):
        return _os.path.dirname(_os.path.abspath(_sys.executable))
    return _os.path.dirname(_os.path.abspath(__file__))


def _load_config():
    cfg = dict(CONFIG_DEFAULTS)
    path = _os.path.join(_base_dir(), "config.json")
    if _os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg.update(_json.load(f))
        except Exception as e:
            print(f"[警告] config.json 读取失败({e})，已使用默认配置。")
    for key in ("chat_region", "title_region", "input_box"):
        try:
            cfg[key] = tuple(cfg[key])
        except Exception:
            print(f"[警告] {key} 格式不对，已使用默认值。")
            cfg[key] = tuple(CONFIG_DEFAULTS[key])
    if cfg.get("window_rect"):
        cfg["window_rect"] = tuple(cfg["window_rect"])
    cfg["service_chats"] = [str(s).strip() for s in cfg.get("service_chats", []) if str(s).strip()]
    return cfg


CONFIG = _load_config()
# ===================================================


# ---------------- 窗口定位与截图 ----------------
def find_wecom_window():
    """找企业微信窗口。返回 (hwnd, (左,上,右,下)) / "minimized" / None。Windows 才有。"""
    if not _IS_WIN:
        return None
    try:
        import win32gui
    except ImportError:
        return None
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "企业微信" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        return None
    if not found:
        return None
    hwnd = found[0]
    try:
        if win32gui.IsIconic(hwnd):
            return "minimized"
        return (hwnd, tuple(win32gui.GetWindowRect(hwnd)))
    except Exception:
        return None


def capture_window(hwnd):
    """系统级截整个窗口(被遮挡也能截到)。返回 PIL 图片,失败抛异常。"""
    import win32gui
    import win32ui
    from ctypes import windll
  
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口尺寸异常")
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    try:
        ok = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2) # PW_RENDERFULLCONTENT;注:win32gui无PrintWindow,必须走ctypes
        if not ok:
            raise RuntimeError("PrintWindow 失败")
        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               data, "raw", "BGRX", 0, 1).copy()
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
    return img


def crop_relative(img, wx, wy, ww, wh, region):
    """把绝对屏幕坐标的 region,换算到窗口截图上并裁剪。"""
    sx = img.width / ww
    sy = img.height / wh
    x0 = max(0, (region[0] - wx) * sx)
    y0 = max(0, (region[1] - wy) * sy)
    x1 = min(img.width, (region[0] + region[2] - wx) * sx)
    y1 = min(img.height, (region[1] + region[3] - wy) * sy)
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError("裁剪区域超出窗口范围,窗口可能被缩放过,请重跑 calibrate.py")
    return img.crop((x0, y0, x1, y1))


# ---------------- OCR ----------------
_ocr = None
_ocr_tmp = _os.path.join(_base_dir(), "_ocr_tmp.png")


def _init_ocr():
    """按装好的 PaddleOCR 版本初始化:3.x 优先,2.x 兜底。"""
    from paddleocr import PaddleOCR
    try:
        # PaddleOCR 3.x:show_log 已移除,use_angle_cls 改名 use_textline_orientation
        return PaddleOCR(use_textline_orientation=False, lang="ch")
    except Exception:
        # PaddleOCR 2.x
        return PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)


def _ocr_predict(ocr, img_path):
    """3.x 用 predict(),2.x 用 ocr(),统一返回可迭代的 pages。"""
    predict = getattr(ocr, "predict", None)
    if callable(predict):
        return predict(img_path)
    try:
        return ocr.ocr(img_path, cls=False)
    except TypeError:
        return ocr.ocr(img_path)


def _iter_ocr_page(page):
    """把 2.x/3.x 的单页结果统一成 (文字, 中心x) 迭代器。"""
    # 3.x:OCRResult(平行数组 rec_texts/rec_polys,或 .json['res'])
    rec_texts = getattr(page, "rec_texts", None)
    rec_polys = getattr(page, "rec_polys", None)
    if rec_texts is None:
        try:
            _d = (page.json or {}).get("res", {})
        except Exception:
            _d = {}
        rec_texts = _d.get("rec_texts") or []
        rec_polys = _d.get("rec_polys") or []
    if not isinstance(page, (list, tuple)):
        for _t, _poly in zip(list(rec_texts), list(rec_polys or [])):
            _t = str(_t).strip()
            if not _t:
                continue
            try:
                _xs = [float(_p[0]) for _p in _poly]
                _cx = sum(_xs) / len(_xs)
            except Exception:
                continue
            yield _t, _cx
        return
    # 2.x:[[box,(text,conf)],...]
    for _line in page:
        try:
            _box, (_t, _conf) = _line[0], _line[1]
            _t = str(_t).strip()
            _xs = [float(_p[0]) for _p in _box]
            _cx = sum(_xs) / len(_xs)
        except Exception:
            continue
        if _t:
            yield _t, _cx


def ocr_lines(pil_img):
    """返回 [(文字, 中心x), ...],中心x 相对传入图片。"""
    global _ocr
    if _ocr is None:
        _ocr = _init_ocr()
    pil_img.save(_ocr_tmp)
    try:
        result = _ocr_predict(_ocr, _ocr_tmp)
    except Exception:
        return []
    out = []
    for page in result or []:
        if page is None:
            continue
        out.extend(_iter_ocr_page(page))
    return out


def split_sides(lines, width):
    """按左右位置分:客户气泡在左,你的在右。返回 (客户[...], 你的[...])。"""
    customer, me = [], []
    for text, cx in lines:
        (customer if cx < width / 2 else me).append(text)
    return customer, me


# ---------------- 风格学习 ----------------
def _memory_path():
    return _os.path.join(_base_dir(), "style_memory.jsonl")


def load_examples():
    path = _memory_path()
    if not _os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(_json.loads(line))
                except Exception:
                    pass
    return out


def append_example(customer, reply):
    """记一条你的真实回复。只保留最近 300 条。"""
    if not customer.strip() or not reply.strip():
        return
    exs = load_examples()
    exs.append({"customer": customer.strip()[:200], "reply": reply.strip()[:200]})
    exs = exs[-300:]
    with open(_memory_path(), "w", encoding="utf-8") as f:
        for e in exs:
            f.write(_json.dumps(e, ensure_ascii=False) + "\n")


def build_messages(customer_text):
    msgs = [{"role": "system", "content": CONFIG["system_prompt"]}]
    k = int(CONFIG.get("learn_examples", 3) or 0)
    if k > 0:
        shown = [e for e in load_examples()[-k:] if e.get("customer") and e.get("reply")]
        if shown:
            msgs[0]["content"] += ("\n\n下面是你(店主)过去的真实回复,"
                                   "请模仿其语气、用词和简洁程度,不要改变业务口径:")
            for e in shown:
                msgs.append({"role": "user", "content": e["customer"]})
                msgs.append({"role": "assistant", "content": e["reply"]})
    msgs.append({"role": "user", "content": customer_text})
    return msgs


def get_ai_reply(messages):
    import requests
    url = CONFIG["llm_base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {CONFIG['llm_api_key']}",
        "Content-Type": "application/json",
    }
    payload = {"model": CONFIG["llm_model"], "messages": messages, "temperature": 0.7}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def log_send(customer_msg, final_text, action):
    path = _os.path.join(_base_dir(), "replies.log")
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "customer": customer_msg,
        "reply": final_text,
        "action": action,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(record, ensure_ascii=False) + "\n")


def title_matches(title):
    title = (title or "").strip()
    if not title:
        return False
    return any(s in title or title in s for s in CONFIG["service_chats"])


# ---------------- 控制窗口 ----------------
import tkinter as tk
from tkinter import messagebox


class Controller:
    QUEUE_CAP = 20

    def __init__(self):
        self.paused = False
        self.busy = False
        self.queue = []
        self.last_customer = []
        self.last_me = []
        self.last_trigger = 0

        self.root = tk.Tk()
        self.root.title("客服 RPA 控制")
        self.root.geometry("300x190")
        self.root.attributes("-topmost", True)

        self.status_var = tk.StringVar(value="状态: 运行中")
        tk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=12, pady=(12, 4))
        self.queue_var = tk.StringVar(value="待处理: 0 条")
        tk.Label(self.root, textvariable=self.queue_var, anchor="w").pack(fill="x", padx=12, pady=2)
        self.learn_var = tk.StringVar(value="已学习你的回复: 0 条")
        tk.Label(self.root, textvariable=self.learn_var, anchor="w", fg="gray").pack(fill="x", padx=12, pady=2)

        btns = tk.Frame(self.root)
        btns.pack(pady=8)
        self.pause_btn = tk.Button(btns, text="暂停", width=10, command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=6)
        tk.Button(btns, text="退出", width=10, command=self.root.destroy).pack(side="left", padx=6)

        tk.Label(self.root, text="急停:鼠标甩到屏幕左上角", fg="gray").pack(pady=2)
        self.refresh_learn_count()

    def refresh_learn_count(self):
        self.learn_var.set(f"已学习你的回复: {len(load_examples())} 条")

    def set_status(self, s):
        self.status_var.set("状态: " + s)
        self.queue_var.set(f"待处理: {len(self.queue)} 条")

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.config(text="恢复" if self.paused else "暂停")
        self.set_status("已暂停(你自己回)" if self.paused else "运行中")

    def confirm_dialog(self, customer_msg, draft):
        decision = {"send": False, "text": ""}
        dlg = tk.Toplevel(self.root)
        dlg.title("客服 AI 话术确认")
        dlg.geometry("520x440")
        dlg.attributes("-topmost", True)

        tk.Label(dlg, text="客户消息:", anchor="w").pack(fill="x", padx=10, pady=(10, 0))
        t1 = tk.Text(dlg, height=6, wrap="word")
        t1.pack(fill="x", padx=10)
        t1.insert("1.0", customer_msg)
        t1.config(state="disabled")

        tk.Label(dlg, text="AI 话术(可直接改):", anchor="w").pack(fill="x", padx=10, pady=(10, 0))
        t2 = tk.Text(dlg, height=8, wrap="word")
        t2.pack(fill="x", padx=10)
        t2.insert("1.0", draft)

        def on_send():
            decision["send"] = True
            decision["text"] = t2.get("1.0", "end").strip()
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(pady=12)
        tk.Button(btns, text="发送给客户", width=14, bg="#07c160", fg="white",
                  command=on_send).pack(side="left", padx=10)
        tk.Button(btns, text="忽略", width=14, command=dlg.destroy).pack(side="left", padx=10)

        dlg.grab_set()
        self.root.wait_window(dlg)
        return decision["send"], decision["text"]

    def auto_send(self, text, wx, wy):
        import platform
        import pyperclip

        pyperclip.copy(text)
        ib = CONFIG["input_box"]
        if _IS_WIN and CONFIG.get("window_rect") and wx is not None:
            x, y = wx + ib[0], wy + ib[1]
        else:
            x, y = ib
        pyautogui.click(x, y)
        time.sleep(0.4)
        mod = "command" if platform.system() == "Darwin" else "ctrl"
        pyautogui.hotkey(mod, "v")
        time.sleep(0.3)
        pyautogui.press("enter")

    def handle_one(self, customer_msg, wx, wy):
        self.busy = True
        self.set_status("正在生成话术…")
        try:
            draft = get_ai_reply(build_messages(customer_msg))
        except Exception as e:
            messagebox.showerror("AI 调用失败", f"{e}\n\n请检查 config.json 里的 key/base_url/model。")
            self.set_status("运行中")
            self.busy = False
            return
        send, final = self.confirm_dialog(customer_msg, draft)
        if send and final:
            self.auto_send(final, wx, wy)
            log_send(customer_msg, final, "sent")
            self.set_status("已发送")
        else:
            log_send(customer_msg, draft, "ignored")
            self.set_status("已忽略")
        self.refresh_learn_count()
        self.busy = False

    def poll(self):
        try:
            if not self.paused and not self.busy:
                wx = wy = None
                if _IS_WIN:
                    w = find_wecom_window()
                    if w is None:
                        self.set_status("未找到企业微信窗口")
                        return
                    if w == "minimized":
                        self.set_status("窗口已最小化,检测暂停")
                        return
                    hwnd, (wx, wy, wr, wb) = w
                    try:
                        img = capture_window(hwnd)
                    except Exception as e:
                        self.set_status(f"截图失败: {e}")
                        return
                    title_img = crop_relative(img, wx, wy, wr - wx, wb - wy, CONFIG["title_region"])
                    chat_img = crop_relative(img, wx, wy, wr - wx, wb - wy, CONFIG["chat_region"])
                else:
                    # Mac:退化为固定区域截图
                    title_img = pyautogui.screenshot(region=CONFIG["title_region"])
                    chat_img = pyautogui.screenshot(region=CONFIG["chat_region"])

                title = "".join(t for t, _ in ocr_lines(title_img)).strip()
                # 门禁只管"学":只有客服会话才学习你的话术风格;
                # 看消息和生成话术对所有会话都生效。
                is_service = bool(CONFIG["service_chats"]) and title_matches(title)

                lines = ocr_lines(chat_img)
                customer, me = split_sides(lines, chat_img.width)

                # 学习:你新回的话记下来(只在客服会话里,私人聊天不记)
                new_m = [l for l in me if l not in self.last_me]
                new_c = [l for l in customer if l not in self.last_customer]
                if is_service and new_m:
                    ctx = new_c[-1] if new_c else (self.last_customer[-1] if self.last_customer else "")
                    append_example(ctx, "\n".join(new_m))
                    self.refresh_learn_count()

                now = time.time()
                if new_c and now - self.last_trigger > 20:
                    self.last_trigger = now
                    if len(self.queue) >= self.QUEUE_CAP:
                        self.queue.pop(0)
                    self.queue.append("\n".join(new_c))

                self.last_customer, self.last_me = customer, me

                if self.queue:
                    self.handle_one(self.queue.pop(0), wx, wy)
                else:
                    tag = "·学" if is_service else ""
                    self.set_status(f"运行中[{title or '未知会话'}]{tag}")
        except Exception:
            traceback.print_exc()
        finally:
            self.root.after(CONFIG["interval"] * 1000, self.poll)


def main():
    if CONFIG["llm_api_key"].strip() in ("", "填你的key"):
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("还没填 key", "config.json 里 llm_api_key 还是空的。\n先填上你的 key 再运行。")
        r.destroy()
        return

    pyautogui.FAILSAFE = True
    print("RPA 启动。要求:企业微信登录好、电脑不锁屏(最小化会自动暂停)。")
    print("控制窗口可随时 暂停/恢复/退出;急停:鼠标甩到屏幕左上角。")

    app = Controller()
    app.root.after(1000, app.poll)
    app.root.mainloop()


if __name__ == "__main__":
    main()
