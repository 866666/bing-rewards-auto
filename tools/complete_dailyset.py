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

def _eval_with_retry(ws, js, tries=3, delay=6, timeout=30):
    """执行返回字符串的 XHR JS；遇到 API_ERR / JS_EXC / 空值自动重试。
    2026-09-12 教训：rewards API 会偶发不可达（JS_EXC: Failed to load getuserinfo），
    一次性读取失败曾被误当成"任务已完成"。这里统一做重试，仍失败则返回 None。"""
    last = None
    for i in range(1, tries + 1):
        try:
            v = cdp_js(ws, js, timeout=timeout, await_promise=True)
        except Exception as e:
            v = f"JS_EXC:{e}"
        if v and not str(v).startswith(("API_ERR", "JS_EXC")):
            return v
        last = v
        print(f"  [x] api({i}/{tries}): {v}")
        if i < tries:
            time.sleep(delay)
    return None


def read_state(ws):
    """读积分 + 当天 dailySet 状态。
    ⚠ 返回 None 表示 API 不可达（≠ 已完成）；调用方必须区分 None 与真实 0 未完成。"""
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
          level: us.levelInfo ? us.levelInfo.activeLevelName : null,
          pcSearch: (ctrs.pcSearch?.[0]?.attributes?.progress ?? '?') + '/' + (ctrs.pcSearch?.[0]?.attributes?.max ?? '?'),
          activity: (ctrs.activityAndQuiz?.[0]?.pointProgress ?? '?') + '/' + (ctrs.activityAndQuiz?.[0]?.pointProgressMax ?? '?'),
          dailyPoint: (ctrs.dailyPoint?.[0]?.pointProgress ?? '?') + '/' + (ctrs.dailyPoint?.[0]?.pointProgressMax ?? '?'),
          dailySetDone: dsp.reduce((s, t) => s + (t.complete ? 1 : 0), 0),
          dailySetTotal: dsp.length,
          tasks: dsp.map(t => ({title: t.title, complete: !!t.complete, hash: t.hash}))
        });
      } catch(e) { return 'JS_EXC:' + e.message; }
    })()"""
    v = _eval_with_retry(ws, js)
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def read_tasks(ws):
    """拿当天任务列表（含 destination）。
    ⚠ 返回 None = API 不可达（**绝不等于"没有任务"**）；返回 [] = API 正常但今日列表为空。"""
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
          title: t.title, complete: !!t.complete, type: t.attributes ? t.attributes.type : null,
          destination: t.attributes ? t.attributes.destination : null
        })));
      } catch(e) { return 'JS_EXC:' + e.message; }
    })()"""
    v = _eval_with_retry(ws, js)
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None

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


# 奖励归属参数标记（仅供参考的**提示**，不是"跳过"依据）。
# 2026-09-14：曾因当日 Child2「芝加哥湖畔秋日清凉」的链接只有 filters=sid:"..."（无下面这些参数），
# 误判为"微软侧坏活动、无法完成"并加了跳过逻辑 —— 随后用户**手动点击即完成**，证明判断错误。
# 真正原因是脚本用 el.click()（非受信任事件）点不动该卡片，已改为 CDP 真实鼠标事件。
# 保留此函数仅用于在不计分时提示方向。
ATTR_MARKERS = ("BTEPOKey", "BTDSUOID", "PUBL=RewardsDO", "CREA=")


def offer_attributed(destination):
    """活动链接是否带常见奖励归属参数（仅作诊断提示）。"""
    return bool(destination) and any(m in destination for m in ATTR_MARKERS)


def js_click_index(ws, idx):
    """按索引直接派发 click —— **不需要坐标**。
    用于卡片尺寸恒为 0（页面布局异常）时兜底：坐标法完全失效，但 click 仍能触发导航。
    2026-10-09 A/B 实测：el.click() 同样能把 /search 页打开。"""
    js = """(() => {
      const a = [...document.querySelectorAll('a[target=_blank][href*="bing.com"]')][__IDX__];
      if (!a) return 'NO_ANCHOR2';
      const raw = a.getAttribute('href') || '';
      if (/["<>`\\s]/.test(raw)) {
        a.setAttribute('href', raw.replace(/"/g,'%22').replace(/</g,'%3C')
                                .replace(/>/g,'%3E').replace(/`/g,'%60').replace(/\\s/g,'%20'));
      }
      a.click();
      return 'JS_CLICK';
    })()""".replace("__IDX__", str(int(idx)))
    return cdp_js(ws, js, timeout=15)


