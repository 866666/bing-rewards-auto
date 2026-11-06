# -*- coding: utf-8 -*-
r"""bootstrap.py —— 自愈式启动器：确保 .venv 可用，再运行 rewards_daily.py

为什么需要它（两次真实事故）：
  1) 2026-10-08：WorkBuddy 更新把 `~/.workbuddy/binaries/python/versions/3.13.12`
     改成指向内置 vendor python 的符号链接，并清空第三方包 → 每天静默失败 6 天。
  2) 2026-11-02：系统 Python 从 3.14 升级到 3.15，`C:\Program Files\Python314` 被卸载，
     而 .venv 是基于 3.14 建的 —— 基座消失，`pythonw.exe` 根本起不来，
     **连日志都写不出一行**（11-03~11-06 连续 4 天完全静默）。

  本质：任何"依赖某个系统 Python 版本"的长期任务，都会在版本升级时猝死一次。
  对策：任务别直接指向 venv 的解释器，而是指向本脚本（由版本无关的 `pyw.exe -3` 启动）。
  它先自检 venv，坏了就用**当前可用的任意 Python**重建并装依赖，然后再拉主脚本。

用法（计划任务 Action）：
  C:\Windows\pyw.exe   -3  "C:\Users\shang\WorkBuddy\bing-rewards-auto\tools\bootstrap.py"

可选参数：
  --check-only   只做健康检查并写日志，不运行主脚本（排查用）
  其余参数原样透传给 rewards_daily.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VENV = BASE_DIR / ".venv"
VPY = VENV / "Scripts" / "python.exe"
VPW = VENV / "Scripts" / "pythonw.exe"
MAIN = BASE_DIR / "rewards_daily.py"
LOG = BASE_DIR / "logs" / "bootstrap.log"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEPS = ["requests", "websocket-client"]

# pyw 下 sys.stdout 为 None，只能写文件
LOG.parent.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd, timeout=300):
    """静默执行，返回 (returncode, 合并输出)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=str(BASE_DIR))
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def venv_ok():
    if not (VPY.exists() and VPW.exists()):
        return False, "venv 解释器文件缺失"
    rc, out = run([str(VPY), "-c", "import requests, websocket; print('ok')"], timeout=60)
    if rc == 0:
        return True, "ok"
    return False, out[-400:] or f"rc={rc}"


def rebuild_venv():
    """用当前正在运行的 Python 重建 venv 并安装依赖"""
    log(f"重建 venv，使用解释器: {sys.executable}")
    rc, out = run([sys.executable, "-m", "venv", "--clear", str(VENV)], timeout=300)
    log(f"  venv 创建 rc={rc} {out[-300:]}")
    if rc != 0:
        return False
    for extra in ([], ["--upgrade", "pip"]):
        cmd = [str(VPY), "-m", "pip", "install", "-q"] + extra + (["-i", MIRROR] if extra else [])
        if extra:
            run(cmd, timeout=300)
    rc, out = run([str(VPY), "-m", "pip", "install", "-q"] + DEPS + ["-i", MIRROR], timeout=600)
    log(f"  安装 {DEPS} rc={rc} {out[-300:]}")
    return rc == 0


def main():
    args = [a for a in sys.argv[1:] if a != "--check-only"]
    check_only = "--check-only" in sys.argv
    log("=" * 50)
    log(f"bootstrap 启动 | 宿主解释器 {sys.executable} | py {sys.version.split()[0]}")

    ok, why = venv_ok()
    log(f"venv 自检: {'OK' if ok else 'FAIL - ' + why}")

    if not ok:
        if not rebuild_venv():
            log("❌ venv 重建失败，放弃本次运行（请手动检查网络 / Python 安装）")
            return 3
        ok, why = venv_ok()
        log(f"重建后自检: {'OK' if ok else 'FAIL - ' + why}")
        if not ok:
            log("❌ 重建后仍不可用，放弃")
            return 3

    if check_only:
        log("--check-only：健康检查结束，未运行主脚本")
        return 0

    log("拉起 rewards_daily.py")
    rc, out = run([str(VPW), str(MAIN)] + args, timeout=4 * 3600)
    log(f"主脚本退出码 {rc}")
    if rc != 0:
        log("主脚本输出尾部:\n" + (out[-1500:] or "(空)"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
