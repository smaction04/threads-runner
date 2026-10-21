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
MIN_PENDING = int(os.environ.get("MIN_PENDING", "8"))
TARGET = int(os.environ.get("TARGET", "15"))
KST = 9 * 3600
MAXLEN = 120
MIN_SLOT_GAP = 4500
PER_DAY_MIN, PER_DAY_MAX = 3, 4
HIGH_PRICE = 30000
HIGH_SHARE = 0.3
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


def slots(items, n, now, rnd=random):
    """KST 날짜별로 이미 잡힌 글 수를 세어 하루 3~4건이 되도록 8~23시 안에 배정. 기존 글과 75분 이상 간격.
    이미 예약이 있는 마지막 날까지는 부족분(3건 미만)을 n과 상관없이 먼저 채우고, 그 뒤 n개가 될 때까지 다음 날로 넘어간다."""
    taken = sorted(i["at"] for i in items if i.get("status") in ("scheduled", "published", "partial", "publishing", "unsure"))
    last_day = max([day_of(i["at"]) for i in items if i.get("status") == "scheduled"], default=day_of(now))
    out, d = [], day_of(now)
    for _ in range(60):
        if d > last_day and len(out) >= n:
            break
        base = d * 86400 - KST
        have = [t for t in taken + out if day_of(t) == d]
        need = 0
        if len(have) < PER_DAY_MIN:
            need = rnd.choice([PER_DAY_MIN, PER_DAY_MAX]) - len(have)
        tries = 0
        while need > 0 and tries < 300:
            tries += 1
            t = base + rnd.randint(8 * 3600, 23 * 3600 - 60)
            if t <= now + 3600 or any(abs(t - x) < MIN_SLOT_GAP for x in taken + out):
                continue
            out.append(t)
            need -= 1
        d += 1
    return sorted(out)


def main():
    q = json.load(open(Q, encoding="utf-8"))
    items = q["items"]
    for i in items:
        try:
            i["id"] = int(i["id"])
        except (TypeError, ValueError):
            pass
    pend = [i for i in items if i["status"] == "scheduled"]
    if len(pend) >= MIN_PENDING:
        log(step="refill-skip", pending=len(pend))
        return
    if not KEY:
        log(step="refill-fatal", error="GEMINI_API_KEY 없음")
        sys.exit(1)
    pool = [p for p in json.load(open(POOL, encoding="utf-8")) if p.get("use") and re.fullmatch(r"https://link\.coupang\.com/a/[A-Za-z0-9]+", str(p.get("link", "")))]
    if not pool:
        log(step="refill-fatal", error="사용할 상품 없음")
        sys.exit(1)
    dash = json.load(open(DASH, encoding="utf-8"))
    now = int(time.time())
    need = TARGET - len(pend)
    times = slots(items, need, now)
    nid = max([i["id"] for i in items if isinstance(i["id"], int)], default=0) + 1
    nno = max([r.get("no", 0) for r in dash], default=0) + 1
    by_at = sorted(items, key=lambda x: x["at"])
    used = {}
    for i in items:
        used[i.get("product")] = max(used.get(i.get("product"), 0), i["at"])
    recent = [i["text"] for i in by_at]
    recent_styles = [i.get("style") for i in by_at]
    last_prod = by_at[-1].get("product") if by_at else None
    added = 0
    for at in times:
        if time.time() - T0 > BUDGET_SEC:
            log(step="refill-budget", added=added)
            break
        want_high = random.random() < HIGH_SHARE
        tier_pool = [p for p in pool if is_high(p) == want_high] or pool
        cand = sorted([p for p in tier_pool if p["id"] != last_prod] or tier_pool, key=lambda p: used.get(p["id"], 0))
        prod = cand[0]
        text = None
        for _ in range(2):
            names = HIGH_STYLES if is_high(prod) else STYLES
            style = random.choice([x for x in names if x not in recent_styles[-3:]] or list(names))
            text = make_text(prod, recent, style, at)
            if text:
                break
        if not text:
            log(step="refill-skip-one", product=prod["id"])
            used[prod["id"]] = at
            continue
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
        used[prod["id"]] = at
        last_prod = prod["id"]
        nid += 1
        nno += 1
        added += 1
        log(step="refill-add", id=it["id"], product=prod["id"], at=sched, style=style)
    if DRY:
        log(step="refill-dry", would_add=added)
        return
    json.dump(q, open(Q, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(dash, open(DASH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(step="refill-done", added=added)


if __name__ == "__main__":
    main()