def wait_cards_ready(ws, timeout=40, need=3):
    """等到 dashboard 的锚点**全部完成布局**。
    2026-11-10 实锤：只等"至少 1 张有尺寸"太宽松 —— 页面常处于**半渲染**状态
    （实测 6 个锚点里只有 2 个有尺寸，其余 rect 仍是 0），此时点第 2/3 张卡必然
    返回 BAD_RECT|…，而同一张卡在"全部就绪"时（就绪数 6）用真实鼠标点击一次就中。
    故判据改为：sized == total 立即就绪；否则等 sized 连续 3 次不变且 >= need 才算稳定。
    返回最终就绪数量。"""
    sized = total = 0
    last = -1
    stable = 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        o = None
        try:
            v = cdp_js(ws, """(() => {
              const SEL = 'a[target=_blank][href*="bing.com"]';
              const a = [...document.querySelectorAll(SEL)];
              let s = 0;
              for (const e of a) {
                const r = e.getBoundingClientRect();
                if (r.width > 5 && r.height > 5) s++;
              }
              return JSON.stringify({t: a.length, s: s, ready: document.readyState});
            })()""", timeout=10)
            if isinstance(v, str) and v.startswith("{"):
                o = json.loads(v)
        except Exception:
            o = None
        if o:
            total, sized = o.get("t", 0), o.get("s", 0)
            if sized >= need and sized >= total:
                return sized                     # 全部布局完成
            if sized == last:
                stable += 1
            else:
                stable = 0
                last = sized
            if stable >= 3 and sized >= need:
                return sized                     # 稳定但未全覆盖（可能有隐藏锚点）
        time.sleep(0.8)
    return sized


def goto_dashboard(ws, min_wait=10, card_timeout=40, reloads=1, need=3):
    """导航到 dashboard，并等到锚点**整体布局就绪**；未就绪则重载重试。
    返回就绪锚点数（< need 表示页面始终没渲染完整，调用方应走 JS 点击兜底）。"""
    n = 0
    for i in range(reloads + 1):
        try:
            cdp_send(ws, "Page.enable")
            # 让标签页进入前台：后台标签页有时不参与布局
            cdp_send(ws, "Page.bringToFront", timeout=10)
        except Exception:
            pass
        cdp_nav(ws, "https://rewards.bing.com/dashboard", min_wait)
        n = wait_cards_ready(ws, timeout=card_timeout, need=need)
        print(f"  dashboard 就绪锚点: {n}" + ("" if n >= need else f"  ← 未达 {need}，重载一次"))
        if n >= need:
            return n
    return n


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


