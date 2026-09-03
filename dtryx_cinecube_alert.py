import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from playwright.async_api import async_playwright, Response, Page, TimeoutError as PlaywrightTimeoutError

# ============================================================
# DTRYX - 씨네큐브 특별회차 알리미 (TEST)
# ============================================================
# GitHub Secrets
#   DISCORD_WEBHOOK_URL
#   DISCORD_USER_ID
#
# 씨네큐브
#   BrandCd  = cinecube
#   CinemaCd = 000003
#
# 감시
#   - 오늘 ~ +20일 (총 21일)
#   - "-#이벤트명"이 붙은 모든 특별회차
#   - 최초 발견 시 상영준비중이면 1차 알림
#   - 같은 회차가 예매가능으로 바뀌면 2차 알림
#   - 매시 00/30분에는 21일 전체 동시스캔
# ============================================================

THEATER_NAME = "씨네큐브"
BRAND_CD = "cinecube"
CINEMA_CD = "000003"
CGID = "FE8EF4D2-F22D-4802-A39A-D58F23A29C1E"

BASE_URL = "https://www.dtryx.com/reserve/movie.do"
DAYS = int(os.getenv("DTRYX_DAYS", "21"))
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "290"))
NORMAL_INTERVAL = float(os.getenv("NORMAL_INTERVAL", "7"))
CONCURRENCY = int(os.getenv("DTRYX_CONCURRENCY", "2"))
PAGE_TIMEOUT_MS = int(os.getenv("PAGE_TIMEOUT_MS", "12000"))
POST_LOAD_WAIT_MS = int(os.getenv("POST_LOAD_WAIT_MS", "3500"))
DEBUG = os.getenv("DTRYX_DEBUG", "1") == "1"

STATE_PATH = Path(os.getenv("STATE_PATH", "state/dtryx_state.json"))
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "debug_dtryx"))

WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID", "").strip()

KST = ZoneInfo("Asia/Seoul")

EVENT_RE = re.compile(r"-#\s*([^\n\r|,/<>]+)", re.I)
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
DATE_RE = re.compile(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}")

PENDING_WORDS = (
    "상영준비중", "예매준비중", "준비중", "예매오픈예정",
    "판매대기", "판매준비", "booking unavailable"
)
OPEN_WORDS = (
    "예매가능", "예매하기", "인원/좌석 선택", "좌석선택",
    "booking", "reserve"
)

MOVIE_KEYS = (
    "MovieNm", "MovieName", "MovieNmKor", "MovieTitle", "MovNm",
    "movieNm", "movieName", "movieTitle", "title", "MOVIE_NM"
)
DATE_KEYS = (
    "PlaySDT", "PlayYMD", "PlayYmd", "playDate", "playYmd",
    "ShowDate", "showDate", "date", "PLAY_YMD"
)
TIME_KEYS = (
    "PlayStartTime", "PlayStartTm", "StartTime", "ShowTime",
    "playStartTime", "playStartTm", "startTime", "showTime", "PLAY_START_TM"
)
SCREEN_KEYS = (
    "ScreenNm", "ScreenName", "TheaterNm", "HallNm",
    "screenNm", "screenName", "theaterNm", "hallNm", "SCREEN_NM"
)
SCREEN_CD_KEYS = (
    "ScreenCd", "screenCd", "SCREEN_CD"
)
MOVIE_CD_KEYS = (
    "MovieCd", "movieCd", "MOVIE_CD"
)
SHOW_SEQ_KEYS = (
    "ShowSeq", "showSeq", "PlaySeq", "playSeq", "SHOW_SEQ"
)
SALE_KEYS = (
    "AllowSaleYn", "SaleYn", "BookingYn", "ReserveYn", "IsSale",
    "allowSaleYn", "saleYn", "bookingYn", "reserveYn", "isSale",
    "SALE_YN", "BOOKING_YN"
)
SEAT_KEYS = (
    "RemainSeatCnt", "SeatRemainCnt", "RemainCnt", "AvailableSeatCnt",
    "remainSeatCnt", "seatRemainCnt", "remainCnt", "availableSeatCnt"
)


def kst_now() -> datetime:
    return datetime.now(KST)


def log(msg: str) -> None:
    print(f"[{kst_now():%Y-%m-%d %H:%M:%S KST}] {msg}", flush=True)


def clean_text(v: Any) -> str:
    if v is None:
        return ""
    s = str(v)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def first_value(d: Dict[str, Any], keys: Iterable[str]) -> str:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return clean_text(d[k])
    return ""


