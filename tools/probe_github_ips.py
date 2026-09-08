# -*- coding: utf-8 -*-
"""probe_github_ips.py - 探测 GitHub 各域名当前可达的最优 IP（TCP 443 连通+时延）
用法: python tools/probe_github_ips.py
输出: 按域名给出建议 hosts 条目
"""
import sys, socket, time
from concurrent.futures import ThreadPoolExecutor
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 候选 IP 池（GitHub 官方与 Fastly CDN；140.82.* = GitHub 自家 anycast VIP）
CANDIDATES = {
    "github.com": [
        "140.82.112.3", "140.82.113.3", "140.82.114.3", "140.82.116.3",
        "140.82.121.3", "140.82.112.4", "140.82.113.4", "140.82.114.4",
        "140.82.116.4", "140.82.121.4", "140.82.112.5", "140.82.113.5",
        "140.82.114.5", "140.82.116.5", "140.82.121.5", "140.82.112.6",
        "140.82.113.6", "140.82.114.6", "140.82.116.6", "140.82.121.6",
        "20.205.243.166", "20.205.243.168", "20.27.177.113", "20.200.245.247",
        "20.248.136.48", "20.248.137.48", "20.248.138.48", "20.248.139.48",
        "185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133",
        "192.0.66.2", "192.0.66.10",
    ],
    "api.github.com": [
        "140.82.112.6", "140.82.113.6", "140.82.114.6", "140.82.116.6",
        "140.82.121.6", "140.82.112.5", "140.82.113.5", "140.82.114.5",
        "140.82.112.3", "140.82.113.3", "140.82.114.3", "140.82.116.3",
        "140.82.121.3", "20.205.243.168", "20.27.177.113", "20.200.245.247",
        "20.248.136.48", "20.248.137.48", "192.0.66.2", "192.0.66.10",
    ],
    "codeload.github.com": [
        "140.82.112.10", "140.82.113.10", "140.82.114.10", "140.82.116.10",
        "140.82.121.10", "20.205.243.165", "20.27.177.113", "20.200.245.247",
        "20.248.136.48", "20.248.137.48", "20.248.138.48", "20.248.139.48",
        "185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133",
    ],
    "objects.githubusercontent.com": [
        "185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133",
    ],
}

def probe(host, port=443, timeout=4):
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.time() - t0) * 1000)
    except Exception:
        return None

for domain, ips in CANDIDATES.items():
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(lambda ip: (ip, probe(ip)), ips))
    ok = [(ip, ms) for ip, ms in results if ms is not None]
    ok.sort(key=lambda x: x[1])
    print(f"=== {domain} （连通 {len(ok)}/{len(ips)}）===")
    for ip, ms in ok[:6]:
        print(f"   {ms:5d} ms  {ip}")
    if ok:
        print(f"   → 建议: {ok[0][0]}  {domain}")
    print()