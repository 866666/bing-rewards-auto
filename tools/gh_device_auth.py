# -*- coding: utf-8 -*-
"""gh_device_auth.py - 手动走完 GitHub device flow（走本地多IP轮换代理，带重试）
用法: python tools/gh_device_auth.py
流程: 拿 device_code → 打印 user_code 请在浏览器授权 → 轮询 access_token（网络失败自动重试）
产出: 成功后写入 .gh_token.tmp，并打印 token 供 gh auth login --with-token
"""
import sys, json, time, urllib.request, urllib.parse
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROXY = "http://127.0.0.1:17898"
CLIENT_ID = "178c6fc778ccc68e1d6a"  # GitHub CLI 官方 OAuth App
AUTH_BASE = "https://github.com/login"


def build_opener():
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    )
    op.addheaders = [("User-Agent", "gh/2.86.0"), ("Accept", "application/json")]
    return op


def post(op, url, data, tries=6):
    body = urllib.parse.urlencode(data).encode()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            with op.open(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode())
        except Exception as e:
            last = e
            print(f"    retry {i+1}/{tries}: {e}", flush=True)
            time.sleep(2)
    return -1, {"error": str(last)}


def main():
    op = build_opener()

    # ===== 阶段1：等网络窗口，申请设备码（自动重试最长 30 分钟）=====
    print("[1] 等待网络窗口，申请设备码（自动重试，最长 30 分钟）...", flush=True)
    t_start = time.time()
    dev_code = user_code = None
    interval = 5
    while time.time() - t_start < 1800:
        st, d = post(op, f"{AUTH_BASE}/device/code",
                     {"client_id": CLIENT_ID, "scope": "repo"}, tries=2)
        if st == 200 and d.get("device_code"):
            dev_code = d["device_code"]
            user_code = d["user_code"]
            interval = int(d.get("interval", 5))
            break
        el = int(time.time() - t_start)
        if el % 120 < 10:
            print(f"    [{el//60} 分钟] 网络窗口未开（{d.get('error', st)}），继续等待...", flush=True)
        time.sleep(8)
    if not dev_code:
        print("  ✗ 30 分钟窗口内未成功，请稍后再试")
        return 1

    print("  ✅ 设备码: " + user_code, flush=True)
    print("  → 请在浏览器打开 https://github.com/login/device 输入 " + user_code + " 并授权", flush=True)
    print("    授权完成后本脚本自动轮询 token（无需操作）...", flush=True)

    # ===== 阶段2：轮询 access_token（网络失败自动重试，最长 10 分钟）=====
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(max(interval, 5))
        st, d = post(op, f"{AUTH_BASE}/oauth/access_token",
                     {"client_id": CLIENT_ID, "device_code": dev_code,
                      "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, tries=3)
        if st == 200 and d.get("access_token"):
            tok = d["access_token"]
            with open("C:/Users/shang/WorkBuddy/bing-rewards-auto/.gh_token.tmp", "w") as f:
                f.write(tok)
            print("\n  ✅ 授权成功！token 已保存到 .gh_token.tmp", flush=True)
            return 0
        if d.get("error") == "authorization_pending":
            continue
        if d.get("error") == "slow_down":
            interval += 5
            continue
        if d.get("error"):
            print(f"    {d}", flush=True)
    print("\n  ✗ 等待授权超时")
    return 2


if __name__ == "__main__":
    sys.exit(main())