def normalize_date(raw: str, fallback: str) -> str:
    raw = clean_text(raw)
    m = DATE_RE.search(raw)
    if not m:
        # YYYYMMDD
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 8 and digits[:4].startswith("20"):
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return fallback
    nums = re.findall(r"\d+", m.group(0))
    return f"{int(nums[0]):04d}-{int(nums[1]):02d}-{int(nums[2]):02d}"


def normalize_time(raw: str) -> str:
    raw = clean_text(raw)
    m = TIME_RE.search(raw)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 4:
        hh, mm = digits[-4:-2], digits[-2:]
        if 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59:
            return f"{hh}:{mm}"
    return raw[:8]


def extract_event(text: str) -> str:
    m = EVENT_RE.search(text or "")
    return clean_text(m.group(1)) if m else ""


def strip_screen(text: str) -> str:
    text = clean_text(text)
    if "-#" in text:
        text = text.split("-#", 1)[0]
    # "2관/2D" -> "2관", "S관/2D" -> "S관"
    if "/" in text:
        text = text.split("/", 1)[0]
    return text.strip()


def bool_sale_value(raw: str) -> Optional[bool]:
    s = clean_text(raw).lower()
    if not s:
        return None
    if s in {"y", "yes", "true", "1", "가능", "open"}:
        return True
    if s in {"n", "no", "false", "0", "불가", "close", "closed"}:
        return False
    return None


def infer_status(record: Dict[str, Any], blob: str) -> str:
    sale_raw = first_value(record, SALE_KEYS)
    b = bool_sale_value(sale_raw)
    if b is True:
        return "open"
    if b is False:
        return "pending"

    low = blob.lower()
    if any(w.lower() in low for w in PENDING_WORDS):
        return "pending"
    if any(w.lower() in low for w in OPEN_WORDS):
        return "open"

    # 특별회차 레코드 자체가 시간표 API에서 내려왔지만 판매여부 필드가 없다면
    # 오탐 방지를 위해 pending으로 시작한다.
    return "pending"


def booking_url(item: Dict[str, str]) -> str:
    params = {
        "cgid": CGID,
        "TimeTableBrandCd": BRAND_CD,
        "TimeTableCinemaCd": CINEMA_CD,
    }
    if item.get("movie_cd"):
        params["TimeTableMovieCd"] = item["movie_cd"]
    if item.get("date"):
        params["TimeTablePlaySDT"] = item["date"]
    if item.get("screen_cd"):
        params["TimeTableScreenCd"] = item["screen_cd"]
    if item.get("show_seq"):
        params["TimeTableShowSeq"] = item["show_seq"]
    return f"{BASE_URL}?{urlencode(params)}"


