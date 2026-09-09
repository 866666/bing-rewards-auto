# -*- coding: utf-8 -*-
"""retry_earn.py - 针对特定未完成任务做"真实点击+保留tab+轮询计分"补跑
区别于 complete_earn.py：点击后保留新 tab 并轮询 API（最长 ~90s/任务），
不提前关闭 tab，避免 tracking 未做完就关导致不计分（同 daily_set 修复思路）。
用法: python tools/retry_earn.py [标题关键字1] [标题关键字2] ...
      python tools/retry_earn.py                     # 全部未完成的"非 rnoreward、非 referral、非 install/mobile"任务
"""
import sys, json, time, os, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complete_dailyset import (ensure_edge, get_ws, cdp_nav, cdp_js,
                               list_tabs, find_task_anchor, wait_for_new_tab,
                               close_tab, read_state)

TARGET_KEYWORDS = sys.argv[1:] if len(sys.argv) > 1 else None


def main_script_running():
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FO", "CSV", "/FI", "IMAGENAME eq pythonw.exe"],
                             capture_output=True, text=True, encoding="gbk", errors="ignore",
                             timeout=10).stdout
        return out.lower().count("pythonw.exe") > 1
    except Exception:
        return False


def fetch_tasks(ws):
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
      xhr.send();
      const j = JSON.parse(xhr.responseText);
      const d = j.dashboard || {};
      const norm = (t, src) => {
        const a = t.attributes || {};
        return {title:(t.title||t.name||'untitled')+'', complete:!!t.complete,
                type:(a.type||t.type)+'', dest:(a.destination||t.destination)+'', src:src,
                is_unlocked:(typeof t.is_unlocked==='undefined'? true : t.is_unlocked),
                locked_category_criteria:(t.locked_category_criteria||a.locked_category_criteria||'')+''};
      };
      const out = {points:(j.dashboard.userStatus||{}).availablePoints, tasks:[]};
      const dsp=(d.dailySetPromotions||{})['"""+today+"""']||[];
      (dsp||[]).forEach(t=>out.tasks.push(norm(t,'dailySet')));
      (d.morePromotions||[]).forEach(t=>out.tasks.push(norm(t,'more')));
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


def is_skippable(t):
    """判断是否不可自动完成。
    2026-09-09 修正：rnoreward=1 **不是**不计分标志——观星建议 带 rnoreward=1 但实点卡片能得分，
    故不再据此跳过。真正不可网页完成的判据：
      - type appinstall/mobile/punchcard : 需安装 App / 移动端
      - dest 含 referandearn             : 邀请推荐，需朋友参与
      - is_unlocked=False 或 locked_category_criteria=rewardsApp : 需 Rewards App（锁🔒仅限积分商城应用）
    其余 urlreward/float/search/quiz 一律尝试"卡片实点+保留tab+轮询"，成败由轮询判定。"""
    typ = t["type"].lower()
    if typ in ("appinstall", "mobile", "app", "punchcard") or "install" in typ:
        return True, "需安装/移动端"
    if "referandearn" in t["dest"]:
        return True, "邀请推荐类，需朋友参与"
    if t.get("is_unlocked") is False or (t.get("locked_category_criteria") or "").strip():
        return True, f"需 Rewards App（{t.get('locked_category_criteria') or 'locked'}）"
    return False, ""


def main():
    if main_script_running():
        print("[x] 主脚本/rewards_daily 正在运行，稍后重试")
        return 1
    if not ensure_edge():
        print("[x] Edge CDP 不可用")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/earn", 12)

    data = fetch_tasks(ws)
    if not data:
        ws.close(); return 1
    print(f"当前积分: {data['points']}")

    todo = [t for t in data["tasks"] if not t["complete"]]
    if TARGET_KEYWORDS:
        todo = [t for t in todo if any(k in t["title"] for k in TARGET_KEYWORDS)]
    # 过滤掉不可自动完成的
    hand = []
    for t in todo:
        skip, why = is_skippable(t)
        if skip:
            print(f"  ⏭ 跳过: {t['title']}（{why}）")
        else:
            hand.append(t)
    if not hand:
        print("\n没有可自动补做的任务")
        ws.close(); return 0

    print(f"\n== 准备补做 {len(hand)} 个可计分任务 ==")
    for i, t in enumerate(hand, 1):
        title = t["title"]
        print(f"\n[{i}/{len(hand)}] {title}  dest={t['dest'][:80]}")
        before = {x["id"] for x in list_tabs()}
        cdp_nav(ws, "https://rewards.bing.com/earn", 10)
        time.sleep(3)
        r = find_task_anchor(ws, title, t["dest"] if t["dest"].startswith("http") else "", timeout=22)
        print(f"  click: {r}")
        if str(r).startswith("NO_ANCHOR"):
            # 兜底：直访 destination（无 tracking 时可能不计，仅作尝试）
            if t["dest"].startswith("http"):
                print("  (找不到卡片) 直访 destination 试试")
                try:
                    cdp_nav(ws, t["dest"], 8)
                except Exception as e:
                    print("  nav err:", e)
                time.sleep(20)
            continue
        nws, ntab = wait_for_new_tab(before, max_wait=12)
        if nws and ntab:
            try:
                tb = [x for x in list_tabs() if x["id"] == ntab][0]
                print(f"  new tab: {tb.get('url','')[:100]}")
            except Exception:
                pass
            nws.close()  # 只断 CDP，保留浏览器 tab 让 tracking 跑完
            print("  保留新 tab，回 dashboard 轮询计分...")
        else:
            print("  未捕获新 tab，等待补偿")
            time.sleep(30)
        # 回 dashboard 轮询（dailyset 成功经验）
        cdp_nav(ws, "https://rewards.bing.com/dashboard", 8)
        time.sleep(8)
        done = False
        for _ in range(6):
            time.sleep(15)
            s1 = None
            try:
                s1 = fetch_tasks(ws)
            except Exception:
                s1 = None
            if s1:
                hit = next((x for x in s1["tasks"] if x["title"] == title), None)
                if hit and hit["complete"]:
                    done = True
                    print("  ✅ 计分已生效")
                    break
        if not done:
            print("  ⏳ 90s 内未计分（可能该任务今日不计奖励）")
        if ntab:
            close_tab(ntab)

    # 最终汇总
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 10)
    time.sleep(5)
    final = fetch_tasks(ws)
    if final:
        print(f"\n== 最终积分: {final['points']} ==")
        for t in final["tasks"]:
            if not t["complete"]:
                print(f"  ⬜ {t['title']}")
    ws.close()
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
