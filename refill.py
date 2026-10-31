import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

Q = "queue.json"
LOG = "log.jsonl"
POOL = "products.json"
DASH = "dash/dash_base.json"
KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
DRY = os.environ.get("DRY_RUN", "1") != "0"
KST = 9 * 3600
MAXLEN = 120
MIN_SLOT_GAP = 3600
AHEAD_DAYS = 3
KIND_WINDOW = 3 * 86400
LIMITS = "limits.json"
ACTIVE = ("scheduled", "published", "partial", "publishing", "unsure")
HOURS = [8, 9, 10, 11, 14] * 3 + list(range(12, 23))
HIGH_PRICE = 30000
BUDGET_SEC = 330
T0 = time.time()
DISCLOSURE = "[광고] 이 게시글은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
BASE_RULE = (
    "스레드 살림템 계정의 글. 공통 규칙: 전체 120자 이내, 한 줄에 한 문장 줄바꿈(2~6줄), 반말·커뮤니티 말투(ㅋㅋ ㄷㄷ ;; 허용), "
    "이모지 0~1개, 해시태그·링크·URL은 쓰지 않는다(링크는 댓글에 따로 붙는다). 그 외 표현은 자유롭게, 읽는 사람이 멈추고 볼 만큼 생생하게 쓴다."
)
STYLES = {
    "질문형": "3~4줄. 첫 줄은 독자에게 던지는 질문(예: 싱크대 냄새 다들 어떻게 잡아?). 가운데는 이 상품이 그 고민에 쓰인다는 한마디. 마지막 줄은 의견을 묻는 질문.",
    "한줄형": "2~3줄로 아주 짧게. 한 줄 감탄이나 한마디 + 상품 쓰임새 한 줄. 군더더기 없이.",
    "공감형": "4~5줄. 첫 줄은 일상 속 짜증나는 순간 공감(예: 청소 미루다 결국 주말 다 날림). 이어서 이 상품이 그럴 때 필요하다는 이야기. 마지막은 공감 유도.",
    "추천형": "3~4줄. '이거 하나 쟁여두면 마음 편함', '집에 하나쯤 있으면 좋은 것' 같은 톤으로 쟁여두기·추천. 마지막은 저장해두라는 한마디.",
    "비교형": "4줄. 예전 방식(손으로 박박, 대충 닦기 등)과 이 상품을 쓰는 방식을 대비하되 수치는 쓰지 않는다. 마지막은 질문.",
    "발견형": "4~5줄. '이런 것도 있었네' 하고 뒤늦게 알게 된 놀람. 상품 쓰임새를 한 줄로 설명. 마지막은 너네도 알았냐는 식의 질문.",
    "계절형": "3~4줄. 오늘 날짜 기준 {MONTH}월의 실제 계절과 날씨에만 맞춰(다른 계절 표현 금지) 이 상품이 필요한 이유를 말한다. 마지막은 준비했냐는 질문.",
    "체크리스트형": "4~6줄. 첫 줄은 '집에 이거 있으면 청소 반은 끝', 이어서 이 상품이 해결해주는 상황을 짧게 나열. 마지막은 저장 유도.",
}


HIGH_RULE = (
    "비싼 상품(3만원 이상) 글. 공통 규칙은 그대로, 단 이 상품을 쓰면 일상이 어떻게 달라지는지(얻는 것)를 먼저 말하고 사야 하는 이유를 한 가지만 잡는다. "
    "가격·할인율·월/일 환산 금액·보증기간·성능 수치·제조사 보증 같은 사실은 입력에 없으니 절대 지어내지 않는다. 상품명에 없는 기능도 쓰지 않는다. "
    "과장된 호들갑 대신 차분하게 확신하는 말투."
)
HIGH_STYLES = {
    "가치형": "3~4줄. 첫 줄은 이 상품이 있으면 사라지는 번거로움. 가운데는 쓰임새 한 줄. 마지막은 '써본 사람 있어?' 같은 질문.",
    "이야기형": "4~5줄. 이 상품을 쓰게 되는 일상의 한 장면을 꾸며내지 말고 흔한 상황으로만 그린다. 마지막은 고민 중이라면 이런 경우엔 괜찮다는 한마디.",
    "전환형": "3~4줄. 예전에 쓰던 불편한 방식과 이 상품을 쓰는 방식을 대비(수치 없이). 마지막은 바꿀지 말지 묻는 질문.",
}


def log(**kw):
    kw["t"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    if not DRY:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(kw)


def gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}).encode()
    req = urllib.request.Request(url, data=body, headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"gemini {e.code} {e.read().decode(errors='replace')[:200]}") from None
    return d["candidates"][0]["content"]["parts"][0]["text"]