def trusted_click(ws, x, y):
    """用 CDP Input.dispatchMouseEvent 派发**真实鼠标事件**（trusted user gesture）。
    2026-09-14：当日 Child2「芝加哥湖畔秋日清凉」用 `el.click()` 点了多轮始终不计分，
    而用户在同一台机器**手动点击即完成** —— 手动点击是 trusted 输入事件，
    `el.click()` 不是。故改用 CDP Input 源复现真实点击。（实测：真实鼠标事件下该卡片
    新 tab 2 秒内即正常加载 /search 页。）"""
    cdp_send(ws, "Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0}, timeout=15)
    time.sleep(0.15)
    cdp_send(ws, "Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
             timeout=15)
    time.sleep(0.08)
    cdp_send(ws, "Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
             timeout=15)


def find_task_anchor(ws, title, destination="", timeout=25):
    """在 dashboard 找任务卡 <a>，并**用真实鼠标事件点击**（等价手动点击，触发 target=_blank 新开窗口）。
    匹配策略（多级，最稳优先）：
      1) 锚点 href 的 q 参数解码后与 destination 的 q 一致（大小写不敏感）—— 中文搜索词专用
      2) innerText 含标题前 4 字  →  3) 完整标题兜底
    流程：JS 只负责「定位 + 转义畸形 href + 返回视口坐标」，点击交给 CDP Input 源（trusted）；
    若 Input 通道异常则回退 `el.click()`。返回 'TRUSTED_CLICK' / 'JS_CLICK'（可带 '|固定后URL'）。"""
    marker = task_marker(destination, title)
    kw_title = (title or "").strip()
    # 部分卡片的 href 里 filters 参数带**未编码的裸引号**（如 filters=sid:"9696dafd-..."），
    # 属微软渲染的畸形 URL；先做最小转义（只补非法字符，不动已编码的 %xx）再点，更安全。
    fix_href_js = """const SEL = 'a[target=_blank][href*="bing.com"]';
    const fixHref = (el) => {
      const raw = el.getAttribute('href') || '';
      if (/["<>`\\s]/.test(raw)) {
        const f = raw.replace(/"/g,'%22').replace(/</g,'%3C').replace(/>/g,'%3E')
                     .replace(/`/g,'%60').replace(/\\s/g,'%20');
        el.setAttribute('href', f);
        return f;
      }
      return '';
    };
    const pickIdx = (anchors, marker, kwTitle) => {
      const dec = marker ? marker.toLowerCase() : '';
      let i = -1;
      if (dec) i = anchors.findIndex(e => {
        let q = '';
        try { q = new URL(e.getAttribute('href'), location.origin).searchParams.get('q') || ''; } catch(err){}
        return q && q.toLowerCase().includes(dec);
      });
      if (i < 0 && kwTitle) i = anchors.findIndex(e => (e.innerText || '').includes(kwTitle.slice(0, 4)));
      if (i < 0 && kwTitle) i = anchors.findIndex(e => (e.innerText || '').includes(kwTitle));
      return i;
    };"""
    js = """(async () => {
      const marker = __MARKER__;
      const kwTitle = __KW__;
      const deadline = __TIMEOUT__ * 1000;
      const t0 = Date.now();
      let sawZero = false;          // 是否"找到了卡但尺寸一直是 0"（区别于真的找不到）
      let lastIdx = -1;             // 尺寸为 0 时也记住索引，供 JS 点击回退使用
      __FIXJS__
      while (Date.now() - t0 < deadline) {
        const anchors = [...document.querySelectorAll(SEL)];
        const i = pickIdx(anchors, marker, kwTitle);
        if (i >= 0) {
          const a = anchors[i];
          a.scrollIntoView({block: 'center', inline: 'center'});
          await new Promise(r => setTimeout(r, 250));
          // ⚠ 2026-10-09 关键顺序：**先量坐标、后改 href**。
          // setAttribute 修改 href 可能触发 React 重渲染并替换 DOM 节点；若先改后量，
          // 量到的是"已脱离文档"的旧节点 → rect 全 0 → 鼠标事件被派发到 (0,0)，
          // 等于点在页面左上角 = 什么都没点到（静默失败）。
          const r = a.getBoundingClientRect();
          const x = Math.round(r.left + r.width / 2);
          const y = Math.round(r.top + r.height / 2);
          const idx2 = [...document.querySelectorAll(SEL)].indexOf(a);
          lastIdx = idx2 >= 0 ? idx2 : i;
          if (a.isConnected && r.width > 0 && r.height > 0 && (x > 0 || y > 0)) {
            const fixed = fixHref(a);       // 量完坐标再改 href
            return JSON.stringify({idx: lastIdx, x: x, y: y, fixed: fixed || ''});
          }
          // ⚠ 2026-10-29：尺寸为 0 多为「卡片尚未完成布局」，先滚动重试，不要立刻放弃。
          window.scrollBy(0, 120);
          sawZero = true;
          await new Promise(r2 => setTimeout(r2, 600));
          continue;
        }
        window.scrollBy(0, 500);  // 触发懒加载
        await new Promise(r => setTimeout(r, 800));
      }
      // 尺寸始终为 0 → 带上索引返回，让调用方用「不需要坐标」的 JS 点击兜底。
      // 同时附上当时的锚点快照（索引/尺寸/q/文本），便于事后定位"为什么选错/为什么 0 尺寸"。
      const dump = sawZero ? JSON.stringify([...document.querySelectorAll(SEL)].map((e, i) => {
        const r = e.getBoundingClientRect();
        let q = '';
        try { q = new URL(e.getAttribute('href'), location.origin).searchParams.get('q') || ''; } catch (err) {}
        return {i: i, w: Math.round(r.width), h: Math.round(r.height),
                q: decodeURIComponent(q).slice(0, 14),
                t: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 12)};
      })) : '';
      return sawZero ? ('BAD_RECT|' + lastIdx + '|' + dump) : ('NO_ANCHOR:' + marker);
    })()"""
    js = (js.replace("__MARKER__", json.dumps(marker))
            .replace("__KW__", json.dumps(kw_title))
            .replace("__FIXJS__", fix_href_js)
            .replace("__TIMEOUT__", str(timeout)))
    try:
        r = cdp_js(ws, js, timeout=timeout + 10, await_promise=True)
    except Exception as e:
        # ⚠ 2026-10-09 实锤：rewards.bing.com 不可达时，页面里的**同步 XHR 会挂住渲染进程**，
        # 于是 Runtime.evaluate 永远不返回 → TimeoutError。这一条曾把整轮补做直接打断，
        # 后两个任务连点都没点。此处降级为"跳过该任务"，不让单个超时毁掉整轮。
        print(f"  ! 定位卡片失败/超时（{type(e).__name__}）——疑似页面卡住（网络不通），跳过该任务")
        return "CDP_ERR"

    if not (isinstance(r, str) and r.startswith("{")):
        return r if r else "NO_ANCHOR:" + marker  # NO_ANCHOR:... 原样返回

    info = json.loads(r)
    tag = None
    try:
        trusted_click(ws, info["x"], info["y"])
        tag = "TRUSTED_CLICK"
    except Exception as e:
        print(f"  ! CDP 真实鼠标事件失败（{e}），回退 el.click()")
    if tag is None:
        # 回退：非受信任 el.click()（对部分卡片可能不生效）
        js_click = """(() => {
          const a = [...document.querySelectorAll('a[target=_blank][href*="bing.com"]')][__IDX__];
          if (!a) return 'NO_ANCHOR2';
          a.click(); return 'JS_CLICK';
        })()""".replace("__IDX__", str(info["idx"]))
        try:
            tag = cdp_js(ws, js_click, timeout=15) or "NO_ANCHOR2"
        except Exception as e:
            print(f"  ! 回退点击失败（{type(e).__name__}）")
            tag = "CDP_ERR"
    if info.get("fixed"):
        return f"{tag}|{info['fixed']}"
    return tag


def _try_ws(tab):
    try:
        import websocket
        return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)
    except Exception:
        return None