def item_key(item: Dict[str, str]) -> str:
    raw = "|".join([
        CINEMA_CD,
        item.get("date", ""),
        item.get("time", ""),
        item.get("movie", ""),
        item.get("screen", ""),
        item.get("event", ""),
        item.get("show_seq", ""),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"items": {}, "version": 1}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}, "version": 1}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def discord_send(item: Dict[str, str], status: str) -> bool:
    if not WEBHOOK:
        log("⚠️ DISCORD_WEBHOOK_URL 없음 - Discord 전송 생략")
        return False

    mention = f"<@{DISCORD_USER_ID}>\n\n" if DISCORD_USER_ID else ""
    if status == "open":
        headline = f"🚨 {THEATER_NAME} · {item['event']} · 예매오픈"
    else:
        headline = f"⏳ {THEATER_NAME} · {item['event']} · 상영준비중"

    url = booking_url(item)
    ticket = f"[🎟 {item['time']} · {item['movie']} · {item['screen']}]({url})"
    content = (
        f"{mention}"
        f"{headline}\n"
        f"📅 {item['date']}\n"
        f"{ticket}"
    )

    try:
        r = requests.post(WEBHOOK, json={"content": content}, timeout=10)
        if 200 <= r.status_code < 300:
            return True
        log(f"❌ Discord HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"❌ Discord 전송 오류: {type(e).__name__}: {e}")
    return False


def walk_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_dicts(v)


def dict_blob(d: Dict[str, Any]) -> str:
    try:
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(d)


def parse_record(d: Dict[str, Any], target_date: str, source_url: str) -> Optional[Dict[str, str]]:
    blob = dict_blob(d)
    event = extract_event(blob)
    if not event:
        return None

    movie = first_value(d, MOVIE_KEYS)
    date = normalize_date(first_value(d, DATE_KEYS), target_date)
    tm = normalize_time(first_value(d, TIME_KEYS))
    screen_raw = first_value(d, SCREEN_KEYS)

    # 필드명이 예상과 달라 문자열 전체에서 보조 추출
    if not tm:
        m = TIME_RE.search(blob)
        tm = f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""
    if not screen_raw:
        # 이벤트 태그 앞의 "2관/2D" 같은 문자열 탐색
        m = re.search(r'([A-Za-z0-9가-힣]+관)\s*/[^"\']*?-#', blob)
        if m:
            screen_raw = m.group(1)

    screen = strip_screen(screen_raw)

    if not movie:
        # 레코드에 title/name 계열이 하나뿐일 때 보조
        for k, v in d.items():
            if isinstance(v, str) and k.lower() in {"name", "nm"}:
                vv = clean_text(v)
                if vv and "-#" not in vv and len(vv) < 100:
                    movie = vv
                    break

    if not (movie and tm and screen):
        if DEBUG:
            log(
                f"🧪 특별태그 레코드 발견했지만 핵심필드 부족 | "
                f"date={target_date} movie={movie!r} time={tm!r} screen={screen!r} "
                f"| endpoint={source_url}"
            )
        return None

    item = {
        "movie": movie,
        "date": date,
        "time": tm,
        "screen": screen,
        "event": event,
        "movie_cd": first_value(d, MOVIE_CD_KEYS),
        "screen_cd": first_value(d, SCREEN_CD_KEYS),
        "show_seq": first_value(d, SHOW_SEQ_KEYS),
        "status": infer_status(d, blob),
        "source_url": source_url,
    }
    return item


def dedupe_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for it in items:
        k = item_key(it)
        # 같은 키가 pending/open 둘 다 잡히면 open 우선
        if k not in out or (out[k]["status"] != "open" and it["status"] == "open"):
            out[k] = it
    return list(out.values())


async def collect_json_response(resp: Response, target_date: str, bucket: List[Tuple[str, Any]]) -> None:
    try:
        ctype = (resp.headers.get("content-type") or "").lower()
        url_low = resp.url.lower()

        # 시간표 관련 가능성이 큰 XHR/fetch 응답 위주
        likely = any(x in url_low for x in (
            "reserve", "movie", "time", "schedule", "show", "screen",
            "cinema", "ticket", "play", "ajax"
        ))
        if not likely and "json" not in ctype:
            return

        body = await resp.text()
        if not body or len(body) > 3_000_000:
            return

        # 특별 태그가 있으면 무조건 보존
        if "-#" in body or "씨네토크" in body or "#GV" in body or "#무대인사" in body:
            try:
                data = json.loads(body)
            except Exception:
                data = {"__raw_text__": body}
            bucket.append((resp.url, data))
            if DEBUG:
                log(f"🎯 특별태그 응답 포착 | {target_date} | {resp.url}")
            return

        # DEBUG에서 JSON endpoint 목록을 일부 확인
        if "json" in ctype:
            try:
                data = json.loads(body)
            except Exception:
                return
            bucket.append((resp.url, data))
    except Exception:
        return


async def fallback_dom(page: Page, target_date: str) -> List[Dict[str, str]]:
    """JSON 파싱이 안 될 때 DOM 텍스트에서 최소한의 특별회차를 찾는 보조 루틴."""
    out: List[Dict[str, str]] = []
    try:
        body = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return out

    if "-#" not in body and "씨네토크" not in body and "#GV" not in body and "#무대인사" not in body:
        return out

    lines = [re.sub(r"\s+", " ", x).strip() for x in body.splitlines() if x.strip()]
    for i, line in enumerate(lines):
        event = extract_event(line)
        if not event:
            continue

        window = " | ".join(lines[max(0, i - 8): min(len(lines), i + 9)])
        tm_m = TIME_RE.search(window)
        screen_m = re.search(r"([A-Za-z0-9가-힣]+관)(?:/[^| ]+)?(?:-#|$)", window)
        if not (tm_m and screen_m):
            continue

        tm = f"{int(tm_m.group(1)):02d}:{tm_m.group(2)}"
        screen = screen_m.group(1)

        # 주변 텍스트에서 영화명 후보
        movie = ""
        for cand in reversed(lines[max(0, i - 8):i]):
            if TIME_RE.search(cand):
                continue
            if "관/" in cand or "-#" in cand:
                continue
            if cand in {"영화", "상영시간", "전체", "예매하기"}:
                continue
            if 1 < len(cand) < 80:
                movie = cand
                break

        if movie:
            status_blob = window.lower()
            status = "pending" if any(w.lower() in status_blob for w in PENDING_WORDS) else "open"
            out.append({
                "movie": movie,
                "date": target_date,
                "time": tm,
                "screen": screen,
                "event": event,
                "movie_cd": "",
                "screen_cd": "",
                "show_seq": "",
                "status": status,
                "source_url": page.url,
            })

    if DEBUG and out:
        log(f"🧩 DOM fallback {len(out)}건 | {target_date}")
    return out


def build_url(target_date: str) -> str:
    # DTRYX가 지원하는 회차 딥링크 파라미터 구조 사용
    params = {
        "cgid": CGID,
        "TimeTableBrandCd": BRAND_CD,
        "TimeTableCinemaCd": CINEMA_CD,
        "TimeTablePlaySDT": target_date,
    }
    return f"{BASE_URL}?{urlencode(params)}"


async def scan_date(context, target_date: str, sem: asyncio.Semaphore) -> Tuple[str, List[Dict[str, str]], int]:
    """
    DTRYX는 GitHub Actions에서 DOMContentLoaded가 오래 걸릴 수 있다.
    따라서 페이지 전체 로딩 완료를 기다리지 않고 HTTP 응답이 시작되는 commit까지만 기다린 뒤
    XHR/fetch 응답을 수집한다. 이미지/폰트/영상은 차단해 서버·브라우저 부하를 낮춘다.
    """
    async with sem:
        page = await context.new_page()
        captured: List[Tuple[str, Any]] = []
        tasks: List[asyncio.Task] = []
        nav_started = False

        async def route_handler(route):
            try:
                if route.request.resource_type in {"image", "font", "media"}:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                pass

        await page.route("**/*", route_handler)

        def on_response(resp: Response):
            tasks.append(asyncio.create_task(
                collect_json_response(resp, target_date, captured)
            ))

        page.on("response", on_response)

        try:
            try:
                await page.goto(
                    build_url(target_date),
                    wait_until="commit",
                    timeout=PAGE_TIMEOUT_MS,
                )
                nav_started = True
            except PlaywrightTimeoutError:
                # DTRYX는 일부 환경에서 navigation 완료 신호가 늦다.
                # 이미 응답/XHR이 진행 중일 수 있으므로 즉시 실패시키지 않는다.
                current = page.url or ""
                if "dtryx.com" in current:
                    nav_started = True
                    if DEBUG:
                        log(f"↻ {target_date} 페이지 로딩 지연 - 응답 수집 계속")
                else:
                    raise

            # networkidle/domcontentloaded를 기다리지 않는다.
            await page.wait_for_timeout(POST_LOAD_WAIT_MS)

            # response 이벤트에서 만든 task가 더 생길 수 있어 한 번 더 짧게 대기
            await page.wait_for_timeout(500)
            if tasks:
                await asyncio.gather(*list(tasks), return_exceptions=True)

            items: List[Dict[str, str]] = []
            for source_url, data in captured:
                for d in walk_dicts(data):
                    it = parse_record(d, target_date, source_url)
                    if it:
                        items.append(it)

            if not items:
                items.extend(await fallback_dom(page, target_date))

            if DEBUG:
                # endpoint 진단: JSON 응답을 전혀 못 잡았을 때만 짧은 로그
                if not captured:
                    log(f"🧪 {target_date} JSON/XHR 후보 0건 | page={page.url}")

                # 특별 태그는 보이는데 파싱 실패한 경우 HTML 저장
                try:
                    html = await page.content()
                    if ("-#" in html or "씨네토크" in html or "#GV" in html or "#무대인사" in html) and not items:
                        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                        (DEBUG_DIR / f"{target_date}.html").write_text(html, encoding="utf-8")
                        log(f"🧪 파싱 실패 HTML 저장: {DEBUG_DIR}/{target_date}.html")
                except Exception:
                    pass

            return target_date, dedupe_items(items), 0

        except PlaywrightTimeoutError as e:
            log(f"❌ {target_date} 접속 timeout | {PAGE_TIMEOUT_MS/1000:.0f}초")
            return target_date, [], 1
        except Exception as e:
            log(f"❌ {target_date} 조회 오류: {type(e).__name__}: {e}")
            return target_date, [], 1
        finally:
            try:
                await page.close()
            except Exception:
                pass


def process_items(items: List[Dict[str, str]], state: Dict[str, Any]) -> Tuple[int, Dict[str, int]]:
    alerts = 0
    stats = {"special": 0, "pending": 0, "open": 0}
    now = kst_now().isoformat(timespec="seconds")

    for item in items:
        stats["special"] += 1
        stats[item["status"]] += 1

        k = item_key(item)
        prev = state["items"].get(k)
        current_status = item["status"]

        should_alert = False
        # 새 특별회차: 현재 상태 그대로 1회 알림
        if prev is None:
            should_alert = True
        # 상영준비중 -> 예매오픈: 두 번째 알림
        elif prev.get("status") != "open" and current_status == "open":
            should_alert = True

        if should_alert:
            ok = discord_send(item, current_status)
            if ok or not WEBHOOK:
                alerts += 1
                log(
                    f"{'🚨 예매오픈' if current_status == 'open' else '⏳ 상영준비중'} | "
                    f"{item['date']} {item['time']} | {item['movie']} | "
                    f"{item['screen']} | #{item['event']}"
                )

        state["items"][k] = {
            **item,
            "status": current_status,
            "last_seen": now,
            "first_seen": prev.get("first_seen", now) if prev else now,
        }

    # 너무 오래된 state 정리
    cutoff = (kst_now().date() - timedelta(days=2)).isoformat()
    for k in list(state["items"].keys()):
        d = state["items"][k].get("date", "")
        if d and d < cutoff:
            del state["items"][k]

    save_state(state)
    return alerts, stats


async def full_scan(context, dates: List[str], label: str, state: Dict[str, Any]) -> None:
    t0 = time.monotonic()
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(scan_date(context, d, sem) for d in dates))

    all_items: List[Dict[str, str]] = []
    errors = 0
    for _, items, err in results:
        all_items.extend(items)
        errors += err

    all_items = dedupe_items(all_items)
    alerts, stats = process_items(all_items, state)

    elapsed = time.monotonic() - t0
    log(
        f"🔎 {label} 동시스캔 완료 | {len(dates)}일 | "
        f"특별회차 {stats['special']} | 상영준비중 {stats['pending']} | "
        f"예매가능 {stats['open']} | 알림 {alerts} | 오류 {errors} | {elapsed:.1f}초"
    )


