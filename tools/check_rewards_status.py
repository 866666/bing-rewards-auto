# -*- coding: utf-8 -*-
"""check_rewards_status.py - 快速诊断 Rewards 状态与界面语言
用法: python tools/check_rewards_status.py
输出: 积分、各计数器、dashboard 页面语言、Bing 搜索页语言、截图
"""
import sys, io, json, time, base64, urllib.request, urllib.parse, subprocess, os
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
EDGE_ARGS = ("--remote-debugging-port=9224 --remote-allow-origins=* "
             "--user-data-dir=" + PROFILE + " "
             "--no-first-run --no-default-browser-check about:blank")
SHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rewards_diag.png")


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
        print("  [x] msedge.exe not found")
        return False
    try:
        subprocess.Popen(f'"{exe}" {EDGE_ARGS}', shell=False,
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


def cdp_nav(ws, url, wait=8):
    cdp_send(ws, "Page.enable")
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)


def cdp_shot(ws, path):
    r = cdp_send(ws, "Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["result"]["data"]))
    print(f"  screenshot -> {path}")


def api(ws):
    js = """(() => {
      try {
        const x = new XMLHttpRequest();
        x.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
        x.send();
        if (x.status !== 200) return 'API_ERR:' + x.status;
        return x.responseText;
      } catch (e) { return 'JS_EXC:' + e; }
    })()"""
    v = cdp_js(ws, js)
    if not v or v.startswith("API_ERR") or v.startswith("JS_EXC"):
        print(f"  [x] getuserinfo: {v}")
        return None
    try:
        return json.loads(v)
    except Exception as e:
        print(f"  [x] parse error: {e}")
        return None


def main():
    if not ensure_edge():
        print("  [x] CDP not reachable")
        return 1
    ws = get_ws()
    print("== 1) rewards dashboard (user-opened URL) ==")
    cdp_nav(ws, "https://rewards.bing.com/dashboard?ref=rewardspanel", wait=10)
    lang = cdp_js(ws, "document.documentElement.lang")
    title = cdp_js(ws, "document.title")
    print(f"  html lang: {lang!r}  title: {title!r}")
    print("  activity text (search-related lines):")
    pt = cdp_js(ws, "document.body && document.body.innerText ? document.body.innerText : ''") or ""
    for line in pt.splitlines():
        s = line.strip()
        if s and ("search" in s.lower() or "Search" in s or "30" in s or "minutes" in s.lower() or "Activity" in s):
            print("   | " + s)
    data = api(ws)
    if data:
        cnt = data.get("counters") or {}
        print("  counters: " + json.dumps(cnt, ensure_ascii=False))
        am = data.get("accountModel") or {}
        for k in ("userSince", "userStatus", "id", "levels"):
            if k in am:
                print(f"  accountModel.{k}: {json.dumps(am[k], ensure_ascii=False)[:200]}")
        if "justForYou" in data:
            print("  justForYou: " + json.dumps(data["justForYou"], ensure_ascii=False)[:200])
        if "dailySetPromotions" in data:
            print("  dailySetPromotions: " + json.dumps(data["dailySetPromotions"], ensure_ascii=False)[:200])
    else:
        print("  (api unavailable)")

    print("== 2) bing search page (as automation opens it) ==")
    cdp_nav(ws, "https://www.bing.com/?cc=us&setmkt=en-US&setlang=en", wait=8)
    lang2 = cdp_js(ws, "document.documentElement.lang")
    title2 = cdp_js(ws, "document.title")
    q = cdp_js(ws, "location.href")
    print(f"  final url: {q}")
    print(f"  html lang: {lang2!r}  title: {title2!r}")
    cdp_shot(ws, SHOT)
    ws.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())