def wait_for_new_tab(before_ids, max_wait=15):
    """点击后等待新开的搜索 tab，返回 (ws|None, tab_id|None)。

    ⚠ 2026-10-09 修正（重要）：旧版把"能建立 CDP websocket"当作"找到了 tab"的前提，
    而连接一失败就 `continue` **重扫同一份列表** → 轮次被空转耗尽，返回 (None, None)，
    日志表现为「点了但没捕获到新 tab」。
    A/B 实测证明这是**检测端的假否**：同一张卡的三种点击方式（转义 href+真点 / 原始 href+真点 /
    转义 href+el.click()）**都能打开 /search 页**。
    现改为：只看 tab 列表（优先 url 含 /search），websocket 仅作"附加产物"（失败就返回 None，
    调用方本来也只是立刻 close 它）。
    """
    fallback = None
    for _ in range(max_wait):
        for t in list_tabs():
            if t["id"] in before_ids:
                continue
            if "/search" in (t.get("url") or ""):
                return _try_ws(t), t["id"]
            if fallback is None:
                fallback = t          # 还没拿到 url 的新 tab，先记下
        time.sleep(1)
    if fallback:
        return _try_ws(fallback), fallback["id"]
    return None, None


def do_one_task(ws, i, total, t):
    """处理单个 Daily Set 任务。
    2026-10-09：整体包 try/except —— 网络不通时页面会卡住、CDP 调用会超时，
    旧版一次 TimeoutError 就把整轮补做打断（后面任务连点都没点）。现在单任务失败只跳过它。"""
    try:
        title = t["title"]
        print(f"\n[{i}/{total}] {title}  type={t['type']}")
        # 提示（**不跳过**）：链接缺常见归属参数时，若本轮不计分多半是微软侧数据异常，
        # 而不是我们点得不对 —— 2026-09-14 实测：这类活动手动点仍可完成，故照常点击。
        if not offer_attributed(t.get("destination", "")):
            print("  ℹ 该链接未带常见奖励归属参数（BTEPOKey / PUBL=RewardsDO）——仅供参考，非故障判据")
        # 2026-10-09：实测「点了但没捕获到新 tab」会白等 90s 且必然不计分
        #（当日第 1 个任务成功、后两个都栽在这），故失败后再补点一次。
        fixed_url = ""
        nws = ntab = None
        for attempt in (1, 2):
            if attempt == 2:
                # ⚠ 2026-11-09：第一次失败常见原因是**页面假死**（同步 XHR 挂住渲染进程
                # → rect 一直 0 / Runtime.evaluate 超时）。在卡死的页面上原地重试往往没用，
                # 先重新加载 dashboard 再试，实测能恢复（当日「多彩的适应」重跑一次即点中）。
                try:
                    goto_dashboard(ws, min_wait=8, card_timeout=20)
                except Exception as e:
                    print(f"  ! 重试前刷新页面失败（{type(e).__name__}）")
                time.sleep(2)
            before = {x["id"] for x in list_tabs()}
            r = find_task_anchor(ws, title, t.get("destination", ""))
            print(f"  click{'' if attempt == 1 else '(重试)'}: {str(r)[:170]}")
            if str(r).startswith("NO_ANCHOR"):
                print("  ! 找不到卡片锚点，跳过")
                return
            if str(r).startswith("CDP_ERR"):
                print("  ! 页面/通道异常，跳过该任务")
                return
            if str(r).startswith("BAD_RECT"):
                # 尺寸恒为 0（页面布局异常）→ 坐标法失效，改用**不需要坐标的 JS 点击**兜底。
                # 2026-11-10 起：不再"跳过该任务"（那会导致 0/3），而是照点。
                parts = str(r).split("|", 2)
                idx = parts[1].strip() if len(parts) > 1 else ""
                if len(parts) > 2 and parts[2].strip():
                    print(f"  现场锚点快照: {parts[2][:700]}")
                print(f"  ↻ 卡片尺寸为 0，坐标法失效 → 回退 JS 点击（idx={idx}）")
                if idx.lstrip("-").isdigit():
                    try:
                        r = js_click_index(ws, idx)
                    except Exception as e:
                        r = f"CDP_ERR:{type(e).__name__}"
                else:
                    r = "BAD_RECT"
                print(f"  click(回退): {str(r)[:80]}")
                if str(r).startswith(("NO_ANCHOR", "CDP_ERR", "BAD_RECT")):
                    if attempt == 2:
                        print("  ! 回退点击也失败，跳过该任务")
                        return
                    time.sleep(2)
                    continue
            # 若卡片 href 是畸形 URL（裸引号），find_task_anchor 会返回转义后的 URL 供兜底
            elif "|" in str(r):
                fixed_url = str(r).split("|", 1)[1].strip()
            nws, ntab = wait_for_new_tab(before)
            if ntab:
                break
            if attempt == 1:
                print("  ! 未捕获新 tab，3s 后重试点击...")
                time.sleep(3)
        if not ntab:
            print("  ! 两次点击都未捕获新 tab，等待补偿")
            time.sleep(WAIT_PER_TASK)
        tab_url = ""
        if ntab:
            if nws:
                nws.close()  # 只断开 CDP 连接，保留浏览器 tab 让 tracking 跑完
            # ⚠ 延迟轮询：/json/list 里刚出现的 tab，url 字段常常还是空串（导航未提交）。
            # 旧版立刻读 → 日志永远打印 "new tab opened: 空"，是**假信号**，会误导排查。
            for _ in range(6):  # 最多 12s
                time.sleep(2)
                cur = next((x for x in list_tabs() if x["id"] == ntab), None)
                tab_url = (cur or {}).get("url") or ""
                if "/search" in tab_url:
                    break
            print(f"  new tab: {tab_url[:120] if tab_url else '(未加载 /search)'}")
            print("  保留新 tab，回 dashboard 轮询计分（最长 90s）...")
        # 兜底（2026-09-14）：畸形 href 被转义后，若新 tab 仍未真正加载搜索页
        #（URL 为空 / about:blank / 不在 /search），直接用转义后的 URL 新开一个 tab。
        if fixed_url and ("/search" not in tab_url):
            print(f"  ↻ 新 tab 未加载搜索页，改用转义 URL 直开: {fixed_url[:110]}")
            cdp_new_page(fixed_url)
            time.sleep(WAIT_PER_TASK)
        # 回 dashboard 并轮询该任务是否完成
        try:
            cdp_nav(ws, "https://rewards.bing.com/dashboard", 8)
        except Exception as e:
            print(f"  ! 回 dashboard 失败（{type(e).__name__}）")
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
            print("  ⏳ 90s 内未观察到 complete=true（可能是网络/计分延迟，也可能是任务今日确实不计奖励）")
        if ntab:
            close_tab(ntab)
    except Exception as e:
        print(f"  ! 该任务处理异常（{type(e).__name__}: {e}），继续下一个")


