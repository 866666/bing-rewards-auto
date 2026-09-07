# -*- coding: utf-8 -*-
"""检查任务触发后的进程与日志状态"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import psutil
    names = []
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if "wscript" in n or "pythonw" in n:
            try:
                names.append((p.pid, n, " ".join(p.cmdline())[:150]))
            except Exception:
                pass
    if names:
        for pid, n, cl in names:
            print(pid, n, cl)
    else:
        print("无 wscript/pythonw 进程")
except ImportError:
    print("(psutil 不可用，跳过进程检查)")

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rewards_daily_log.txt")
mt = os.path.getmtime(LOG)
print("\nlog mtime:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mt)))
print("now      :", time.strftime("%Y-%m-%d %H:%M:%S"))