FORBIDDEN = ("기절", "소름", "소오름", "현타", "충격받", "충격먹")
UNITS = ("ml", "kg", "cm", "mm")


def norm(t):
    return re.sub(r"\s+", "", t)


def key(t):
    return re.sub(r"[\s.,!?~ㅋㅎㅠㅜ;:'\"·…\-_()]+", "", t).lower()


def valid(t, prod=None):
    """글의 모양만 검사한다(줄 수·길이·링크/해시태그). 문구 내용(효능·숫자·기간 등)은 제한하지 않는다."""
    if not isinstance(t, str):
        return False
    lines = [l for l in t.splitlines() if l.strip()]
    if not 2 <= len(lines) <= 6 or len(t) > MAXLEN:
        return False
    if re.search(r"[#＃]", t) or re.search(r"[A-Za-z0-9\-]+\s*\.\s*[A-Za-z]{2,}", t) or re.search(r"https?:|www\.|://", t, re.I):
        return False
    return True


def month_of(ts):
    return int(time.strftime("%m", time.gmtime(ts + KST)))


def is_high(prod):
    return prod.get("tier") == "high" or prod.get("price", 0) >= HIGH_PRICE


def make_text(prod, recent, style, at):
    kind = prod.get("kind") or prod["name"]
    high = is_high(prod)
    rule = (HIGH_STYLES if high else STYLES)[style].replace("{MONTH}", str(month_of(at)))
    prompt = "\n".join([
        "너는 스레드에 올릴 짧은 후킹 글 작성자다.",
        "[공통 규칙]", BASE_RULE,
        *(["[비싼 상품 규칙]", HIGH_RULE, f"[상품명] {prod['name']}"] if high else []),
        f"[이번 글의 형식: {style}]", rule,
        f"[상품 종류] {kind}",
        "[이미 쓴 글: 첫 줄과 표현이 겹치지 않게 새로 쓸 것]",
        "\n---\n".join(recent[-8:]),
        '출력은 JSON만: {"text": "글"}',
    ])
    for _ in range(2):
        if time.time() - T0 > BUDGET_SEC:
            return None
        try:
            t = str(json.loads(gemini(prompt)).get("text", "")).strip()
        except Exception as e:
            log(step="refill-llm-error", error=str(e)[:200])
            continue
        firsts = {norm(r.splitlines()[0]) for r in recent if r.strip()}
        if valid(t, prod) and norm(t.splitlines()[0]) not in firsts:
            return t
    return None


def day_of(ts):
    return (ts + KST) // 86400


def band(price):
    if price < 5000:
        return None
    return "S" if price <= 30000 else "M" if price <= 100000 else "L"


def kind_of(prod):
    return prod.get("kind") or re.sub(r"[^가-힣a-z]", "", prod["name"].lower())[:8]


def target(times, d, cap):
    want = sum(day_of(t) == d - 1 for t in times) + 1
    return min(want, cap) if cap is not None else want


def day_plan(times, now, cap):
    """오늘~3일 뒤까지 날마다 목표 = 전날 개수 + 1 (경고 뒤엔 cap 이하). 모자란 날과 개수만 돌려준다."""
    times, plan = list(times), []
    for d in range(day_of(now), day_of(now) + AHEAD_DAYS + 1):
        n = target(times, d, cap) - sum(day_of(t) == d for t in times)
        if n > 0:
            plan.append((d, n))
            times += [d * 86400 - KST + 12 * 3600] * n
    return plan


def pick_time(d, taken, now, rnd=random):
    base = d * 86400 - KST
    for _ in range(500):
        t = base + rnd.choice(HOURS) * 3600 + rnd.randint(1, 59) * 60
        if t > now + 3600 and all(abs(t - x) >= MIN_SLOT_GAP for x in taken):
            return t
    return None


def save(path, obj):
    crlf = b"\r\n" in open(path, "rb").read(4096)
    t = json.dumps(obj, ensure_ascii=False, indent=1)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(t.replace("\n", "\r\n") if crlf else t)


