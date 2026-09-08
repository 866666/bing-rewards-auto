# -*- coding: utf-8 -*-
"""gh_proxy.py - 本地 HTTP CONNECT 代理，自动轮换 GitHub 多 IP 直连（免代理/免改 hosts 高频操作）

背景：GitHub APAC/美洲 VIP 被间歇性 TLS 干扰（同一 IP 秒级 000/200 随机）。
方案：CONNECT 代理只在 TCP 层透传（非 MITM，TLS 端到端由客户端完成），
      每次建连时自动尝试多个已知 VIP，选最早可用的转发，规避单 IP 抖动。

用法:
  python tools/gh_proxy.py [端口]      # 默认 127.0.0.1:7898
  HTTPS_PROXY=http://127.0.0.1:7898 gh auth login --web ...
  HTTPS_PROXY=http://127.0.0.1:7898 git push ...
"""
import socket, threading, sys, random, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7898
LOG_TAG = time.strftime("%H:%M:%S")

# GitHub 已知 VIP（web/api/codeload 覆盖；顺序会随机化）
GITHUB_IPS = [
    "140.82.112.3", "140.82.113.3", "140.82.114.3", "140.82.116.3", "140.82.121.3",
    "140.82.112.6", "140.82.113.6", "140.82.114.6", "140.82.116.6", "140.82.112.4",
    "20.205.243.166", "20.205.243.168", "20.205.243.165", "20.27.177.113",
    "20.200.245.247", "185.199.108.133", "185.199.109.133",
]
GITHUB_DOMAINS = ("github.com", "api.github.com", "codeload.github.com",
                  "objects.githubusercontent.com", "raw.githubusercontent.com",
                  "gist.github.com", "avatars.githubusercontent.com",
                  "github.githubassets.com", "assets-cdn.github.com")


def pick_github_ip(host):
    """并行探测候选 VIP：先 TCP 粗筛，再真实 TLS 握手（带 SNI）精筛，返回 TLS 通的第一个 IP"""
    import ssl, concurrent.futures
    tcp_ok = []

    def tcp_check(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.5)
            s.connect((ip, 443))
            s.close()
            return True
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(tcp_check, GITHUB_IPS))
    tcp_ok = [ip for ip, ok in zip(GITHUB_IPS, results) if ok]
    if not tcp_ok:
        return None

    ctx = ssl.create_default_context()

    def tls_ok(ip):
        try:
            s = socket.create_connection((ip, 443), timeout=4)
            try:
                t = ctx.wrap_socket(s, server_hostname=host)  # 真实 TLS 握手（SNI=host）
                t.close()
                return True
            except Exception:
                s.close()
                return False
        except Exception:
            return False

    # 随机起点轮换，避免连续撞同一个抖动 IP
    order = tcp_ok[:]
    start = random.randrange(len(order))
    order = order[start:] + order[:start]
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        ok_map = {ip: r for ip, r in zip(order, ex.map(tls_ok, order))}
    for ip in order:
        if ok_map.get(ip):
            return ip
    return None


def pipe(src, dst, tag):
    """单向搬运数据直到 EOF/异常"""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle(client, addr):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            req += chunk
        first = req.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        parts = first.split()
        if len(parts) < 2 or parts[0] != "CONNECT":
            client.close()
            return
        host, _, port_s = parts[1].partition(":")
        port = int(port_s)
        if host in GITHUB_DOMAINS:
            ip = pick_github_ip(host)
            if not ip:
                print(f"[{LOG_TAG}] {host}: 所有候选 IP 均不可达", flush=True)
                try:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                except Exception:
                    pass
                client.close()
                return
            print(f"[{LOG_TAG}] {addr[0]} -> {host} via {ip}", flush=True)
        else:
            ip = host
        up = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        up.settimeout(10)
        up.connect((ip, port))
        up.settimeout(None)
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        t1 = threading.Thread(target=pipe, args=(client, up, host), daemon=True)
        t2 = threading.Thread(target=pipe, args=(up, client, host), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()
    except Exception as e:
        print(f"[{LOG_TAG}] {host}: {e}", flush=True)
    finally:
        try:
            client.close()
        except Exception:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LISTEN_PORT))
    srv.listen(50)
    print(f"[{LOG_TAG}] GitHub CONNECT proxy listening 127.0.0.1:{LISTEN_PORT}", flush=True)
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()


if __name__ == "__main__":
    main()