# -*- coding: utf-8 -*-
"""RPA 核心纯逻辑:不依赖 tkinter / pyautogui / paddle,可以在任何平台单测。"""
import json
import os
import time
from collections import Counter


def title_matches(title, service_chats):
    """标题是否命中客服会话名单。"""
    if not title or not service_chats:
        return False
    return any(s and s in title for s in service_chats)


def diff_lines(old_lines, new_lines):
    """多重集差分:客户连发两条相同文字,第二条也能被抓到。"""
    c = Counter(old_lines or [])
    out = []
    for l in new_lines or []:
        if c.get(l, 0) > 0:
            c[l] -= 1
        else:
            out.append(l)
    return out


def region_abs(rel, wx, wy):
    """窗口相对坐标 -> 屏幕绝对坐标(窗口左上角为 wx, wy)。"""
    return [wx + rel[0], wy + rel[1], wx + rel[2], wy + rel[3]]


def resolve_region(rel, abs_, wx, wy):
    """优先用相对坐标;旧版绝对坐标兜底(窗口移动后会偏,建议重跑 calibrate)。"""
    if rel:
        return region_abs(rel, wx, wy)
    return abs_


def pick_best(cands):
    """cands: [(标题, (x0,y0,x1,y1))...] -> 面积最大的企业微信窗口下标,排除客服助手自己。

    找不到返回 None。
    """
    best_i, best_area = None, 0
    for i, (title, r) in enumerate(cands or []):
        t = title or ""
        if "企业微信" not in t or "客服助手" in t:
            continue
        area = max(0, r[2] - r[0]) * max(0, r[3] - r[1])
        if area > 10000 and area > best_area:
            best_i, best_area = i, area
    return best_i


def append_example(data_dir, ctx, me, cap=300):
    """记一条你的话术风格(只应从客服会话调用)。"""
    p = os.path.join(data_dir, "style_memory.jsonl")
    rec = {"t": int(time.time()), "q": ctx or "", "a": me or ""}
    lines = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    lines.append(json.dumps(rec, ensure_ascii=False))
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-cap:]) + "\n")


def load_examples(data_dir, n=3):
    """取最近 n 条风格范例,生成话术时附上。"""
    p = os.path.join(data_dir, "style_memory.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    out = []
    for l in lines[-n:]:
        try:
            d = json.loads(l)
            if d.get("a"):
                out.append(d)
        except Exception:
            pass
    return out


def log_reply(data_dir, title, customer, draft, action):
    """发送/忽略/跳过都记一笔,方便回查。"""
    p = os.path.join(data_dir, "replies.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]<{action}>[{title}]\n"
            f"客:{customer}\n稿:{draft}\n---\n"
        )