def main():
    q = json.load(open(Q, encoding="utf-8"))
    items = q["items"]
    for i in items:
        try:
            i["id"] = int(i["id"])
        except (TypeError, ValueError):
            pass
    try:
        cap = json.load(open(LIMITS, encoding="utf-8")).get("warn_cap")
    except FileNotFoundError:
        cap = None
    now = int(time.time())
    plan = day_plan([i["at"] for i in items if i.get("status") in ACTIVE], now, cap)
    if not plan:
        log(step="refill-skip", pending=sum(i["status"] == "scheduled" for i in items))
        return
    if not KEY:
        log(step="refill-fatal", error="GEMINI_API_KEY 없음")
        sys.exit(1)
    allp = json.load(open(POOL, encoding="utf-8"))
    prods = {p["id"]: p for p in allp}
    used_ids = {i.get("product") for i in items}
    pool = [p for p in allp if p.get("use") and p["id"] not in used_ids and band(p.get("price", 0))
            and re.fullmatch(r"https://link\.coupang\.com/a/[A-Za-z0-9]+", str(p.get("link", "")))]
    pool.sort(key=lambda p: -p.get("reviews", 0))
    dash = json.load(open(DASH, encoding="utf-8"))
    nid = max([i["id"] for i in items if isinstance(i["id"], int)], default=0) + 1
    nno = max([r.get("no", 0) for r in dash], default=0) + 1
    by_at = sorted(items, key=lambda x: x["at"])
    recent = [i["text"] for i in by_at]
    recent_styles = [i.get("style") for i in by_at]
    act = [(i["at"], prods.get(i.get("product"), {})) for i in items if i.get("status") in ACTIVE]
    added = 0
    fail = red = None
    for d in range(plan[0][0], day_of(now) + AHEAD_DAYS + 1):
        times = [x[0] for x in act]
        snap = (len(items), len(dash), len(recent), len(recent_styles), len(act), nid, nno, added, list(pool))
        day = time.strftime("%Y-%m-%d", time.gmtime(d * 86400))
        for _ in range(target(times, d, cap) - sum(day_of(t) == d for t in times)):
            if time.time() - T0 > BUDGET_SEC:
                fail = "refill-budget"
                break
            at = pick_time(d, [x[0] for x in act], now)
            if at is None:
                fail = "refill-no-slot"
                break
            kinds = {kind_of(p) for t, p in act if p and abs(t - at) < KIND_WINDOW}
            day_b = [band(p.get("price", 0)) for t, p in act if p and day_of(t) == d]
            near_b = [band(p.get("price", 0)) for t, p in act if p and d - 2 <= day_of(t) <= d]
            order = sorted("SML", key=lambda x: (x in day_b, near_b.count(x)))
            prod = next((p for x in order for p in pool if band(p["price"]) == x and kind_of(p) not in kinds), None)
            if not prod:
                fail = "refill-no-product"
                break
            pool.remove(prod)
            text = None
            for _ in range(2):
                names = HIGH_STYLES if is_high(prod) else STYLES
                style = random.choice([x for x in names if x not in recent_styles[-3:]] or list(names))
                text = make_text(prod, recent, style, at)
                if text:
                    break
            if not text:
                fail = "refill-no-text"
                break
            it = {"id": nid, "at": at, "text": text, "comment": DISCLOSURE + chr(10) + prod["link"], "status": "scheduled",
                  "post_id": None, "reply_id": None, "verified": None, "error": None, "source_draft": None,
                  "product": prod["id"], "auto": True, "style": style}
            sched = time.strftime("%Y-%m-%d %H:%M", time.gmtime(at + KST))
            row = {"no": nno, "when": sched, "status": "예약", "how": "깃허브", "product": prod["name"][:20],
                   "price": prod["price"], "style": style, "first_line": text.splitlines()[0], "chars": len(text),
                   "views": None, "likes": None, "replies": None, "reposts": None, "verify": "발행 전", "url": "",
                   "sched": sched, "lag_min": None, "hour": int(sched[11:13])}
            items.append(it)
            dash.append(row)
            recent.append(text)
            recent_styles.append(style)
            act.append((at, prod))
            nid += 1
            nno += 1
            added += 1
            log(step="refill-add", id=it["id"], product=prod["id"], at=sched, style=style)
        if not fail:
            got = {band(p.get("price", 0)) for t, p in act if p and day_of(t) == d} - {None}
            if added > snap[7] and sum(day_of(t) == d for t, p in act) >= 3 and len(got) < 3:
                fail = "refill-band-missing"
        if fail:
            del items[snap[0]:], dash[snap[1]:], recent[snap[2]:], recent_styles[snap[3]:], act[snap[4]:]
            nid, nno, added, pool = snap[5], snap[6], snap[7], snap[8]
            log(step=fail, day=day, left=len(pool))
            if d == day_of(now) and fail != "refill-budget":
                red, fail = fail, None
                continue
            break
    if DRY:
        log(step="refill-dry", would_add=added)
        if fail or red:
            sys.exit(1)
        return
    if added:
        save(Q, q)
        save(DASH, dash)
    log(step="refill-done", added=added)
    if fail or red:
        sys.exit(1)


if __name__ == "__main__":
    main()
