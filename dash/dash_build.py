import json
import sys
import time
from pathlib import Path

REPO = "smaction04/threads-runner"
BASE = Path(__file__).parent / "dash_base.json"
OUT = Path(__file__).parent / "dash_posts.json"
KST = 9 * 3600


def gh_file(name):
    return open(name, encoding="utf-8").read()


def kst(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + KST))


def utc_to_ts(s):
    return int(time.mktime(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S")) - time.timezone)


def main():
    rows = json.loads(BASE.read_text(encoding="utf-8"))
    q = json.loads(gh_file("queue.json"))["items"]
    stats = json.loads(gh_file("stats.json"))
    log = [json.loads(l) for l in gh_file("log.jsonl").splitlines() if l.strip()]
    by_sched = {r["sched"]: r for r in rows}
    post_t, post_url = {}, {}
    for e in log:
        if e.get("step") == "post":
            post_t[e["id"]] = utc_to_ts(e["t"].rstrip("Z"))
        if e.get("step") == "verify" and e.get("url"):
            post_url[e["id"]] = e["url"]
    miss = []
    for it in q:
        row = by_sched.get(kst(it["at"]))
        if not row:
            miss.append(it["id"])
            continue
        if not it.get("post_id"):
            continue
        st = (stats["posts"].get(str(it["id"])) or {}).get("latest") or {}
        t = post_t.get(it["id"], it["at"])
        row.update(status="발행 완료", when=kst(t), hour=int(kst(t)[11:13]), lag_min=max(0, round((t - it["at"]) / 60)),
                   verify="원본과 일치" if it.get("verified") == "ok" else str(it.get("verified") or "확인 필요"),
                   url=post_url.get(it["id"], row.get("url", "")))
        for k in ("views", "likes", "replies", "reposts"):
            if k in st:
                row[k] = st[k]
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("updated", stats.get("updated"), "published", sum(r["status"] == "발행 완료" for r in rows), "unmatched", miss)


main()
