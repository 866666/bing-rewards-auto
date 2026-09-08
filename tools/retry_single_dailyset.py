# -*- coding: utf-8 -*-
"""retry_single_dailyset.py - 按 destination/BTEPOKey 精确补做单个漏做的每日任务
用法: python tools/retry_single_dailyset.py
策略: 1) 直接导航 destination 停留 60s → 验证；2) 若失败则 dashboard 按 href 匹配卡片实点击 → 新 tab 停留 60s → 验证
"""
import sys, json, time, urllib.request, urllib.parse, subprocess, os, socket
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- 绕过本地 CDP 的系统代理（Clash 2718 拦截 loopback 会返回 502）---
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

CDP_HTTP = "http://127.0.0.1:9224"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_edge():
    """若 9224 无 CDP 实例则拉起一个调试 Edge（用自动化专用 profile，避开用户自己的 Edge）。"""
    try:
        urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=4)
        print("[edge] CDP 已在运行")
        return True
    except Exception:
        pass
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(exe):
        exe = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    profile = os.path.join(BASE_DIR, "edge_debug_profile")
    print("[edge] 启动调试 Edge...")
    subprocess.Popen(
        [exe, "--remote-debugging-port=9224", "--remote-allow-origins=*",
         f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
         "--disable-features=msEdgeFirstRunExperience", "about:blank"],
        shell=False, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True)
    for _ in range(45):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=3)
            time.sleep(2)
            print("[edge] Edge 就绪")
            return True
        except Exception:
            continue
    print("[edge] Edge 启动失败")
    return False


def get_ws():
    pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
    pages = [x for x in pages if x.get("type") == "page"]
    if not pages:
        raise RuntimeError("no page")
    import websocket
    return websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)


def list_tabs():
    try:
        pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
        return [x for x in pages if x.get("type") == "page"]
    except Exception:
        return []


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
                 {"expression": expr, "returnByValue": True, "awaitPromise": await_promise}, timeout=timeout)
    return r.get("result", {}).get("result", {}).get("value")


def cdp_nav(ws, url, wait=10):
    cdp_send(ws, "Page.enable")
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)


def read_state(ws):
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
        xhr.send();
        if (xhr.status !== 200) return 'ERR:' + xhr.status;
        const j = JSON.parse(xhr.responseText);
        const dsp = (j.dashboard.dailySetPromotions || {})['""" + today + """'] || [];
        const us = j.dashboard.userStatus;
        return JSON.stringify({
          points: us.availablePoints,
          tasks: dsp.map(t => ({title: t.title, complete: t.complete,
                                dest: (t.attributes && t.attributes.destination) || ''}))
        });
      } catch(e) { return 'EXC:' + e.message; }
    })()"""
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    return json.loads(v) if v and str(v)[0] == "{" else None


def click_by_href(ws, keyword, before_ids):
    """按 href 关键词点卡片（避开文本语言问题）"""
    j = """(() => {
      const kw = arguments[0];
      const anchors = [...document.querySelectorAll('a[target=_blank][href*="bing.com"]')];
      const a = anchors.find(e => (e.getAttribute('href') || '').includes(kw));
      if (!a) return 'NO_ANCHOR';
      a.click();
      return 'CLICKED';
    })()""".replace("arguments[0]", json.dumps(keyword))
    v = cdp_js(ws, j, timeout=15)
    # 等新 tab
    for _ in range(12):
        for t in list_tabs():
            if t["id"] not in before_ids:
                import websocket
                return websocket.create_connection(t["webSocketDebuggerUrl"], timeout=60, suppress_origin=True), t["id"]
        time.sleep(1)
    return None, None


def main():
    if not ensure_edge():
        print("[x] Edge 无法启动，无法补做任务")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 14)
    s0 = read_state(ws)
    if not s0:
        print("[x] 无法读取状态")
        return 1
    todo = [t for t in s0["tasks"] if not t["complete"]]
    print("baseline:", s0["points"], "todo:", [t["title"] for t in todo])
    if not todo:
        print("✅ 全部完成")
        return 0

    dst = todo[0]["dest"]
    title = todo[0]["title"]
    print(f"\n[策略1] 直接导航 destination 并停留 60s: {dst[:120]}")

    # 关掉旧 tab 干扰：用新 tab 操作
    before = {x["id"] for x in list_tabs()}
    cdp_nav(ws, dst, 10)
    print("  停留 60s 让 tracking 生效...")
    time.sleep(60)

    cdp_nav(ws, "https://rewards.bing.com/dashboard", 14)
    time.sleep(10)
    s1 = read_state(ws)
    t1 = [t for t in (s1["tasks"] if s1 else []) if t["title"] == title]
    ok = bool(t1 and t1[0]["complete"])
    print(f"  策略1 结果: {'✅ 完成' if ok else '❌ 未完成'}")

    if not ok:
        print(f"\n[策略2] dashboard 按 href 匹配卡片实点击（kw=BTDSUOID 片段）")
        kw = dst.split("BTDSUOID")[0][-30:] if "BTDSUOID" in dst else dst.split("q=")[1][:20]
        # 用 destination 里稳定的标识：Child1 / rnoreward
        marker = "Child1" if "Child1" in dst else urllib.parse.quote(dst.split("?")[1][:30])
        before = {x["id"] for x in list_tabs()}
        r = cdp_js(ws, """(() => {
          const m = arguments[0];
          const anchors = [...document.querySelectorAll('a[target=_blank][href*="bing.com"]')];
          const a = anchors.find(e => (e.getAttribute('href')||'').includes(m));
          if (!a) return 'NO_ANCHOR';
          a.click(); return 'CLICKED';
        })()""".replace("arguments[0]", json.dumps(marker)), timeout=15)
        print(f"  click: {r}")
        if str(r) == "CLICKED":
            time.sleep(18)
            cdp_nav(ws, "https://rewards.bing.com/dashboard", 14)
            time.sleep(10)
            s2 = read_state(ws)
            t2 = [t for t in (s2["tasks"] if s2 else []) if t["title"] == title]
            ok = bool(t2 and t2[0]["complete"])
            print(f"  策略2 结果: {'✅ 完成' if ok else '❌ 未完成'}")
    ws.close()
    print("\n最终:", "全部 Daily Set 完成 ✅" if ok else "仍待处理，可稍后重试")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())