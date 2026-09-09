# -*- coding: utf-8 -*-
"""complete_dailyset.py - 完成当天 Daily Set 的 3 个 +10 任务（urlreward 类型）
关键：必须从 rewards dashboard 页面"真实点击"任务卡片（target=_blank 新开 bing 搜索页，
referer + JS tracking 触发计分）；直接 Page.navigate 到 destination 实测不计分。
用法: python tools/complete_dailyset.py
"""
import sys, json, time, urllib.request, urllib.parse, subprocess, os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- 绕过本地 CDP 的系统代理（Clash 2718 拦截 loopback 会返回 502）---
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

CDP_HTTP = "http://127.0.0.1:9224"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
PROFILE = os.path.join(BASE_DIR, "edge_debug_profile")
EDGE_CANDIDATES = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
EDGE_ARG_LIST = [
    "--remote-debugging-port=9224",
    "--remote-allow-origins=*",
    f"--user-data-dir={PROFILE}",
    "--no-first-run", "--no-default-browser-check", "about:blank",
]
WAIT_PER_TASK = 18  # 每个任务在新 tab 停留秒数（tracking 生效）
WAIT_SETTLE = 20    # 全部完成后等待计分到账

def cdp_alive():
    try:
        urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=3)
        return True
    except Exception:
        return False

def ensure_edge(max_wait=45):
    if cdp_alive():
        return True
    exe = next((e for e in EDGE_CANDIDATES if os.path.exists(e)), None)
    if not exe:
        print("  [x] msedge not found")
        return False
    try:
        subprocess.Popen([exe] + EDGE_ARG_LIST, shell=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as e:
        print(f"  [x] Edge start failed: {e}")
        return False
    for _ in range(max_wait):
        time.sleep(1)
        if cdp_alive():
            time.sleep(3)
            return True
    return False

def cdp_new_page(url="about:blank"):
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(f"{CDP_HTTP}/json/new?{urllib.parse.quote(url, safe='')}", method=method)
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            continue
    return False

def get_ws():
    pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
    pages = [x for x in pages if x.get("type") == "page"]
    if not pages:
        if cdp_new_page():
            time.sleep(3)
            pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
            pages = [x for x in pages if x.get("type") == "page"]
        if not pages:
            raise RuntimeError("no page")
    import websocket
    return websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)

def cdp_send(ws, method, params=None, timeout=60):
    cdp_send.id = getattr(cdp_send, "id", 0) + 1
    ws.send(json.dumps({"id": cdp_send.id, "method": method, "params": params or {}}))
    dl = time.time() + timeout
    while time.time() < dl:
        ws.settimeout(max(1, dl - time.time()))
        try:
            r = json.loads(ws.recv())
        except Exception:
            continue
        if r.get("id") == cdp_send.id:
            return r
    raise TimeoutError(method)

def cdp_js(ws, expr, timeout=30, await_promise=False):
    r = cdp_send(ws, "Runtime.evaluate",
                 {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
                 timeout=timeout)
    return r.get("result", {}).get("result", {}).get("value")

def cdp_nav(ws, url, wait=10):
    cdp_send(ws, "Page.enable")
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)

def read_state(ws):
    """读积分 + 当天 dailySet 状态"""
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
        xhr.send();
        if (xhr.status !== 200) return 'API_ERR:' + xhr.status;
        const j = JSON.parse(xhr.responseText);
        const us = j.dashboard.userStatus;
        const dsp = (j.dashboard.dailySetPromotions || {})['""" + today + """'] || [];
        const ctrs = us.counters || {};
        return JSON.stringify({
          points: us.availablePoints,
          dailySetComplete: (ctrs.activityAndQuiz?.[0]?.pointProgress ?? '?') + '/' + (ctrs.activityAndQuiz?.[0]?.pointProgressMax ?? '?'),
          tasks: dsp.map(t => ({title: t.title, complete: t.complete, hash: t.hash}))
        });
      } catch(e) { return 'JS_EXC:' + e.message; }
    })()"""
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    if not v or str(v).startswith("API_ERR") or str(v).startswith("JS_EXC"):
        print(f"  [x] api: {v}")
        return None
    return json.loads(v)

def read_tasks(ws):
    """拿当天任务列表（含 destination）"""
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
        xhr.send();
        if (xhr.status !== 200) return 'API_ERR:' + xhr.status;
        const j = JSON.parse(xhr.responseText);
        const dsp = (j.dashboard.dailySetPromotions || {})['""" + today + """'] || [];
        return JSON.stringify(dsp.map(t => ({
          title: t.title, complete: t.complete, type: t.attributes ? t.attributes.type : null,
          destination: t.attributes ? t.attributes.destination : null
        })));
      } catch(e) { return 'JS_EXC:' + e.message; }
    })()"""
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    if not v or str(v).startswith("API_ERR") or str(v).startswith("JS_EXC"):
        print(f"  [x] api: {v}")
        return []
    return json.loads(v)

def list_tabs():
    """列出当前所有 page tab"""
    try:
        pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
        return [x for x in pages if x.get("type") == "page"]
    except Exception:
        return []


def close_tab(tab_id):
    try:
        urllib.request.urlopen(f"{CDP_HTTP}/json/close/{tab_id}", timeout=5)
    except Exception:
        pass


def task_marker(destination, title):
    """从 destination 提取稳定标识用于 href 匹配（比 innerText 匹配可靠）。
    优先取 ?q= 参数（URL 解码），回退 path/query 片段。"""
    marker = ""
    if destination:
        try:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(destination).query).get("q", [""])[0]
            if q:
                marker = urllib.parse.unquote(q)[:30]
        except Exception:
            pass
        if not marker:
            marker = destination.split("bing.com")[-1][:30]
    return marker or (title or "").strip()[:4]


