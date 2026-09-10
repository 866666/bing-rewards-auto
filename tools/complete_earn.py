# -*- coding: utf-8 -*-
"""complete_earn.py - 自动完成 rewards.bing.com/earn 页面动态出现的积分任务
========================================================
覆盖任务类型（来自 getuserinfo 的 dailySetPromotions / morePromotions / punchCards）：
  urlreward / floatreward : 导航 destination 或 dashboard 实点击即计分（可自动）
  search                  : destination 为 bing 搜索特定词，导航即完成（可自动）
  quiz                    : 导航答题页，循环点击选项直到完成（best-effort）
  punchcard               : 完成子任务自动累计，本身无需点击（跳过，靠已做任务自然达成）
  appinstall / mobile 等  : 需安装 App / 移动端，无法脚本完成（报告跳过）

用法:
  python tools/complete_earn.py             # 自动检测并完成当前出现的任务
  python tools/complete_earn.py --dry       # 只列出当前所有任务不执行
防并发：检测到主脚本(rewards_daily.py)正在运行时自动退出，避免抢 Edge。
"""
import sys, json, time, os, urllib.request
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complete_dailyset import (ensure_edge, get_ws, cdp_nav, cdp_js, cdp_send,
                               list_tabs, find_task_anchor, wait_for_new_tab,
                               close_tab, CDP_HTTP, read_state, close_tab)

STAY_PER_DEST = 20   # destination 直访停留秒数（tracking 生效）
STAY_PER_TAB = 18    # 新 tab 实点击停留秒数
MAX_PER_QUIZ = 6     # quiz 最多答题次数


def main_script_running():
    """主脚本在跑则不并发（同操作一个 Edge/9224）。
    读项目根目录的 .rewards_running.lock（含 PID）：该 PID 进程存活则视为运行中。
    比"pythonw 数量>1"可靠——本机常驻多个无关 pythonw(opencode_proxy) 会误判。"""
    try:
        lock = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".rewards_running.lock")
        if not os.path.exists(lock):
            return False
        pid = open(lock, encoding="utf-8").read().strip()
        if not pid.isdigit():
            return False
        # Windows: tasklist 查 PID 是否存活
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                             capture_output=True, text=True, encoding="gbk", errors="ignore",
                             timeout=10).stdout
        return f'"{pid}"' in out
    except Exception:
        return False


