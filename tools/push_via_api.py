# -*- coding: utf-8 -*-
"""push_via_api.py — github.com 被墙(git push 不通)时，用 GitHub REST(Git Data API) 推送。

场景：本机 github.com:443 遭 SNI/TLS 干扰，git push / SSH 均不通；但 api.github.com 畅通。
做法：先找出远端分支缺失的本地提交链，用 REST 逐个重建 blob/tree/commit（保持每个
      commit 的 SHA 与本地一致），最后把远端 ref 移到本地 HEAD → 本地远端完全同步，
      无需 force push（下次 github.com 通了也能正常 git push）。

用法:
  python tools/push_via_api.py --dry-run     # 只重建并校验 SHA，不更新远端 ref
  python tools/push_via_api.py               # 正式推送
"""
import argparse, base64, json, re, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

API = "https://api.github.com"
PAT_FALLBACK = Path.home() / ".workbuddy" / "credentials" / "github-pat.txt"
S = requests.Session()
S.trust_env = False  # 绕过系统代理直连 api.github.com


def git(*a, binary=False):
    r = subprocess.run(["git"] + list(a), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)} 失败: {r.stderr.decode('utf-8','replace')[:300]}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def api(method, url, token, **kw):
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    kw.setdefault("headers", h)
    r = S.request(method, url, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {method} {url}\n{r.text[:400]}")
    return r.json() if r.content else None


def parse_ident(s):
    """'Name <email> 1234567890 +0800' -> {name,email,date(ISO8601)}"""
    m = re.match(r"^(.*?) <(.*?)> (\d+) ([+-]\d{4})$", s)
    if not m:
        raise RuntimeError(f"无法解析 ident: {s!r}")
    name, email, ts, tz = m.groups()
    sign = 1 if tz[0] == "+" else -1
    off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    iso = datetime.fromtimestamp(int(ts), timezone(off)).isoformat()
    return {"name": name, "email": email, "date": iso}


def commit_meta(sha):
    """读本地 commit 原始元数据"""
    raw = git("cat-file", "commit", sha, binary=True).decode("utf-8", "replace")
    head, _, message = raw.partition("\n\n")
    tree = None; parents = []; author = committer = None
    for ln in head.split("\n"):
        if ln.startswith("tree "): tree = ln[5:]
        elif ln.startswith("parent "): parents.append(ln[7:])
        elif ln.startswith("author "): author = ln[7:]
        elif ln.startswith("committer "): committer = ln[10:]
    return dict(tree=tree, parents=parents, author=author, committer=committer, message=message)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="866666/bing-rewards-auto")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not PAT_FALLBACK.exists():
        raise SystemExit(f"缺 token: {PAT_FALLBACK}")
    token = PAT_FALLBACK.read_text(encoding="utf-8").strip()
    owner, repo = a.repo.split("/")

    me = api("GET", f"{API}/user", token)
    print(f"认证: {me.get('login')}")

    head = git("rev-parse", "HEAD").strip()
    ref = api("GET", f"{API}/repos/{owner}/{repo}/git/ref/heads/{a.branch}", token)
    base = ref["object"]["sha"]
    print(f"远端 {a.branch} @ {base[:10]}  |  本地 HEAD @ {head[:10]}")
    if base == head:
        print("已同步，无需推送"); return

    # 远端 base 必须是本地 HEAD 的祖先（本地领先）
    try:
        git("merge-base", "--is-ancestor", base, head)
    except Exception:
        raise SystemExit("远端 base 不是本地 HEAD 祖先，非快进场景，请人工处理")

    commits = [c for c in git("rev-list", "--reverse", f"{base}..{head}").split() if c]
    print(f"待推送提交 {len(commits)} 个（从旧到新）")

    prev = base
    prev_tree = api("GET", f"{API}/repos/{owner}/{repo}/git/commits/{prev}", token)["tree"]["sha"]
    ok = True
    for c in commits:
        meta = commit_meta(c)
        # 相对前一提交的变更
        out = git("diff", "--name-status", prev, c)
        items = []
        for ln in out.splitlines():
            if not ln.strip(): continue
            parts = ln.split("\t")
            st, path = parts[0][0], parts[-1]
            if st == "D":
                items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            mode = git("ls-tree", c, path).split()[0]
            blob_bytes = git("cat-file", "blob", f"{c}:{path}", binary=True)
            blob = api("POST", f"{API}/repos/{owner}/{repo}/git/blobs", token,
                       json={"content": base64.b64encode(blob_bytes).decode(), "encoding": "base64"})
            items.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})

        tree = api("POST", f"{API}/repos/{owner}/{repo}/git/trees", token,
                   json={"base_tree": prev_tree, "tree": items})
        tree_ok = (tree["sha"] == meta["tree"])
        commit = api("POST", f"{API}/repos/{owner}/{repo}/git/commits", token,
                     json={"message": meta["message"], "tree": tree["sha"],
                           "parents": [prev],
                           "author": parse_ident(meta["author"]),
                           "committer": parse_ident(meta["committer"])})
        sha_ok = (commit["sha"] == c)
        ok = ok and tree_ok and sha_ok
        print(f"  {c[:10]}  tree={'✓' if tree_ok else '✗('+tree['sha'][:10]+'≠'+meta['tree'][:10]+')'}"
              f"  commit={'✓' if sha_ok else '✗('+commit['sha'][:10]+')'}"
              f"  [{len(items)} 文件]  {meta['message'].splitlines()[0][:48] if meta['message'] else ''}")
        prev, prev_tree = c, meta["tree"]

    if not ok:
        print("\n⚠ 存在 SHA 不一致：重建 commit 与本地 SHA 不同（内容一致但历史会分叉）")
        print("  仍可继续（远端将获得等价内容）；但本地远端会分叉，后续需 force push 或 reset 对齐")
        if not a.dry_run:
            print("  （已中止，未更新 ref）")
        return 1 if not a.dry_run else 0

    print("\n✅ 全部 SHA 校验一致")
    if a.dry_run:
        print("（dry-run：未更新远端 ref）"); return 0
    api("PATCH", f"{API}/repos/{owner}/{repo}/git/refs/heads/{a.branch}", token,
        json={"sha": head, "force": False})
    print(f"✅ 已推送 {a.branch} -> {head[:10]}（{len(commits)} 提交，历史完整保留）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