def find_task_anchor(ws, title, destination="", timeout=25):
    """在 dashboard 找任务卡 <a> 并真实点击（触发 React onClick + target=_blank 新开窗口）。
    先按 href 匹配 destination 稳定片段（更可靠），回退 innerText 匹配标题；
    轮询等待卡片渲染（dashboard 懒加载，首次任务常见未渲染导致的 NO_ANCHOR）。"""
    marker = task_marker(destination, title)
    kw_title = (title or "").strip()
    js = """(async () => {
      const marker = arguments[0];
      const kwTitle = arguments[1];
      const deadline = arguments[2] * 1000;
      const t0 = Date.now();
      while (Date.now() - t0 < deadline) {
        const anchors = [...document.querySelectorAll('a[target=_blank][href*="bing.com"]')];
        let a = null;
        if (marker) a = anchors.find(e => (e.getAttribute('href') || '').includes(marker));
        if (!a && kwTitle) a = anchors.find(e => (e.innerText || '').includes(kwTitle.slice(0, 4)));
        if (a) { a.click(); return 'CLICKED'; }
        window.scrollBy(0, 500);  // 触发懒加载
        await new Promise(r => setTimeout(r, 800));
      }
      return 'NO_ANCHOR:' + marker;
    })()""".replace("arguments[2]", str(timeout))
    r = cdp_js(ws, js, timeout=timeout + 10, await_promise=True)
    if str(r) == "CLICKED":
        return r
    # 兜底：再按 innerText 完整标题试一次
    js2 = """(() => {
      const kwTitle = arguments[0];
      const anchors = [...document.querySelectorAll('a[target=_blank][href*="bing.com"]')];
      const a = anchors.find(e => (e.innerText || '').includes(kwTitle));
      if (!a) return 'NO_ANCHOR2';
      a.click(); return 'CLICKED';
    })()""".replace("arguments[0]", json.dumps(kw_title))
    return cdp_js(ws, js2, timeout=15)


def wait_for_new_tab(before_ids, max_wait=12):
    """点击后等待新开的搜索 tab 出现，返回 (ws, tab_id)"""
    for _ in range(max_wait):
        tabs = list_tabs()
        for t in tabs:
            if t["id"] not in before_ids:
                try:
                    import websocket
                    w = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)
                    return w, t["id"]
                except Exception as e:
                    time.sleep(1)
                    continue
        time.sleep(1)
    return None, None


def main():
    if not ensure_edge():
        print("[x] CDP unreachable")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)

    s0 = read_state(ws)
    print("baseline :", json.dumps(s0, ensure_ascii=False) if s0 else "n/a")

    tasks = read_tasks(ws)
    todo = [t for t in tasks if not t["complete"] and t.get("destination")]
    print(f"todo     : {len(todo)} 个未完成任务")
    if not todo:
        print("今天的 Daily Set 已完成或没有 destination，无需操作")
        ws.close()
        return 0

    for i, t in enumerate(todo, 1):
        title = t["title"]
        print(f"\n[{i}/{len(todo)}] {title}  type={t['type']}")
        before = {x["id"] for x in list_tabs()}
        r = find_task_anchor(ws, title, t.get("destination", ""))
        print(f"  click: {r}")
        if str(r).startswith("NO_ANCHOR"):
            print("  ! 找不到卡片锚点，跳过")
            continue
        nws, ntab = wait_for_new_tab(before)
        if nws and ntab:
            try:
                tb = [x for x in list_tabs() if x["id"] == ntab][0]
                print(f"  new tab opened: {tb.get('url', '')[:100]}")
            except Exception:
                pass
            nws.close()  # 只断开 CDP 连接，保留浏览器 tab 让 tracking 跑完
            print("  保留新 tab，回 dashboard 轮询计分（最长 90s）...")
        else:
            print("  ! 未捕获新 tab，等待补偿")
            time.sleep(WAIT_PER_TASK)
        # 回 dashboard 并轮询该任务是否完成
        cdp_nav(ws, "https://rewards.bing.com/dashboard", 8)
        time.sleep(8)
        done = False
        for _ in range(5):  # 5×15s = 75s + 基线 ≈ 90s
            time.sleep(15)
            try:
                s1 = read_state(ws)
            except Exception:
                s1 = None
            if s1:
                tt = [x for x in s1.get("tasks") or [] if x.get("title") == title]
                if tt and tt[0].get("complete"):
                    done = True
                    print("  ✅ 计分已生效")
                    break
        if not done:
            print("  ⏳ 90s 内未计分（可能任务本身今日不计奖励 rnoreward，非脚本问题）")
        if ntab:
            close_tab(ntab)

    print(f"\n== waiting {WAIT_SETTLE}s for credits to land ==")
    time.sleep(WAIT_SETTLE)
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)  # 回 rewards 域，避免 XHR 跨域失败
    s1 = read_state(ws)
    print("after    :", json.dumps(s1, ensure_ascii=False) if s1 else "n/a")
    if s0 and s1:
        print("\n== diff ==")
        print(f"  points: {s0.get('points')} -> {s1.get('points')}")
        for a, b in zip(s0.get("tasks") or [], s1.get("tasks") or []):
            mark = "✅" if b.get("complete") else "❌"
            print(f"  {mark} {b.get('title')}: {a.get('complete')} -> {b.get('complete')}")
    ws.close()
    print("\nDONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())