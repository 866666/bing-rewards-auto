# -*- coding: utf-8 -*-
"""probe_dailyset.py - 探测当天 Daily Set 任务的数据结构（title/complete/URL/attributes）
用法: python tools/probe_dailyset.py [端口]
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
EDGE_ARGS = ("--remote-debugging-port=9224 --remote-allow-origins=* "
             "--user-data-dir=" + PROFILE + " "
             "--no-first-run --no-default-browser-check about:blank")

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

def cdp_nav(ws, url, wait=10):
    cdp_send(ws, "Page.enable")
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)

def main():
    if not ensure_edge():
        print("[x] CDP unreachable")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
        xhr.send();
        if (xhr.status !== 200) return 'API_ERR:' + xhr.status;
        const j = JSON.parse(xhr.responseText);
        const dsp = j.dashboard.dailySetPromotions || {};
        const list = dsp['""" + today + """'] || [];
        return JSON.stringify(list.map(t => {
          const a = t.attributes || {};
          return {
            title: t.title, complete: t.complete, hash: t.hash,
            type: a.type, offerid: a.offerid, state: a.state,
            progress: a.progress, max: a.max,
            destination: a.destination, link_text: a.link_text,
            query_comment: a.query_comment, give_eligible: a.give_eligible
          };
        }), null, 1);
      } catch(e) { return 'JS_EXC:' + e.message; }
    })()"""
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    print("today:", today)
    print(v)
    ws.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())