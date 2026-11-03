import json

LIMITS = "limits.json"


def read_warn_cap(path=LIMITS):
    """경고 상한 읽기. (상한, 문제) 반환. 파일 없음 = (None, None). 이상하면 안전하게 (0, 문제) = 오늘 새 글 없음."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, RecursionError) as e:
        return 0, f"읽기 실패({type(e).__name__})"
    if not isinstance(data, dict) or set(data) - {"warn_cap"}:
        return 0, "warn_cap 칸 하나만 있어야 함"
    if "warn_cap" not in data:
        return None, None
    w = data["warn_cap"]
    if type(w) is not int or w < 0:
        return 0, f"warn_cap은 0 이상 정수여야 함({w!r})"
    return w, None
