# -*- coding: utf-8 -*-
"""rpa_core 纯逻辑单测:不需要 Windows / 企业微信 / 显卡,直接跑。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpa_core import (  # noqa: E402
    title_matches, diff_lines, region_abs, resolve_region,
    pick_best, append_example, load_examples, log_reply,
)

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


print("== title_matches ==")
check("命中", title_matches("张三-充电宝咨询", ["张三"]))
check("未命中", not title_matches("我妈", ["张三"]))
check("空标题不命中", not title_matches("", ["张三"]))
check("名单为空不命中", not title_matches("张三", []))
check("None 名单不命中", not title_matches("张三", None))

print("== diff_lines ==")
check("新消息被抓到", diff_lines(["a"], ["a", "b"]) == ["b"])
check("无新消息", diff_lines(["a"], ["a"]) == [])
check("相同文字连发两条,第二条也抓到", diff_lines(["在吗"], ["在吗", "在吗"]) == ["在吗"])
check("旧两条新一条不重复报", diff_lines(["a", "a"], ["a"]) == [])
check("空旧", diff_lines([], ["hi"]) == ["hi"])
check("空新", diff_lines(["a"], []) == [])

print("== region_abs / resolve_region ==")
check("相对转绝对", region_abs([10, 20, 30, 40], 100, 200) == [110, 220, 130, 240])
check("窗口移动后跟着走",
      region_abs([10, 20, 30, 40], 500, 600) == [510, 620, 530, 640])
check("优先用相对坐标",
      resolve_region([1, 2, 3, 4], [9, 9, 9, 9], 100, 100) == [101, 102, 103, 104])
check("无相对坐标时兜底绝对坐标",
      resolve_region(None, [9, 9, 9, 9], 100, 100) == [9, 9, 9, 9])

print("== pick_best ==")
cands = [
    ("企业微信登录", (0, 0, 300, 200)),          # 小登录窗,应被更大的主窗口压过
    ("客服助手", (0, 0, 2000, 1200)),            # 自己的控制窗,必须排除
    ("企业微信", (0, 0, 1200, 800)),             # 主窗口,面积最大
    ("微信", (0, 0, 1500, 900)),                 # 标题不对,排除
]
check("选面积最大的企业微信主窗口", pick_best(cands) == 2)
check("只有控制窗时返回 None", pick_best([("客服助手", (0, 0, 2000, 1200))]) is None)
check("空列表返回 None", pick_best([]) is None)
check("太小的窗忽略", pick_best([("企业微信", (0, 0, 10, 10))]) is None)

print("== append_example / load_examples ==")
with tempfile.TemporaryDirectory() as d:
    for i in range(305):
        append_example(d, f"q{i}", f"a{i}")
    with open(os.path.join(d, "style_memory.jsonl"), encoding="utf-8") as f:
        n = len([l for l in f.read().splitlines() if l.strip()])
    check("只保留最近 300 条", n == 300)
    ex = load_examples(d, 3)
    check("取最近 3 条", len(ex) == 3 and ex[-1]["a"] == "a304")
    check("空目录返回空", load_examples(tempfile.mkdtemp(), 3) == [])
    # 坏行不炸
    with open(os.path.join(d, "style_memory.jsonl"), "a", encoding="utf-8") as f:
        f.write("not json\n")
    check("坏行被跳过", len(load_examples(d, 500)) == 300)

print("== log_reply ==")
with tempfile.TemporaryDirectory() as d:
    log_reply(d, "张三", "在吗", "在的", "发送")
    log_reply(d, "我妈", "吃饭没", "吃了", "忽略")
    txt = open(os.path.join(d, "replies.log"), encoding="utf-8").read()
    check("两条都记下", "<发送>" in txt and "<忽略>" in txt and "张三" in txt)

print(f"\n{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
