#!/usr/bin/env python3
"""批量精简 Folo 订阅。

用法: python3 prune_subscriptions.py <accounts.db>
环境变量 PRUNE_MODE: dry(默认,只打印将要退订的源) / apply(实际执行退订)

两个动作:
1. 退订 prune-cutlist.json 中列出的非安全类源(按 feed URL 匹配)
2. 同一账号内 URL 重复的订阅只保留一个(如 delivr.to 的多个 gi= 变体)
"""
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("FOLO_API_BASE", "https://api.folo.is")
DRY_RUN = os.environ.get("PRUNE_MODE", "dry") != "apply"
CUTLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prune-cutlist.json")


def norm(u):
    """归一化 URL 用于匹配: 小写、http->https、rsshub scheme 展开、去掉捕获用参数。"""
    u = (u or "").strip().lower()
    if u.startswith("rsshub://"):
        u = "https://rsshub.app/" + u[len("rsshub://"):]
    elif u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    if "?" in u:
        base, q = u.split("?", 1)
        keep = [p for p in q.split("&") if p and p.split("=", 1)[0] != "gi"]
        u = base + ("?" + "&".join(keep) if keep else "")
    return u.rstrip("/")


def api(token, method, path, body=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(API_BASE + path, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            req.add_header("Origin", "https://app.follow.is")
            req.add_header("Referer", "https://app.follow.is/")
            req.add_header("Cookie", "__Secure-better-auth.session_token=" + token)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read(300).decode(errors="replace")
            last = RuntimeError("HTTP %d: %s" % (e.code, detail))
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise last
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last


def main():
    accounts_db = sys.argv[1] if len(sys.argv) > 1 else "data/data/accounts.db"
    with open(CUTLIST) as fp:
        cut_urls = set(json.load(fp)["cut_urls"])

    db = sqlite3.connect(accounts_db)
    accounts = db.execute(
        "select name, session_token from accounts "
        "where status='active' and coalesce(session_token,'') != ''"
    ).fetchall()
    db.close()
    print("accounts: %d, mode: %s, cut urls: %d" % (len(accounts), "APPLY" if not DRY_RUN else "dry", len(cut_urls)))

    total = 0
    failed = 0
    for name, token in accounts:
        try:
            resp = api(token, "GET", "/subscriptions")
        except Exception as e:  # noqa: BLE001
            print("[%s] ERROR get subscriptions: %s" % (name, e))
            failed += 1
            continue
        items = resp.get("data") or []
        seen = {}
        actions = []
        for it in items:
            feed = it.get("feeds") or {}
            url = feed.get("url") or ""
            n = norm(url)
            if n.startswith("folo://"):
                continue
            reason = None
            if n in cut_urls:
                reason = "cut"
            elif n in seen:
                reason = "dup:" + seen[n]
            else:
                seen[n] = url
            if reason:
                actions.append((it["feedId"], url, reason))
        print("[%s] subscriptions=%d to_remove=%d" % (name, len(items), len(actions)))
        for _fid, url, reason in actions:
            print("  - %-4s %s" % (reason, url))
        if DRY_RUN or not actions:
            continue
        for fid, url, _reason in actions:
            try:
                r = api(token, "DELETE", "/subscriptions", {"feedIdList": [fid]})
            except Exception as e:  # noqa: BLE001
                print("  x FAIL %s: %s" % (url, e))
                failed += 1
                time.sleep(1)
                continue
            if r.get("code") == 0:
                total += 1
                print("  x OK   %s" % url)
            else:
                failed += 1
                print("  x FAIL %s: %s" % (url, json.dumps(r, ensure_ascii=False)))
            time.sleep(0.3)
        time.sleep(1)

    print("done: deleted=%d failed=%d dry_run=%s" % (total, failed, DRY_RUN))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
