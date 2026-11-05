import calendar
import json
import re
import sys
import time
from pathlib import Path

REPO = "smaction04/threads-runner"
BASE = Path(__file__).parent / "dash_base.json"
OUT = Path(__file__).parent / "dash_posts.json"
KST = 9 * 3600
ROOT = Path(__file__).parent.parent


def gh_file(name):
    return open(ROOT / name, encoding="utf-8").read()


def kst(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + KST))


def utc_to_ts(s):
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S"))


def load_products(warns):
    try:
        ps = json.loads(gh_file("products.json"))
        ps = ps if isinstance(ps, list) else ps.get("items", [])
        return [p for p in ps if isinstance(p, dict)]
    except (OSError, ValueError, AttributeError) as e:
        warns.append(f"products.json 못 읽음: {e}")
        return []


def post_fields(it, by_id, by_link):
    t = it.get("text")
    t = t if isinstance(t, str) else ""
    p = None
    for link in re.findall(r"https?://\S+", str(it.get("comment") or "") + " " + t):
        p = by_link.get(link)
        if p:
            break
    p = p or by_id.get(it.get("product"))
    lines = t.splitlines()
    return {"product": str((p or {}).get("name", ""))[:20], "price": (p or {}).get("price"), "style": it.get("style") or "",
            "first_line": lines[0] if lines else "", "chars": len(t)}


def text_changed(row, fields):
    first = row.get("first_line") or ""
    return row.get("chars") != fields["chars"] or not fields["first_line"].startswith(first)


def sync_with_queue(rows, q, warns):
    ps = load_products(warns)
    by_id = {p["id"]: p for p in ps if isinstance(p.get("id"), (int, str))}
    by_link = {p["link"]: p for p in ps if isinstance(p.get("link"), str) and p["link"]}
    qt = {}
    for it in q:
        try:
            s = kst(it["at"])
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            warns.append(f"예약 시각 이상해서 건너뜀: {it.get('id') if isinstance(it, dict) else it!r}")
            continue
        if s in qt:
            warns.append(f"같은 분에 예약 겹침 {s}: {qt[s].get('id')}, {it.get('id')}")
            if not it.get("post_id") or qt[s].get("post_id"):
                continue
        qt[s] = it
    kept, have = [], set()
    for r in rows:
        s = r.get("sched")
        if s in have:
            warns.append(f"기준 표에 같은 시각 겹침: {s}")
        elif s and (s in qt or r.get("status") == "발행 완료"):
            kept.append(r)
            have.add(s)
        else:
            warns.append(f"기준 표에서 뺌: {s}")
    dropped = len(rows) - len(kept)
    added = changed = 0
    for sched, it in qt.items():
        try:
            f = post_fields(it, by_id, by_link)
        except (TypeError, ValueError, AttributeError) as e:
            warns.append(f"글 정보 못 읽음 {it.get('id')}: {e}")
            continue
        if sched in have:
            row = next(r for r in kept if r["sched"] == sched)
            if row.get("status") == "발행 완료" and not it.get("post_id"):
                warns.append(f"발행된 글과 같은 분에 예약 겹침 {sched}: {it.get('id')}")
            elif text_changed(row, f):
                row.update(f, views=None, likes=None, replies=None, reposts=None, url="")
                changed += 1
            continue
        kept.append({"no": 0, "when": sched, "status": "예약", "how": "깃허브", **f,
                     "views": None, "likes": None, "replies": None, "reposts": None, "verify": "발행 전", "url": "", "sched": sched,
                     "lag_min": None, "hour": int(sched[11:13])})
        added += 1
    kept.sort(key=lambda r: r["sched"])
    for i, r in enumerate(kept, 1):
        r["no"] = i
    return kept, added, dropped, changed, qt


def main():
    warns = []
    rows = json.loads(BASE.read_text(encoding="utf-8"))
    q = json.loads(gh_file("queue.json"))["items"]
    stats = json.loads(gh_file("stats.json"))
    rows, added, dropped, changed, qt = sync_with_queue(rows, q, warns)
    by_sched = {r["sched"]: r for r in rows}
    post_t, post_url = {}, {}
    for l in gh_file("log.jsonl").splitlines():
        if not l.strip():
            continue
        try:
            e = json.loads(l)
            if e.get("step") == "post":
                post_t[e["id"]] = utc_to_ts(e["t"].rstrip("Z"))
            if e.get("step") == "verify" and e.get("url"):
                post_url[e["id"]] = e["url"]
        except (ValueError, KeyError, TypeError, AttributeError) as ex:
            warns.append(f"log.jsonl 한 줄 건너뜀: {ex}")
    miss = []
    for sched, it in qt.items():
        row = by_sched.get(sched)
        if not row:
            miss.append(it.get("id"))
            continue
        if not it.get("post_id"):
            if it.get("status") in ("unsure", "failed"):
                row["verify"] = "확인 필요: " + ("발행 여부 불명" if it["status"] == "unsure" else "발행 실패")
            continue
        try:
            st = ((stats.get("posts") or {}).get(str(it.get("id"))) or {}).get("latest") or {}
            t = post_t.get(it.get("id"), it["at"])
            row.update(status="발행 완료", when=kst(t), hour=int(kst(t)[11:13]), lag_min=max(0, round((t - it["at"]) / 60)),
                       verify=("광고 댓글 미게시" if not it.get("reply_id") else "원본과 일치" if it.get("verified") == "ok" else str(it.get("verified") or "확인 필요")),
                       url=post_url.get(it.get("id"), row.get("url", "")))
            for k in ("views", "likes", "replies", "reposts"):
                if k in st:
                    row[k] = st[k]
        except (TypeError, ValueError, AttributeError) as e:
            warns.append(f"발행 정보 못 읽음 {it.get('id')}: {e}")
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("updated", stats.get("updated"), "published", sum(r["status"] == "발행 완료" for r in rows), "unmatched", miss, "added", added, "dropped", dropped, "changed", changed)
    for w in warns:
        print("WARN", w)


main()