def main():
    if not ensure_edge():
        print("[x] CDP unreachable")
        return 1
    ws = get_ws()
    # ⚠ 2026-11-11 顺序很重要：**先读 API，再等卡片布局**。
    # 卡片的标题/目的地/状态全部来自 getuserinfo 这个接口；接口不通时页面**永远渲染不出卡片**
    #（实测就绪锚点恒为 2、其余 rect 为 0），傻等 40s ×2 次重载纯属浪费。
    # 所以先用短等待把页面带到 rewards 域，立刻读 API；不通就立刻返回 2，不再等布局。
    cdp_nav(ws, "https://rewards.bing.com/dashboard", 8)
    tasks = read_tasks(ws)
    if tasks is None:
        print("  ⚠ 无法读取 Daily Set 任务列表（rewards API 不可达）")
        print("     卡片数据来自该接口 —— 取不到就渲染不出来，故未执行任何点击")
        print("     按【未完成】处理；网络恢复后重跑即可（通常几小时后积分也会延迟到账）")
        ws.close()
        return 2

    # API 正常 → 再等卡片真正布局就绪（固定 sleep 不可靠，见 wait_cards_ready 注释）
    goto_dashboard(ws, min_wait=6, card_timeout=40)

    s0 = read_state(ws)
    print("baseline :", json.dumps(s0, ensure_ascii=False) if s0 else "n/a")

    print(f"任务列表 : {len(tasks)} 个（已完成 {sum(1 for t in tasks if t.get('complete'))} 个）")
    if not tasks:
        print("  ⚠ API 正常但今日任务列表为空（可能尚未发布或日期键不匹配），无需操作")
        ws.close()
        return 0
    todo = [t for t in tasks if not t["complete"] and t.get("destination")]
    print(f"todo     : {len(todo)} 个未完成任务")
    if not todo:
        print("今天的 Daily Set 已全部完成（或剩余任务无 destination），无需操作")
        ws.close()
        return 0

    for i, t in enumerate(todo, 1):
        do_one_task(ws, i, len(todo), t)

    print(f"\n== waiting {WAIT_SETTLE}s for credits to land ==")
    time.sleep(WAIT_SETTLE)
    try:
        cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)  # 回 rewards 域，避免 XHR 跨域失败
    except Exception as e:
        print(f"  ! 最终回 dashboard 失败（{type(e).__name__}）")
    s1 = read_state(ws)
    print("after    :", json.dumps(s1, ensure_ascii=False) if s1 else "n/a")
    if s0 and s1:
        print("\n== diff ==")
        print(f"  points: {s0.get('points')} -> {s1.get('points')}")
        for a, b in zip(s0.get("tasks") or [], s1.get("tasks") or []):
            mark = "✅" if b.get("complete") else "❌"
            print(f"  {mark} {b.get('title')}: {a.get('complete')} -> {b.get('complete')}")
    ws.close()

    # --- 结束自检：必须真实读到状态才能判定"成功" ---
    if s1 is None:
        print("\nUNVERIFIED  Daily Set 状态未验证（rewards API 不可达）——不得视为完成，请重跑")
        return 2
    done_n = s1.get("dailySetDone")
    total_n = s1.get("dailySetTotal") or 3
    if done_n is None:
        done_n = sum(1 for t in (s1.get("tasks") or []) if t.get("complete"))
    if done_n >= 3:
        print(f"\nDONE  ✅ Daily Set {done_n}/{total_n} 全部完成")
        return 0
    print(f"\nINCOMPLETE  ⚠ Daily Set 仅 {done_n}/{total_n} 完成 —— 稍后可重跑本脚本补做")
    return 4

if __name__ == "__main__":
    sys.exit(main())