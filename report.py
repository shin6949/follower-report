"""X / YouTube 팔로워 수를 조회해 sqlite에 쌓고, 오늘 기록 표를 Discord로 보낸다."""
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

CONFIG = Path(__file__).with_name("config.env")
DB = Path(__file__).with_name("followers.db")
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode()


def fetch_x(user):
    # shortcut: 비공식 서드파티(fxtwitter) 의존, 막히면 공식 X API로 교체
    # fxtwitter가 같은 계정에도 무작위로 404를 줘서(성공률 ~30%) 재시도
    # shortcut: 없는 계정도 같은 404라 재시도를 끝까지 한다(약 42초), 느려지면 연속 실패 계정은 재시도 생략
    for attempt in range(15):
        try:
            return int(json.loads(get(f"https://api.fxtwitter.com/{user}"))["user"]["followers"])
        except Exception:
            if attempt == 14:
                raise
            time.sleep(3)


def parse_yt(html):
    # 채널 헤더 이후 첫 "N subscribers"가 이 채널 값 (앞쪽엔 다른 채널 수치가 없음)
    start = html.find('"pageHeaderViewModel"')
    m = re.compile(r'([\d.,]+)([KMB]?) subscribers').search(html, max(start, 0))
    if start < 0 or not m:
        raise ValueError("subscriber count not found")
    return round(float(m[1].replace(",", "")) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m[2]])


def fetch_yt(handle):
    return parse_yt(get(f"https://www.youtube.com/@{quote(handle)}?hl=en"))


PLATFORMS = {  # 플랫폼: (config 키, 표시 이름, 계정 이름 형식, 조회 함수). 순서가 표의 열 순서
    "x": ("X_USER", "트위터", r"[A-Za-z0-9_]{1,15}", fetch_x),
    "youtube": ("YT_HANDLE", "유튜브", r"[\w.\-·]+", fetch_yt),
}


def safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"{fn.__name__}({', '.join(args)}) failed: {e}", file=sys.stderr)
        return None


def cell(cur, prev):
    if cur is None:
        return "-"
    if prev is None:
        return f"{cur:,}"
    d = cur - prev
    return f"{cur:,}({'±0' if d == 0 else f'{d:+,}'})"


def render(rows, cols, bad=()):
    by_time = {}  # 시각 -> {(플랫폼, 계정): 값}. 설정에서 빠진 계정의 기록은 건너뜀
    for t, p, a, n in rows:
        if (p, a) in cols:
            by_time.setdefault(t, {})[p, a] = n
    lines = ["증가량", "[" + " / ".join(f"{PLATFORMS[p][1]} {a}" for p, a in cols) + "]"]
    prev = {}  # 실패(-)한 시간은 건너뛰고 마지막 성공값과 비교
    for t, vals in by_time.items():
        lines.append(f"{t[11:16]} " + "/".join(cell(vals.get(c), prev.get(c)) for c in cols))
        prev.update({c: n for c, n in vals.items() if n is not None})
    dead = [f"{PLATFORMS[p][1]} {a}" for p, a in cols if all(v.get((p, a)) is None for v in by_time.values())]
    if bad:
        lines.append("⚠ 계정 이름 형식이 잘못되어 건너뜀: " + ", ".join(bad))
    if dead:
        lines.append("⚠ 오늘 조회된 값이 없음(계정 이름 확인): " + ", ".join(dead))
    return "\n".join(lines)


def split_message(text, limit=1900):
    # 디스코드 메시지는 2000자 제한이라 줄 단위로 나누고, 제목·열 이름 두 줄은 조각마다 반복
    # shortcut: 열 이름 줄 자체가 한도를 넘는 경우(계정 수십 개)는 미지원
    lines = text.split("\n")
    head, chunks = lines[:2], []
    for line in lines[2:]:
        if not chunks or len("\n".join(chunks[-1] + [line])) > limit:
            chunks.append(head + [line])
        else:
            chunks[-1].append(line)
    return ["\n".join(c) for c in chunks]


def parse_env(text):
    # shortcut: KEY=VALUE 한 줄씩만 지원(export·인라인 주석 없음), 필요해지면 python-dotenv
    pairs = (l.partition("=") for l in text.splitlines() if "=" in l and not l.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip("\"'") for k, _, v in pairs}


def parse_targets(cfg):
    # 콤마로 나눈 계정 목록. 빈 값·빈 항목·중복은 무시하고, 형식이 틀린 항목은 bad로 따로 돌려준다
    cols, bad = [], []
    for p, (key, label, pattern, _) in PLATFORMS.items():
        for name in dict.fromkeys(s.strip().lstrip("@") for s in cfg.get(key, "").split(",")):
            if re.fullmatch(pattern, name):
                cols.append((p, name))
            elif name:
                bad.append(f"{label} {name}")
    return cols, bad


def webhook_url(webhook, thread_id):
    # 스레드 ID가 없으면 웹훅이 만들어진 채널에 바로 보낸다
    return f"{webhook}?wait=true" + (f"&thread_id={thread_id}" if thread_id else "")


def send(content, webhook, thread_id):
    req = urllib.request.Request(
        webhook_url(webhook, thread_id),
        data=json.dumps({"content": content}).encode(),
        headers={**UA, "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=30).close()


def main():
    cfg = parse_env(CONFIG.read_text())
    webhook, thread_id = cfg["DISCORD_WEBHOOK"], cfg.get("DISCORD_THREAD_ID", "")  # 웹훅이 없으면 수집 전에 바로 실패
    cols, bad = parse_targets(cfg)
    if not cols:
        sys.exit(f"조회할 계정이 없습니다. X_USER, YT_HANDLE 중 하나는 채워야 합니다. 형식 오류: {bad or '없음'}")
    now = datetime.now()
    counts = [safe(PLATFORMS[p][3], a) for p, a in cols]
    with sqlite3.connect(DB) as db:
        # 예전 snapshots 테이블(플랫폼당 한 컬럼)은 건드리지 않고 계정당 한 행인 새 테이블을 쓴다
        db.execute("CREATE TABLE IF NOT EXISTS follower_counts (checked_at TEXT NOT NULL, platform TEXT NOT NULL, account TEXT NOT NULL, count INTEGER)")
        db.executemany("INSERT INTO follower_counts VALUES (?, ?, ?, ?)",
                       [(now.isoformat(" ", "seconds"), p, a, n) for (p, a), n in zip(cols, counts)])
        day = now.date().isoformat()
        rows = db.execute("SELECT checked_at, platform, account, count FROM follower_counts WHERE checked_at LIKE ? ORDER BY checked_at", (day + "%",)).fetchall()
    for part in split_message(render(rows, cols, bad)):
        send(part, webhook, thread_id)
    print(f"{now:%H:%M} " + " ".join(f"{p}:{a}={n}" for (p, a), n in zip(cols, counts)))


if __name__ == "__main__":
    main()
