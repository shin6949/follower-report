from report import parse_env, parse_targets, parse_yt, render, split_message, webhook_url

assert parse_env('# 주석\nA=1\n B = "x=y" \n\nbad') == {"A": "1", "B": "x=y"}

head = '"pageHeaderViewModel":'
assert parse_yt('"8.1M subscribers"' + head + '"content":"7.13K subscribers"') == 7130
assert parse_yt(head + '"15.1M subscribers"') == 15_100_000
assert parse_yt(head + '"999 subscribers"') == 999

# 콤마 구분·공백·@·중복·빈 항목은 정리하고, URL을 붙여넣은 것처럼 형식이 틀린 항목은 bad로 분리
cfg = {"X_USER": " @KBSnews, kbs_2 ,,KBSnews, https://x.com/bad ", "YT_HANDLE": ""}
assert parse_targets(cfg) == ([("x", "KBSnews"), ("x", "kbs_2")], ["트위터 https://x.com/bad"])
# 키가 없거나 비면 해당 플랫폼은 건너뜀, 한글 핸들은 허용, X 이름은 15자까지
assert parse_targets({"YT_HANDLE": "뉴스,a b", "X_USER": "a" * 16}) == (
    [("youtube", "뉴스")], ["트위터 " + "a" * 16, "유튜브 a b"])
assert parse_targets({}) == ([], [])

cols = [("x", "A"), ("youtube", "B")]


def snap(t, x, y):
    return [(t, "x", "A", x), (t, "youtube", "B", y)]


out = render(
    snap("2026-10-24 11:00:01", 7828, 7070)
    + snap("2026-10-24 12:00:01", None, 7090)
    + snap("2026-10-24 13:00:01", 7868, 7090)
    + snap("2026-10-24 14:00:01", 7860, 7100),
    cols)
assert out == ("증가량\n[트위터 A / 유튜브 B]\n11:00 7,828/7,070\n12:00 -/7,090(+20)\n"
               "13:00 7,868(+40)/7,090(±0)\n14:00 7,860(-8)/7,100(+10)"), out

# 하루 중 계정을 추가하면 그 전 시간은 '-', 뺀 계정의 기록은 표에서 제외
out = render(snap("2026-10-24 11:00:01", 1, 2) + snap("2026-10-24 12:00:01", 1, 2)
             + [("2026-10-24 12:00:01", "x", "C", 9), ("2026-10-24 10:00:01", "x", "Z", 7)],
             cols + [("x", "C")])
assert out.splitlines()[1:] == ["[트위터 A / 유튜브 B / 트위터 C]", "11:00 1/2/-", "12:00 1(±0)/2(±0)/9"], out

# 오늘 한 번도 조회되지 않은 계정과 형식 오류 항목은 끝에 알림
out = render(snap("2026-10-24 11:00:01", 1, None), cols, ["트위터 a/b"])
assert out.splitlines()[-2:] == ["⚠ 계정 이름 형식이 잘못되어 건너뜀: 트위터 a/b",
                                 "⚠ 오늘 조회된 값이 없음(계정 이름 확인): 유튜브 B"], out

# 길면 줄 단위로 나누고 제목·열 이름은 조각마다 반복
body = [f"{i:02d}:00 " + "9" * 30 for i in range(24)]
parts = split_message("\n".join(["증가량", "[헤더]"] + body), limit=300)
assert len(parts) > 1 and all(len(p) <= 300 and p.startswith("증가량\n[헤더]\n") for p in parts), parts
assert [l for p in parts for l in p.split("\n")[2:]] == body
# 스레드 ID는 선택: 없으면 쿼리에서 빠진다
assert webhook_url("https://h/1/t", "") == "https://h/1/t?wait=true"
assert webhook_url("https://h/1/t", "99") == "https://h/1/t?wait=true&thread_id=99"
print("ok")