def fetch_earn_tasks(ws):
    """拉取全部任务分类（dailySet / more / punch），字段缺失容错"""
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
      xhr.send();
      if (xhr.status !== 200) return 'ERR:' + xhr.status;
      const j = JSON.parse(xhr.responseText);
      const d = j.dashboard || {};
      const norm = (t, src) => {
        const a = t.attributes || {};
        return {
          title: (t.title || t.name || 'untitled') + '',
          complete: !!t.complete,
          type: (a.type || t.type) + '',
          dest: (a.destination || t.destination) + '',
          src: src,
          is_unlocked: (typeof t.is_unlocked === 'undefined' ? true : t.is_unlocked),
          locked_category_criteria: (t.locked_category_criteria || a.locked_category_criteria || '') + '',
        };
      };
      const out = {points: (j.dashboard.userStatus||{}).availablePoints, tasks: []};
      const dsp = (d.dailySetPromotions || {})[`""" + today + """`] || [];
      (dsp || []).forEach(t => out.tasks.push(norm(t, 'dailySet')));
      (d.morePromotions || []).forEach(t => out.tasks.push(norm(t, 'more')));
      (d.punchCards || []).forEach(t => out.tasks.push(norm(t, 'punch')));
      return JSON.stringify(out);
    })()"""
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    if not v or str(v).startswith("ERR"):
        print("  [x] api:", v)
        return None
    try:
        return json.loads(v)
    except Exception as e:
        print("  [x] parse:", e)
        return None


def do_task(ws, t, dry=False):
    """按类型执行单个任务，返回完成与否（best-effort）"""
    title, typ, dest = t["title"], t["type"], t["dest"]
    if dry:
        print(f"    [dry] {typ:12s} {title[:50]}")
        return False
    print(f"    → {typ:12s} {title[:50]}")

    # 不可自动类型/需其他资源直接跳过
    if typ in ("appinstall", "mobile", "app", "punchcard") or "install" in typ:
        print("      ⏭ 需安装/移动端，跳过")
        return False
    if "referandearn" in dest:
        print("      ⏭ 邀请推荐类（referandearn），需朋友参与，跳过")
        return False
    if t.get("is_unlocked") is False or (t.get("locked_category_criteria") or "").strip():
        print(f"      ⏭ 需 Rewards App（锁），跳过")
        return False

    before = {x["id"] for x in list_tabs()}

    # 1) destination 直访
    if dest.startswith("http"):
        print(f"      直访 destination (Stay {STAY_PER_DEST}s)")
        try:
            cdp_nav(ws, dest, 8)
        except Exception as e:
            print("      nav err:", e)
        time.sleep(STAY_PER_DEST)

    # 2) quiz 特殊处理：在目标页循环点选项
    if typ == "quiz" or "quiz" in dest.lower():
        print("      quiz: 尝试 3 轮选项点击")
        cdp_nav(ws, dest if dest.startswith("http") else "https://rewards.bing.com", 8)
        time.sleep(8)
        for _ in range(MAX_PER_QUIZ):
            clicked = cdp_js(ws, """(() => {
              const btns = [...document.querySelectorAll('[role=radio], li.answer, .qOption, button[id^=rq]')];
              if (!btns.length) return null;
              btns[0].click(); return 'c';
            })()""", timeout=10)
            if not clicked:
                break
            time.sleep(12)

    # 3) 回 dashboard 找卡片实点击（对应 earn/more 卡片）
    cdp_nav(ws, "https://rewards.bing.com/earn", 12)
    time.sleep(4)
    r = find_task_anchor(ws, title, dest if dest.startswith("http") else "", timeout=18)
    print(f"      dashboard card: {r}")
    if str(r) == "CLICKED":
        nws, ntab = wait_for_new_tab(before, max_wait=10)
        if nws and ntab:
            time.sleep(STAY_PER_TAB)
            nws.close()
            close_tab(ntab)

    # 4) 回 dashboard 刷新状态（earn 页回 dashboard 会让 state 可见）
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 10)
    time.sleep(6)
    return None  # 结果由 verify 阶段统一判定


def verify(ws, want):
    """重新拉 API，判定每个任务是否完成（按 title 匹配）"""
    data = fetch_earn_tasks(ws)
    if not data:
        return []
    res = []
    for t in want:
        hit = next((x for x in data["tasks"] if x["title"] == t["title"]), None)
        done = bool(hit and hit["complete"])
        res.append((t["title"], t["type"], done))
    return res


def main():
    dry = "--dry" in sys.argv
    if not dry and main_script_running():
        print("[x] 主脚本(rewards_daily.py)正在运行，为避免抢 Edge 稍后再执行（或等其结束后再跑）")
        return 1
    if not ensure_edge():
        print("[x] Edge CDP 不可用")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/earn", 12)

    data = fetch_earn_tasks(ws)
    if not data:
        print("[x] 无法获取任务列表")
        return 1
    print(f"当前积分: {data['points']}")
    todo = [t for t in data["tasks"] if not t["complete"]]
    print(f"任务总数: {len(data['tasks'])} | 未完成: {len(todo)}")
    for t in data["tasks"]:
        print(f"  {'✅' if t['complete'] else '⬜'} [{t['src']:8s} {t['type']:12s}] {t['title'][:48]}")
    if not todo:
        print("\n全部已完成 🎉")
        ws.close()
        return 0
    if dry:
        ws.close()
        return 0

    print(f"\n== 开始执行 {len(todo)} 个未完成任务 ==")
    for t in todo:
        do_task(ws, t)

    print("\n== 验证结果 ==")
    res = verify(ws, todo)
    ok = sum(1 for _, _, d in res if d)
    for title, typ, done in res:
        print(f"  {'✅' if done else '❌'} [{typ:12s}] {title[:50]}")
    print(f"\n完成 {ok}/{len(res)}")
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())