import json
import re
import datetime
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_TOKEN", "")
UID = "me"
DRY = os.environ.get("DRY_RUN", "1") != "0"
MIN_GAP = 3600
DAILY_CAP = 5
REPLY_DELAY = 8
REPLY_TRIES = 3
REPLY_RETRY_GAP = 20
PUBLISH_DELAY = 10
PUBLISH_RETRY_GAPS = (5, 10, 20)
KST = 9 * 3600
WIN_START, WIN_END = 8, 23
UNSURE_AFTER = 1800
REPLY_RETRY_MAX = 12
REALERT_SEC = 86400
DISCLOSURE_HEAD = "[광고]"
MNF_RE = re.compile(r'"error_subcode"\s*:\s*4279009')
LINK_RE = re.compile(r"https://link\.coupang\.com/a/[A-Za-z0-9]+")
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
        msg = f"{e.code} {e.read().decode(errors='replace')[:300]}"
        raise (Ambiguous(msg) if e.code >= 500 else RuntimeError(msg)) from None
    except Exception as e:
        raise Ambiguous(f"{type(e).__name__}: {str(e)[:200]}") from None


class Ambiguous(RuntimeError):
    """요청은 나갔는데 응답을 못 받음(시간초과·연결끊김 등) — 서버에서 실제로 처리됐을 수 있다."""


