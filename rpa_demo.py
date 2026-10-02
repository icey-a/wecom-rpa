# -*- coding: utf-8 -*-
"""企业微信客服 RPA v3:看客户消息 -> AI 生成话术 -> 你确认/改字 -> 点一下发送。

用法:
  1. python calibrate.py   量好聊天区/标题区/输入框(存为窗口相对坐标)
  2. 填 config.json 里的 zhipu_key(在自己电脑上填,别发给别人)
  3. python rpa_demo.py    开始工作;控制窗可暂停/恢复/退出

v3 改动:
- 监听在后台线程跑,你确认上一条时新消息照样被抓到,排队等你处理,不丢。
- 聊天区/标题区都存成窗口相对坐标,窗口随便挪,不用重跑校准。
- 找窗口时选面积最大的企业微信窗口(排除登录小窗和客服助手自己)。
- 发送用剪贴板粘贴,中文话术不断行不乱码。
- 学习门禁:只从 service_chats 的客服会话学你的话术风格,私人聊天不记;
  看消息和生成话术对所有会话都生效。
"""
import json
import os
import sys
import time
import threading
import queue as queue_mod
import tkinter as tk
from tkinter import scrolledtext, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpa_core import (title_matches, diff_lines, resolve_region, pick_best,
                      append_example, load_examples, log_reply)

IS_WINDOWS = os.name == "nt"

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except Exception:
    pyautogui = None

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None

try:
    import requests
except Exception:
    requests = None

BASE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):  # 打包成 exe 后,配置和数据放在 exe 旁边
    BASE = os.path.dirname(sys.executable)
CONFIG_PATH = os.path.join(BASE, "config.json")
DATA_DIR = os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_DEFAULTS = {
    # 相对坐标(推荐):[窗口内左,上,右,下],窗口移动不影响。由 calibrate.py 写入。
    "chat_region_rel": None,
    "title_region_rel": None,
    # 旧版绝对坐标:没有相对坐标时兜底用,窗口移动后会偏,建议重跑 calibrate。
    "chat_region": None,
    "title_region": None,
    # 输入框:窗口相对坐标 [x, y],由 calibrate.py 写入。
    "input_rel": None,
    # 客服会话名列表:只从这些会话学习你的话术风格(私人聊天不记);
    # 看消息和生成话术对所有会话都生效,不受此限制。
    # 例: ["张三", "售后群", "充电宝咨询"]
    "service_chats": [],
    "poll_interval": 3,
    "zhipu_api": "https://open.bigmodel.cn/api/paas/v4",
    "zhipu_model": "glm-4-flash",
    # 在自己电脑上填,别截图发出来,也别上传网盘。
    "zhipu_key": "",
    "business": (
        "你是这家店的企业微信客服,负责接待咨询充电宝租赁的客户。"
        "说话口语、简短、热情,一次只说一两句,别列条目。"
    ),
}


def load_config():
    cfg = dict(CONFIG_DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


CONFIG = load_config()


# ---------- Windows 窗口 ----------

def find_wecom_window():
    """返回企业微信主窗口 hwnd:面积最大的那个,排除登录小窗和客服助手自己。"""
    if not IS_WINDOWS:
        return None
    import win32gui
    cands = []  # (标题, rect, hwnd)
    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                cands.append((win32gui.GetWindowTitle(hwnd) or "",
                              win32gui.GetWindowRect(hwnd), hwnd))
        except Exception:
            pass
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        return None
    i = pick_best([(t, r) for t, r, _ in cands])
    return cands[i][2] if i is not None else None


def is_minimized(hwnd):
    try:
        import win32gui
        return win32gui.IsIconic(hwnd)
    except Exception:
        return False


def get_window_rect(hwnd):
    import win32gui
    return win32gui.GetWindowRect(hwnd)  # x0, y0, x1, y1


def capture_window(hwnd):
    """截整个企业微信窗口(被遮挡也能截到);返回 PIL 图,原点在窗口左上角。"""
    if not IS_WINDOWS:
        return None
    x0, y0, x1, y1 = get_window_rect(hwnd)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    try:
        import win32gui
        import win32ui
        from ctypes import windll
        from PIL import Image
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img
    except Exception:
        pass
    try:
        if ImageGrab is not None:
            return ImageGrab.grab(bbox=(x0, y0, x1, y1))
    except Exception:
        pass
    return None


# ---------- OCR ----------

def _init_ocr():
    """按装好的 PaddleOCR 版本初始化:3.x 优先,2.x 兜底。"""
    from paddleocr import PaddleOCR
    try:
        # PaddleOCR 3.x:show_log 已移除,use_angle_cls 改名
        return PaddleOCR(use_textline_orientation=False, lang="ch")
    except Exception:
        # PaddleOCR 2.x
        return PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)


def _ocr_predict(ocr, img):
    """3.x 用 predict(),2.x 用 ocr(),统一返回可迭代的 pages。"""
    predict = getattr(ocr, "predict", None)
    if callable(predict):
        return predict(img)
    try:
        return ocr.ocr(img, cls=False)
    except TypeError:
        return ocr.ocr(img)


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


