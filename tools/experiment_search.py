# -*- coding: utf-8 -*-
"""experiment_search.py - 复现油猴脚本的搜索方式，验证 30/30 满后是否还能计分
用法: python tools/experiment_search.py [搜索次数]
输出: 搜索前后 getuserinfo 对比（points / pcSearch / dailyPoint），侧栏进度文本
"""
import sys, json, time, base64, urllib.request, urllib.parse, subprocess, os
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

TERMS = ["python programming", "deep learning", "weather today",
         "best coffee", "space news", "linux tips", "AI tools",
         "travel guide", "book recommendations", "fitness plan"]

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

def cdp_nav(ws, url, wait=8):
    cdp_send(ws, "Page.enable")
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)

def api_state(ws):
    """主脚本同款：dashboard.getuserinfo → 返回状态字符串"""
    today = time.strftime("%m/%d/%Y")
    js = """(async () => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
        xhr.send();
        if (xhr.status !== 200) return 'API_ERR:' + xhr.status;
        const j = JSON.parse(xhr.responseText);
        const us = j.dashboard.userStatus;
        const ctrs = us.counters || {};
        return JSON.stringify({
          points: us.availablePoints,
          level: us.levelInfo ? us.levelInfo.activeLevelName : null,
          pcSearch: (ctrs.pcSearch?.[0]?.attributes?.progress) + '/' + (ctrs.pcSearch?.[0]?.attributes?.max),
          activity: (ctrs.activityAndQuiz?.[0]?.pointProgress ?? '?') + '/' + (ctrs.activityAndQuiz?.[0]?.pointProgressMax ?? '?'),
          dailyPoint: (ctrs.dailyPoint?.[0]?.pointProgress ?? '?') + '/' + (ctrs.dailyPoint?.[0]?.pointProgressMax ?? '?'),
          today: '""" + today + """',
          dailySet3: (j.dashboard.dailySetPromotions?.['""" + today + """'] || []).length
        });
      } catch(e) { return 'JS_EXC:' + e.message; }
    })()"""
    v = cdp_js(ws, js, timeout=30, await_promise=True)
    if not v or str(v).startswith("API_ERR") or str(v).startswith("JS_EXC"):
        print(f"  [x] api: {v}")
        return None
    return json.loads(v)

def read_sidebar_progress(ws):
    """复现油猴：点开积分侧栏，读 .daily_search_row span:last-child 文本"""
    try:
        cdp_js(ws, """(() => { const c = document.querySelector('.points-container'); if (c) { c.click(); return true; } return false; })()""")
        time.sleep(4)
        js = """(() => {
          const f = document.querySelector('iframe');
          if (!f) return 'NO_IFRAME';
          try {
            const d = f.contentDocument || f.contentWindow.document;
            const el = d.querySelector('.daily_search_row span:last-child');
            if (el) return 'PROGRESS=' + el.textContent.trim();
            const t = d.body ? d.body.innerText : '';
            const m = t.match(/\\d+\\s*\\/\\s*\\d+/);
            return m ? 'TEXT_MATCH=' + m[0] : 'TEXT=' + t.slice(0, 300).replace(/\\n/g, ' | ');
          } catch (e) { return 'IFRAME_ERR:' + e.message; }
        })()"""
        return cdp_js(ws, js)
    except Exception as e:
        return f"READ_ERR:{e}"

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    if not ensure_edge():
        print("[x] CDP unreachable")
        return 1
    ws = get_ws()
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)
    s0 = api_state(ws)
    print("baseline :", json.dumps(s0, ensure_ascii=False) if s0 else "n/a")

    print("sidebar  :", read_sidebar_progress(ws))

    print(f"== doing {n} searches like the userscript (cn interface, form submit) ==")
    for i in range(n):
        term = TERMS[i % len(TERMS)]
        cdp_nav(ws, "https://www.bing.com/?setlang=zh-hans", 5)  # 中文界面，无美区强制参数
        r = cdp_js(ws, """(() => {
          const input = document.querySelector('#sb_form_q, input[name=q], textarea[name=q]');
          if (!input) return 'NO_INPUT';
          input.value = arguments[0];
          const form = document.querySelector('#sb_form');
          if (form) { form.submit(); return 'SUBMIT'; }
          return 'NO_FORM';
        })()""".replace("arguments[0]", json.dumps(term)))
        time.sleep(7)  # 油猴同款间隔 5-10s
    print("== waiting for credits to land (30s) ==")
    time.sleep(30)
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)  # 必须先回到 rewards 域，否则 XHR 跨域失败
    s1 = api_state(ws)
    print("after    :", json.dumps(s1, ensure_ascii=False) if s1 else "n/a")
    if s0 and s1:
        print("\n== diff ==")
        for k in ("points", "pcSearch", "activity", "dailyPoint", "dailySet3"):
            print(f"  {k}: {s0.get(k)} -> {s1.get(k)}")
    ws.close()
    print("DONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())