async def main() -> None:
    state = load_state()
    today = kst_now().date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(DAYS)]

    log(
        f"🎬 DTRYX 씨네큐브 감시 시작 | CinemaCd={CINEMA_CD} | "
        f"오늘~+{DAYS-1}일 | 실행 {RUN_SECONDS}초"
    )

    started = time.monotonic()
    idx = 0
    last_halfhour_token = ""
    heartbeat = time.monotonic()
    cycle_ok = 0
    cycle_err = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 900},
        )
        context.set_default_timeout(7000)

        # 시작 직후 1회 전체 확인: 최대 2개 페이지만 병렬로 열어 DTRYX 과부하를 피한다.
        await full_scan(context, dates, "초기 21일", state)

        while time.monotonic() - started < RUN_SECONDS:
            now = kst_now()

            # 00 / 30분에 해당 프로세스당 한 번 전체 동시스캔
            if now.minute in (0, 30):
                token = now.strftime("%Y-%m-%d %H:%M")
                if token != last_halfhour_token:
                    await full_scan(context, dates, f"{now:%H:%M} 00/30", state)
                    last_halfhour_token = token

            # 평소에는 날짜 하나씩 순차 확인
            d = dates[idx % len(dates)]
            idx += 1
            sem = asyncio.Semaphore(1)
            _, items, err = await scan_date(context, d, sem)
            cycle_err += err
            if not err:
                cycle_ok += 1

            alerts, stats = process_items(items, state)

            if DEBUG and (items or alerts):
                log(
                    f"👀 {d} 순차확인 | 특별회차 {stats['special']} | "
                    f"준비중 {stats['pending']} | 예매가능 {stats['open']} | 알림 {alerts}"
                )

            if time.monotonic() - heartbeat >= 60:
                log(
                    f"💚 정상 감시중 | 최근 1분 날짜조회 성공 {cycle_ok} | "
                    f"오류 {cycle_err} | 다음 날짜 {dates[idx % len(dates)]}"
                )
                heartbeat = time.monotonic()
                cycle_ok = 0
                cycle_err = 0

            await asyncio.sleep(NORMAL_INTERVAL)

        await context.close()
        await browser.close()

    log("✅ 이번 GitHub Actions 감시 실행 종료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"❌ FATAL: {type(e).__name__}: {e}")
        raise
