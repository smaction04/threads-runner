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
DRY = os.environ.get("DRY_RUN", "0") != "0"
MIN_PENDING = int(os.environ.get("MIN_PENDING", "8"))
TARGET = int(os.environ.get("TARGET", "15"))
KST = 9 * 3600
DISCLOSURE = "[광고] 이 게시글은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
BASE_RULE = (
    "스레드 살림템 계정의 글. 공통 규칙: 전체 120자 이내, 한 줄에 한 문장 줄바꿈, 반말·커뮤니티 말투(ㅋㅋ ㄷㄷ ;; 허용), "
    "이모지 0~1개, 해시태그·링크·URL 금지, 가격·구성 숫자·스펙은 쓰지 않고 상품 종류와 쓰임새만 말한다. "
    "적히지 않은 구체 수치·효능·인증·기간(몇 분 만에, 며칠 만에 등)은 지어내지 않는다. "
    "상품명(브랜드·규격·용량)을 그대로 쓰지 말고 '곰팡이 세정제', '신발 탈취제'처럼 종류로만 부른다. '기절할 뻔', '소름 돋', '현타 온다', '충격받' 같은 표현은 쓰지 않는다."
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


def log(**kw):
    kw["t"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(kw)


def gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}).encode()
    req = urllib.request.Request(url, data=body, headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"gemini {e.code} {e.read().decode(errors='replace')[:200]}") from None
    return d["candidates"][0]["content"]["parts"][0]["text"]


def valid(t):
    lines = [l for l in t.splitlines() if l.strip()]
    if not 2 <= len(lines) <= 6 or len(t) > 130 or re.search(r"기절|소름|현타|충격받", t):
        return False
    if re.search(r"https?://|www\.|#\w|\.com|\.kr", t):
        return False
    return True


def norm(t):
    return re.sub(r"\s+", "", t)


def make_text(prod, recent, style):
    prompt = (f"너는 스레드에 올릴 짧은 후킹 글 작성자다.\n[공통 규칙]\n{BASE_RULE}\n[이번 글의 형식: {style}]\n{STYLES[style].replace('{MONTH}', str(int(time.strftime('%m', time.gmtime(time.time() + KST)))))}\n[상품명] {prod['name']}\n"
              "[이미 쓴 글: 첫 줄과 표현이 겹치지 않게 새로 쓸 것]\n" + "\n---\n".join(recent[-8:]) +
              '\n출력은 JSON만: {"text": "글"}')
    for _ in range(3):
        try:
            raw = gemini(prompt)
            t = str(json.loads(raw).get("text", "")).strip()
        except Exception as e:
            log(step="refill-llm-error", error=str(e)[:200])
            continue
        if valid(t) and norm(t.splitlines()[0]) not in {norm(r.splitlines()[0]) for r in recent}:
            return t
    return None


def slots(start, n):
    out = []
    day0 = (start + KST) // 86400 * 86400 - KST
    while len(out) < n:
        k = random.choice([3, 4])
        lo, hi = 8 * 3600, 23 * 3600
        seg = (hi - lo) / k
        for j in range(k):
            t = day0 + int(lo + seg * j + random.uniform(0.1, 0.9) * seg)
            if t > start + 3600:
                out.append(t)
        day0 += 86400
    return out[:n]


def main():
    q = json.load(open(Q, encoding="utf-8"))
    items = q["items"]
    pend = [i for i in items if i["status"] == "scheduled"]
    if len(pend) >= MIN_PENDING:
        log(step="refill-skip", pending=len(pend))
        return
    if not KEY:
        log(step="refill-fatal", error="GEMINI_API_KEY 없음")
        sys.exit(1)
    pool = [p for p in json.load(open(POOL, encoding="utf-8")) if p.get("use")]
    need = TARGET - len(pend)
    last_at = max([i["at"] for i in items], default=int(time.time()))
    times = slots(max(last_at, int(time.time())), need)
    nid = max(i["id"] for i in items) + 1
    used = {}
    for i in items:
        used[i.get("product")] = max(used.get(i.get("product"), 0), i["at"])
    recent = [i["text"] for i in items]
    recent_styles = [i.get("style") for i in sorted(items, key=lambda x: x["at"])]
    dash = json.load(open(DASH, encoding="utf-8"))
    added = 0
    last_prod = (sorted(items, key=lambda x: x["at"])[-1].get("product") if items else None)
    for at in times:
        cand = sorted((p for p in pool if p["id"] != last_prod), key=lambda p: used.get(p["id"], 0))
        prod = cand[0]
        text = None
        for _ in range(2):
            style = random.choice([x for x in STYLES if x not in recent_styles[-3:]])
            text = make_text(prod, recent, style)
            if text:
                break
        if not text:
            log(step="refill-skip-one", product=prod["id"])
            used[prod["id"]] = at
            continue
        it = {"id": nid, "at": at, "text": text, "comment": f"{DISCLOSURE}\n{prod['link']}", "status": "scheduled",
              "post_id": None, "reply_id": None, "verified": None, "error": None, "source_draft": None,
              "product": prod["id"], "auto": True}
        sched = time.strftime("%Y-%m-%d %H:%M", time.gmtime(at + KST))
        row = {"no": len(dash) + 1, "when": sched, "status": "예약", "how": "깃허브", "product": prod["name"][:20],
               "price": prod["price"], "style": "신형", "first_line": text.splitlines()[0], "chars": len(text),
               "views": None, "likes": None, "replies": None, "reposts": None, "verify": "발행 전", "url": "",
               "sched": sched, "lag_min": None, "hour": int(sched[11:13])}
        it["style"] = style
        recent_styles.append(style)
        items.append(it)
        dash.append(row)
        recent.append(text)
        used[prod["id"]] = at
        last_prod = prod["id"]
        nid += 1
        added += 1
        log(step="refill-add", id=it["id"], product=prod["id"], at=sched)
    if DRY:
        log(step="refill-dry", would_add=added)
        return
    json.dump(q, open(Q, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(dash, open(DASH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(step="refill-done", added=added)


if __name__ == "__main__":
    main()