def ocr_lines(img, ocr):
    if ocr is None or img is None:
        return []
    try:
        res = _ocr_predict(ocr, img)
    except Exception:
        return []
    out = []
    for page in res or []:
        if page is None:
            continue
        out.extend(_iter_ocr_page(page))
    return out


def split_sides(lines, width):
    """按文字中心点左右分边:左=客户,右=你自己。"""
    customer, me = [], []
    for txt, cx in lines:
        (me if cx > width / 2 else customer).append(txt)
    return customer, me


# ---------- 话术生成 ----------

def build_messages(customer_text, ctx):
    examples = load_examples(DATA_DIR, 3)
    demo = ""
    if examples:
        demo = "下面是这位客服平时自己回复的例子,学她的语气和说法:\n"
        for e in examples:
            demo += f"客:{e['q']}\n客服:{e['a']}\n"
    return [
        {"role": "system", "content": CONFIG["business"] + "\n" + demo},
        {"role": "user",
         "content": f"最近的对话:\n{ctx}\n客户刚说:{customer_text}\n只回一句客服话术,别解释。"},
    ]


def gen_reply(customer_text, ctx):
    key = CONFIG.get("zhipu_key", "").strip()
    if not key or requests is None:
        return None
    try:
        r = requests.post(
            CONFIG["zhipu_api"] + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": CONFIG["zhipu_model"],
                  "messages": build_messages(customer_text, ctx),
                  "max_tokens": 200, "temperature": 0.7},
            timeout=25,
        )
        d = r.json()
        return d["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


# ---------- 确认窗 ----------

class ConfirmWin:
    def __init__(self, master, title, customer_text, draft):
        self.result = None
        self.top = tk.Toplevel(master)
        self.top.title(f"客户[{title}]来消息了")
        self.top.geometry("460x380")
        self.top.attributes("-topmost", True)
        tk.Label(self.top, text="客户说:", anchor="w").pack(fill="x", padx=8)
        tk.Label(self.top, text=customer_text, wraplength=430, justify="left",
                 bg="#f2f2f2").pack(fill="x", padx=8, pady=4)
        tk.Label(self.top, text="AI 话术(可直接改):", anchor="w").pack(fill="x", padx=8)
        self.text = scrolledtext.ScrolledText(self.top, height=8)
        self.text.pack(fill="both", expand=True, padx=8, pady=4)
        self.text.insert("1.0", draft)
        bar = tk.Frame(self.top)
        bar.pack(pady=6)
        tk.Button(bar, text="发送给客户", width=12,
                  command=self._send).pack(side="left", padx=6)
        tk.Button(bar, text="忽略", width=12,
                  command=self._ignore).pack(side="left", padx=6)
        self.text.focus_set()

    def _send(self):
        self.result = "send"
        self.top.destroy()

    def _ignore(self):
        self.result = "ignore"
        self.top.destroy()


# ---------- 主控 ----------

class ControlUI:
    def __init__(self):
        self.running = True
        self.paused = False
        self.busy = False          # 确认窗开着时为 True,防止重入;监听不停
        self.ocr = None
        self.msg_queue = queue_mod.Queue()
        self._pending_status = "启动中…"
        self.last_customer = []
        self.last_me = []
        self._migrated = False

        self.root = tk.Tk()
        self.root.title("客服助手")
        self.root.geometry("300x210")
        self.root.attributes("-topmost", True)
        self.status_var = tk.StringVar(value="启动中…")
        tk.Label(self.root, textvariable=self.status_var, wraplength=280,
                 justify="left").pack(pady=8)
        self.learn_var = tk.StringVar(value="已学习 0 条")
        tk.Label(self.root, textvariable=self.learn_var).pack()
        row = tk.Frame(self.root)
        row.pack(pady=10)
        self.pause_btn = tk.Button(row, text="暂停", width=10,
                                   command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=5)
        tk.Button(row, text="退出", width=10,
                  command=self.stop).pack(side="left", padx=5)
        self.root.protocol("WM_DELETE_WINDOW", self.stop)

        self.worker = threading.Thread(target=self._watch, daemon=True)
        self.worker.start()
        self.root.after(1500, self.tick)

    # ----- 后台监听线程:只做截图/OCR/差分/排队,不碰 tkinter -----
    def _watch(self):
        if PaddleOCR is None:
            self._pending_status = "缺 paddleocr,看不了字"
            return
        try:
            self.ocr = _init_ocr()
        except Exception as e:
            self._pending_status = f"OCR 初始化失败:{str(e)[:60]}"
            return
        while self.running:
            try:
                if not self.paused:
                    self._scan()
            except Exception as e:
                self._pending_status = f"监听出错:{str(e)[:60]}"
            time.sleep(CONFIG["poll_interval"])

    def _scan(self):
        hwnd = find_wecom_window()
        if not hwnd:
            self._pending_status = "没找到企业微信窗口"
            return
        if is_minimized(hwnd):
            self._pending_status = "窗口最小化了,点开再继续"
            return
        wx, wy, _, _ = get_window_rect(hwnd)

        chat_rel = resolve_region(CONFIG.get("chat_region_rel"),
                                  CONFIG.get("chat_region"), wx, wy)
        title_rel = resolve_region(CONFIG.get("title_region_rel"),
                                   CONFIG.get("title_region"), wx, wy)
        if not chat_rel or not title_rel:
            self._pending_status = "没量聊天区/标题区,先跑 calibrate.py"
            return
        if CONFIG.get("chat_region_rel") is None and CONFIG.get("chat_region"):
            self._migrated = True  # 旧版绝对坐标,按当前窗口换算着用

        img = capture_window(hwnd)
        if img is None:
            self._pending_status = "截图失败"
            return
        title_img = img.crop(tuple(map(int, title_rel)))
        chat_img = img.crop(tuple(map(int, chat_rel)))

        title = "".join(t for t, _ in ocr_lines(title_img, self.ocr)).strip()
        # 学习门禁:只有客服会话才学你的话术;看和回对所有会话生效
        is_service = bool(CONFIG["service_chats"]) and title_matches(
            title, CONFIG["service_chats"])

        customer, me = split_sides(ocr_lines(chat_img, self.ocr), chat_img.width)
        prev_c = self.last_customer
        new_c = diff_lines(self.last_customer, customer)
        new_m = diff_lines(self.last_me, me)
        self.last_customer, self.last_me = customer, me

        if is_service and new_m:
            ctx = new_c[-1] if new_c else (prev_c[-1] if prev_c else "")
            append_example(DATA_DIR, ctx, "\n".join(new_m))

        ctx_hist = "\n".join(prev_c[-4:])
        for m in new_c:
            self.msg_queue.put({"text": m, "ctx": ctx_hist,
                                "is_service": is_service,
                                "title": title or "未知会话"})
        nq = self.msg_queue.qsize()
        tag = "·学" if is_service else ""
        mig = "·旧坐标已换算" if self._migrated else ""
        self._pending_status = (
            f"看着[{title or '未知会话'}]{tag}{mig}"
            + (f"·{nq}条排队" if nq else ""))

    # ----- 主线程:状态刷新 + 逐条处理队列 -----
    def tick(self):
        if not self.running:
            return
        if self._pending_status:
            self.set_status(self._pending_status)
            self._pending_status = None
        self.refresh_learn_count()
        if not self.paused and not self.busy:
            try:
                item = self.msg_queue.get_nowait()
            except queue_mod.Empty:
                item = None
            if item:
                self.handle_one(item)
        self.root.after(1500, self.tick)

    def handle_one(self, item):
        text = item["text"]
        self.busy = True
        try:
            self.set_status("想话术中…")
            draft = gen_reply(text, item["ctx"])
            if draft is None:
                log_reply(DATA_DIR, item["title"], text,
                          "(key 没填或网络不通,没生成)", "跳过")
                self.set_status("key 没填或网络不通,跳过这条")
                return
            win = ConfirmWin(self.root, item["title"], text, draft)
            self.root.wait_window(win.top)  # 你确认期间,后台照样抓新消息排队
            final = win.text.get("1.0", tk.END).strip()
            if win.result == "send" and final:
                ok = self.send_text(final)
                log_reply(DATA_DIR, item["title"], text, final,
                          "发送" if ok else "发送失败")
                self.set_status("已发送" if ok else "发送失败,看看输入框位置")
            else:
                log_reply(DATA_DIR, item["title"], text, draft, "忽略")
                self.set_status("已忽略")
        finally:
            self.busy = False

    def send_text(self, text):
        """点输入框 -> 剪贴板粘贴(中文不乱码) -> 回车。发送时重找窗口,挪过也照发。"""
        if not IS_WINDOWS or pyautogui is None:
            return False
        hwnd = find_wecom_window()
        if not hwnd or not CONFIG.get("input_rel"):
            return False
        try:
            wx, wy, _, _ = get_window_rect(hwnd)
            ix, iy = CONFIG["input_rel"]
            pyautogui.click(wx + ix, wy + iy)
            time.sleep(0.4)
            try:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            except Exception:
                pyautogui.typewrite(text, interval=0.02)  # 兜底:中文可能打不出
            time.sleep(0.3)
            pyautogui.press("enter")
            return True
        except Exception:
            return False

    def set_status(self, s):
        self.status_var.set(s)

    def refresh_learn_count(self):
        self.learn_var.set(f"已学习 {len(load_examples(DATA_DIR, 100000))} 条")

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.config(text="恢复" if self.paused else "暂停")
        self.set_status("已暂停" if self.paused else "运行中")

    def stop(self):
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


def main():
    key = CONFIG.get("zhipu_key", "").strip()
    if IS_WINDOWS and not key:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "先填 key",
            "config.json 里的 zhipu_key 还是空的。\n"
            "填上你自己的 key 再跑(在自己电脑上填,别发给别人)。\n"
            "可以先点确定看看界面,但不会真的生成话术。")
        root.destroy()
    ui = ControlUI()
    ui.run()


if __name__ == "__main__":
    main()