def log(**kw):
    kw["t"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    if not DRY:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(kw)


def save(q):
    if DRY:
        return
    with open(Q, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)


class NotReady(RuntimeError):
    """컨테이너가 끝내 준비되지 않음(ERROR·EXPIRED·시간 초과) — 재시도해도 시간만 늘어난다."""


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
            raise NotReady(f"container {st.get('status')}: {st.get('error_message')}")
        time.sleep(5)
        waited += 5
    raise NotReady("container wait timeout")


def publish(text, reply_to=None):
    if DRY:
        raise RuntimeError("연습 모드에서는 게시하지 않음")
    p = {"media_type": "TEXT", "text": text}
    if reply_to:
        p["reply_to_id"] = reply_to
    cid = api("POST", f"/{UID}/threads", p)["id"]
    t_made = time.time()
    wait_ready(cid)
    if not DRY:
        time.sleep(PUBLISH_DELAY)
    for n in range(len(PUBLISH_RETRY_GAPS) + 1):
        try:
            pid = api("POST", f"/{UID}/threads_publish", {"creation_id": cid})["id"]
            try:
                log(step="publish-timing", kind="reply" if reply_to else "post", gap_sec=round(time.time() - t_made, 1), attempts=n + 1)
            except OSError:
                pass
            return pid
        except (Ambiguous, NotReady):
            raise
        except RuntimeError as e:
            if not MNF_RE.search(str(e)) or n == len(PUBLISH_RETRY_GAPS):
                raise
            log(step="publish-retry-mnf", kind="reply" if reply_to else "post", gap_sec=round(time.time() - t_made, 1), n=n + 1)
            if not DRY:
                time.sleep(PUBLISH_RETRY_GAPS[n])


def publish_reply(text, post_id):
    """방금 올린 글에 곧바로 댓글을 달면 'Media Not Found'(400)가 나는 일이 있어, 잠깐 기다린 뒤 400대 거절에 한해 짧게 재시도한다.
    응답 없음/5xx(Ambiguous)는 실제로 게시됐을 수 있고, 컨테이너 미준비(NotReady)는 재시도해도 시간만 늘어나므로 둘 다 그대로 올린다.
    재시도 직전에는 이미 달린 댓글이 있는지 찾아보고, 조회가 실패하면 재시도하지 않는다."""
    if not DRY:
        time.sleep(REPLY_DELAY)
    tries = max(1, REPLY_TRIES)
    for i in range(tries):
        try:
            return publish(text, reply_to=post_id)
        except (Ambiguous, NotReady):
            raise
        except RuntimeError as e:
            if i == tries - 1:
                raise
            log(step="reply-retry-soon", post_id=post_id, n=i + 1, error=str(e)[:120])
            if DRY:
                continue
            time.sleep(REPLY_RETRY_GAP)
            try:
                found = find_reply(post_id, text)
            except LookupFailed:
                raise e from None
            if found:
                return found


def norm(s):
    return "\n".join(line.rstrip() for line in (s or "").strip().splitlines())


def verify(it):
    got = api("GET", f"/{it['post_id']}", {"fields": "text,permalink"})
    body_ok = norm(got.get("text")) == norm(it["text"])
    rep = api("GET", f"/{it['reply_id']}", {"fields": "text"})
    rep_ok = norm(rep.get("text")) == norm(it["comment"])
    it["verified"] = "ok" if body_ok and rep_ok else f"mismatch body={body_ok} reply={rep_ok}"
    log(id=it["id"], step="verify", result=it["verified"], url=got.get("permalink"))


def kst_day(ts):
    return (ts + KST) // 86400


def kst_hour(ts):
    return ((ts + KST) % 86400) // 3600


def sent_at(it):
    return it.get("posted_at") or it.get("publishing_at") or it["at"]


class LookupFailed(RuntimeError):
    pass


def find_reply(post_id, comment):
    """이미 달린 댓글 id. 없으면 None. 조회 자체가 실패하면 LookupFailed(이번 실행은 게시하지 않음). 최대 5쪽."""
    after = None
    for _ in range(5):
        params = {"fields": "id,text"}
        if after:
            params["after"] = after
        try:
            r = api("GET", f"/{post_id}/replies", params)
        except Exception as e:
            raise LookupFailed(str(e)[:200]) from None
        for x in r.get("data", []):
            if norm(x.get("text")) == norm(comment):
                return x.get("id")
        after = ((r.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not (r.get("paging") or {}).get("next"):
            return None
    raise LookupFailed("댓글이 5쪽을 넘어 확인 불가")


def _ts(x):
    try:
        return int(datetime.datetime.strptime(x["timestamp"], "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except Exception:
        return None


def find_post(text, since=0):
    """내 최근 글 50개 중 같은 본문이 since 이후에 이미 올라가 있으면 그 id, 없으면 None. 조회 실패는 LookupFailed."""
    try:
        r = api("GET", "/me/threads", {"fields": "id,text,timestamp", "limit": 50})
    except Exception as e:
        raise LookupFailed(str(e)[:200]) from None
    for x in r.get("data", []):
        if norm(x.get("text")) == norm(text):
            t = _ts(x)
            if t is None:
                raise LookupFailed("글 시간 정보를 읽지 못함 — 중복 여부 판단 불가")
            if t >= since:
                return x.get("id")
    return None


def notify(msg):
    """텔레그램으로 경고를 보낸다(실패해도 발행 흐름은 멈추지 않음). 비밀값이 없거나 연습 모드면 보내지 않는다."""
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if DRY or not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": "[스레드 로봇 경고]\n" + msg}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=15).read()
    except Exception as e:
        print("telegram 실패:", type(e).__name__)


def alerts(q, now):
    """사람이 봐야 하는 상태를 사유별로 한 번씩(댓글 미게시는 하루 한 번 다시) 로그에 남기고 True 반환 → 실행 실패 표시로 알림."""
    bad = False
    for it in q["items"]:
        reasons = []
        if it["status"] == "partial" and it.get("post_id") and not it.get("reply_id"):
            tries = it.get("reply_tries", 0)
            if tries >= REPLY_RETRY_MAX:
                reasons.append(("c_exhaust", "댓글 반복 실패 — 광고 표기 없는 본문이 공개 중(자동 재시도 끝)"))
            else:
                reasons.append(("c_first", "광고 댓글 미게시 — 광고 표기 없는 본문이 공개 중(재시도 중)"))
        if it["status"] == "scheduled" and it.get("lookup_fail", 0) >= 3:
            reasons.append(("lookup", "최근 글 조회가 계속 실패 — 발행이 멈춰 있음(권한·장애 확인)"))
        if str(it.get("verified") or "").startswith("mismatch"):
            reasons.append(("verify", "원본 대조 불일치"))
        if it["status"] == "unsure":
            reasons.append(("unsure", "발행 여부 불명 — 수동 확인 필요"))
        if it["status"] != "failed":
            it.pop("alert_failed", None)
        elif not it.get("post_id"):
            reasons.append(("failed", "발행 실패 — 자동 재시도 안 함(스레드에 올라갔는지 확인 후 예약으로 되돌려야 함): " + str(it.get("error"))[:80]))
        for key, why in reasons:
            at_key = "alert_" + key
            last = it.get(at_key)
            if last and not (key == "c_exhaust" and now - last > REALERT_SEC):
                continue
            it[at_key] = now
            log(id=it["id"], step="ALERT", why=why)
            notify(f"{it['id']}번 글: {why}")
            bad = True
    return bad


def main():
    if os.environ.get("NOTIFY_TEST") == "1":
        notify("알림 시험입니다. 이 메시지가 보이면 서버에서 텔레그램 경고가 정상 작동합니다.")
        print("notify test done")
        return
    if not TOKEN:
        log(step="fatal", error="THREADS_TOKEN 없음")
        notify("THREADS_TOKEN이 없어 발행기가 멈췄습니다")
        sys.exit(1)
    me = api("GET", "/me", {"fields": "id,username"})
    log(step="whoami", username=me.get("username"), dry=DRY)
    q = json.load(open(Q, encoding="utf-8"))
    now = int(time.time())
    for it in q["items"]:
        if not DRY and it["status"] == "publishing" and not it.get("post_id") and now - it.get("publishing_at", it["at"]) > UNSURE_AFTER:
            it["status"] = "unsure"
            it["error"] = "발행 중 중단 — 스레드에 올라갔는지 수동 확인 필요"
            log(id=it["id"], step="unsure", why="publishing 방치")
    if not DRY:
        for it in q["items"]:
            if it["status"] == "unsure" and not it.get("post_id"):
                try:
                    found = find_post(it["text"], it["at"] - 86400)
                except LookupFailed:
                    found = None
                if found:
                    it["post_id"], it["posted_at"] = found, it.get("publishing_at") or now
                    it["status"], it["error"], it["reply_tries"] = "partial", None, 0
                    log(id=it["id"], step="recovered", post_id=found)
    done = [i for i in q["items"] if i["post_id"] or i["status"] in ("unsure", "publishing")]
    last = max([sent_at(i) for i in done], default=0)
    today = [i for i in done if kst_day(sent_at(i)) == kst_day(now)]
    due = [i for i in q["items"] if i["status"] == "scheduled" and i["at"] <= now]
    log(step="status", due=[i["id"] for i in due], pending=sum(i["status"] == "scheduled" for i in q["items"]))
    for it in q["items"]:
        if DRY or it["status"] != "partial" or not it.get("post_id") or it.get("reply_id"):
            continue
        if it.get("reply_tries", 0) >= REPLY_RETRY_MAX:
            continue
        try:
            try:
                found = find_reply(it["post_id"], it["comment"])
            except LookupFailed as e:
                it["reply_tries"] = it.get("reply_tries", 0) + 1
                it["error"] = "댓글 조회 실패: " + str(e)
                log(id=it["id"], step="lookup-failed", tries=it["reply_tries"])
                save(q)
                continue
            if found:
                it["reply_id"] = found
                log(id=it["id"], step="reply-found", reply_id=found)
            else:
                it["reply_tries"] = it.get("reply_tries", 0) + 1
                save(q)
                it["reply_id"] = publish(it["comment"], reply_to=it["post_id"])
                log(id=it["id"], step="reply", reply_id=it["reply_id"], retry=True)
            it["status"] = "published"
            save(q)
            verify(it)
        except Exception as e:
            it["error"] = str(e)[:300]
            log(id=it["id"], step="error", error=it["error"], status=it["status"], retry=True)
        save(q)
    flagged_pre = False
    for it in sorted(due, key=lambda x: x["at"]):
        if not WIN_START <= kst_hour(now) < WIN_END:
            log(id=it["id"], step="skip", why="발행 시간대(KST 8~23시) 밖")
            break
        if len(today) >= DAILY_CAP:
            log(id=it["id"], step="skip", why="daily cap")
            continue
        if last and now - last < MIN_GAP:
            log(id=it["id"], step="skip", why="min gap")
            continue
        if DRY:
            log(id=it["id"], step="dry-run", would_post=it["text"][:40])
            continue
        cm = it.get("comment") or ""
        if not cm.startswith(DISCLOSURE_HEAD) or not LINK_RE.search(cm):
            it["status"] = "failed"
            it["error"] = "광고 문구([광고])나 쿠팡 링크가 없는 항목 — 발행 안 함"
            it["alert_failed"] = now
            log(id=it["id"], step="ALERT", why=it["error"])
            notify(f"{it['id']}번 글: {it['error']}")
            save(q)
            flagged_pre = True
            continue
        it["status"] = "publishing"
        it["publishing_at"] = now
        save(q)
        try:
            try:
                dup = find_post(it["text"], it["at"] - 86400)
            except LookupFailed as e:
                it["status"] = "scheduled"
                it["lookup_fail"] = it.get("lookup_fail", 0) + 1
                it.pop("publishing_at", None)
                it["error"] = "최근 글 조회 실패 — 중복 확인 불가라 이번엔 발행 안 함: " + str(e)
                log(id=it["id"], step="lookup-failed-post")
                save(q)
                break
            it["lookup_fail"] = 0
            if dup:
                it["post_id"] = dup
                log(id=it["id"], step="dup-found", post_id=dup)
            else:
                it["post_id"] = publish(it["text"])
            it["posted_at"] = int(time.time())
            it["reply_tries"] = 1
            it["status"] = "partial"
            save(q)
            log(id=it["id"], step="post", post_id=it["post_id"])
            reply = None
            if dup:
                try:
                    reply = find_reply(it["post_id"], it["comment"])
                except LookupFailed:
                    raise RuntimeError("기존 글의 댓글 조회 실패 — 다음 실행에서 재시도")
            it["reply_id"] = reply or publish_reply(it["comment"], it["post_id"])
            it["status"] = "published"
            save(q)
            log(id=it["id"], step="reply", reply_id=it["reply_id"])
            verify(it)
        except Exception as e:
            it["error"] = str(e)[:300]
            if not it["post_id"]:
                it["status"] = "unsure" if isinstance(e, Ambiguous) else "failed"
            log(id=it["id"], step="error", error=it["error"], status=it["status"])
        save(q)
        last = now
        today.append(it)
        break
    flagged = alerts(q, int(time.time()))
    save(q)
    if (flagged or flagged_pre) and not DRY:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(step="fatal", error=str(e)[:300])
        sys.exit(1)
