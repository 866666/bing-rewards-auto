# -*- coding: utf-8 -*-
"""check_status.py — 只读巡检 Microsoft Rewards 当前状态并截图（不点击任何任务）

输出：积分/等级 + 各类配额（pcSearch/activityAndQuiz/dailyPoint）+ 当日 Daily Set 完成情况
截图：项目根 dashboard_check.png（看板全页）

用法: python tools/check_status.py
"""
import sys, json, os, time, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

from complete_dailyset import ensure_edge, get_ws, cdp_nav, cdp_js, cdp_send

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(ROOT, "dashboard_check.png")


def main():
    if not ensure_edge():
        print("[x] Edge CDP 不可用（9224）")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)
    time.sleep(5)

    # 注意：API 的 dailySetPromotions key 是 "MM/DD/YYYY"（带前导零），
    # 必须用 Python 的 strftime 生成并注入，JS 的 toLocaleDateString('en-US') 会得到 "9/10/2026" 匹配不上
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      const today = __TODAY__;
      const xhr = new XMLHttpRequest();
      xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
      xhr.send();
      if (xhr.status !== 200) return 'API_ERR:' + xhr.status;
      const j = JSON.parse(xhr.responseText);
      const us = j.dashboard.userStatus || {};
      const c = us.counters || {};
      const dsp = (j.dashboard.dailySetPromotions || {})[today] || [];
      const cnt = (x) => x && x[0] ? (x[0].pointProgress + '/' + x[0].pointProgressMax) : '?';
      const pcs = (c.pcSearch && c.pcSearch[0] && c.pcSearch[0].attributes)
        ? c.pcSearch[0].attributes.progress + '/' + c.pcSearch[0].attributes.max : '?';
      return JSON.stringify({
        points: us.availablePoints,
        level: us.levelInfo && us.levelInfo.activeLevelName,
        pcSearch: pcs,
        activityAndQuiz: cnt(c.activityAndQuiz),
        dailyPoint: cnt(c.dailyPoint),
        dailySet: dsp.map(t => ({title: t.title, done: !!t.complete})),
      });
    })()""".replace("__TODAY__", json.dumps(today))
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    if not v or str(v).startswith("API_ERR"):
        print(f"[x] 读取失败: {v}（可能 API 被区域封锁）")
        ws.close()
        return 1

    st = json.loads(v)
    dset = st.get("dailySet") or []
    done = sum(1 for t in dset if t["done"])
    print("=" * 50)
    print(f"积分: {st.get('points')}  |  等级: {st.get('level')}")
    print(f"PC 搜索: {st.get('pcSearch')}  活动与测验: {st.get('activityAndQuiz')}  每日积分: {st.get('dailyPoint')}")
    print(f"每日活动 (Daily Set): {done}/{len(dset) if dset else 3}")
    for t in dset:
        print(f"   {'✅' if t['done'] else '⬜'} {t['title']}")
    print("=" * 50)

    # 截图留档
    try:
        r = cdp_send(ws, "Page.captureScreenshot",
                     {"format": "png", "captureBeyondViewport": True}, timeout=30)
        data = r.get("result", {}).get("data")
        if data:
            open(SHOT, "wb").write(base64.b64decode(data))
            print(f"截图: {SHOT}")
    except Exception as e:
        print(f"截图失败: {e}")
    ws.close()
    return 0 if (dset and done == len(dset)) else 0


if __name__ == "__main__":
    sys.exit(main())
