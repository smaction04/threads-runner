import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://graph.threads.net/v1.0"
TOKEN = os.environ["THREADS_TOKEN"]
UID = "me"
DRY = os.environ.get("DRY_RUN", "1") != "0"
MIN_GAP = 3600
DAILY_CAP = 5
Q = "queue.json"
LOG = "log.jsonl"


def api(method, path, params=None):
    p = dict(params or {})
    p["access_token"] = TOKEN
    url = BASE + path
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(p)
    else:
        data = urllib.parse.urlencode(p).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {e.read().decode(errors='replace')[:300]}") from None


def log(**kw):
    kw["t"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(kw)


def save(q):
    with open(Q, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)


def wait_ready(cid, timeout=120):
    waited = 0
    while waited <= timeout:
        try:
            st = api("GET", f"/{cid}", {"fields": "status,error_message"})
        except RuntimeError:
            time.sleep(5)
            waited += 5
            continue
        if st.get("status") == "FINISHED":
            return
        if st.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {st.get('status')}: {st.get('error_message')}")
        time.sleep(5)
        waited += 5
    raise RuntimeError("container wait timeout")


def publish(text, reply_to=None):
    p = {"media_type": "TEXT", "text": text}
    if reply_to:
        p["reply_to_id"] = reply_to
    cid = api("POST", f"/{UID}/threads", p)["id"]
    wait_ready(cid)
    return api("POST", f"/{UID}/threads_publish", {"creation_id": cid})["id"]


def norm(s):
    return "\n".join(line.rstrip() for line in (s or "").strip().splitlines())


def verify(it):
    got = api("GET", f"/{it['post_id']}", {"fields": "text,permalink"})
    body_ok = norm(got.get("text")) == norm(it["text"])
    rep = api("GET", f"/{it['reply_id']}", {"fields": "text"})
    rep_ok = norm(rep.get("text")) == norm(it["comment"])
    it["verified"] = "ok" if body_ok and rep_ok else f"mismatch body={body_ok} reply={rep_ok}"
    log(id=it["id"], step="verify", result=it["verified"], url=got.get("permalink"))


def main():
    me = api("GET", "/me", {"fields": "id,username"})
    log(step="whoami", username=me.get("username"), dry=DRY)
    q = json.load(open(Q, encoding="utf-8"))
    now = int(time.time())
    done = [i for i in q["items"] if i["post_id"]]
    last = max([i["at"] for i in done], default=0)
    today = [i for i in done if now - i["at"] < 86400]
    due = [i for i in q["items"] if i["status"] == "scheduled" and i["at"] <= now]
    log(step="status", due=[i["id"] for i in due], pending=sum(i["status"] == "scheduled" for i in q["items"]))
    for it in q["items"]:
        if DRY or it["status"] != "partial" or not it.get("post_id") or it.get("reply_id"):
            continue
        try:
            it["reply_id"] = publish(it["comment"], reply_to=it["post_id"])
            it["status"] = "published"
            save(q)
            log(id=it["id"], step="reply", reply_id=it["reply_id"], retry=True)
            verify(it)
        except Exception as e:
            it["error"] = str(e)[:300]
            log(id=it["id"], step="error", error=it["error"], status=it["status"], retry=True)
        save(q)
    for it in sorted(due, key=lambda x: x["at"]):
        if len(today) >= DAILY_CAP:
            log(id=it["id"], step="skip", why="daily cap")
            continue
        if last and now - last < MIN_GAP:
            log(id=it["id"], step="skip", why="min gap")
            continue
        if DRY:
            log(id=it["id"], step="dry-run", would_post=it["text"][:40])
            continue
        it["status"] = "publishing"
        save(q)
        try:
            it["post_id"] = publish(it["text"])
            it["status"] = "partial"
            save(q)
            log(id=it["id"], step="post", post_id=it["post_id"])
            it["reply_id"] = publish(it["comment"], reply_to=it["post_id"])
            it["status"] = "published"
            save(q)
            log(id=it["id"], step="reply", reply_id=it["reply_id"])
            verify(it)
        except Exception as e:
            it["error"] = str(e)[:300]
            if not it["post_id"]:
                it["status"] = "failed"
            log(id=it["id"], step="error", error=it["error"], status=it["status"])
        save(q)
        last = now
        today.append(it)
        break
    save(q)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(step="fatal", error=str(e)[:300])
        sys.exit(1)
