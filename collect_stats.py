import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://graph.threads.net/v1.0"
TOKEN = os.environ["THREADS_TOKEN"]
METRICS = "views,likes,replies,reposts,quotes"


def api(path, params):
    p = dict(params)
    p["access_token"] = TOKEN
    url = BASE + path + "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {e.read().decode(errors='replace')[:200]}") from None


def main():
    q = json.load(open("queue.json", encoding="utf-8"))
    try:
        st = json.load(open("stats.json", encoding="utf-8"))
    except FileNotFoundError:
        st = {"posts": {}}
    now = time.strftime("%Y-%m-%d %H:%M", time.gmtime()) + "Z"
    for it in q["items"]:
        if not it.get("post_id"):
            continue
        rec = st["posts"].setdefault(str(it["id"]), {"post_id": it["post_id"], "posted_at": it.get("at"), "history": []})
        try:
            d = api(f"/{it['post_id']}/insights", {"metric": METRICS})
            vals = {m["name"]: m["values"][0]["value"] for m in d["data"]}
        except Exception as e:
            rec["last_error"] = str(e)[:200]
            continue
        rec.pop("last_error", None)
        rec["latest"] = dict(vals, t=now)
        if not rec["history"] or rec["history"][-1] != rec["latest"]:
            rec["history"].append(rec["latest"])
    st["updated"] = now
    json.dump(st, open("stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(now, {k: v.get("latest", {}).get("views") for k, v in st["posts"].items()})


main()
