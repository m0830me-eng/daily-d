import socket
import time
from urllib.parse import urlencode

import requests

CGID = "FE8EF4D2-F22D-4802-A39A-D58F23A29C1E"
BRAND_CD = "cinecube"
CINEMA_CD = "000003"

PARAMS_CINEMA = {
    "cgid": CGID,
    "BrandCd": BRAND_CD,
    "CinemaCd": CINEMA_CD,
}

PARAMS_RESERVE = {
    "cgid": CGID,
    "TimeTableBrandCd": BRAND_CD,
    "TimeTableCinemaCd": CINEMA_CD,
}

URLS = [
    ("NON-WWW CINEMA", "https://dtryx.com/cinema/main.do?" + urlencode(PARAMS_CINEMA)),
    ("WWW CINEMA", "https://www.dtryx.com/cinema/main.do?" + urlencode(PARAMS_CINEMA)),
    ("NON-WWW RESERVE", "https://dtryx.com/reserve/movie.do?" + urlencode(PARAMS_RESERVE)),
    ("WWW RESERVE", "https://www.dtryx.com/reserve/movie.do?" + urlencode(PARAMS_RESERVE)),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "close",
}

def check_dns(host: str):
    print(f"\n=== DNS {host} ===", flush=True)
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({x[4][0] for x in infos})
        print("OK:", ", ".join(ips), flush=True)
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", flush=True)

def check_url(name: str, url: str):
    print(f"\n=== {name} ===", flush=True)
    print(url, flush=True)

    for attempt in range(1, 3):
        t0 = time.monotonic()
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=(5, 8),
                allow_redirects=False,
            )
            elapsed = time.monotonic() - t0
            print(
                f"TRY {attempt}: HTTP {r.status_code} | {elapsed:.2f}s | "
                f"bytes={len(r.content)} | location={r.headers.get('location', '-')}",
                flush=True,
            )
            print(
                f"content-type={r.headers.get('content-type', '-')}",
                flush=True,
            )
            sample = r.text[:300].replace("\n", " ").replace("\r", " ")
            print(f"BODY: {sample}", flush=True)
            return
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(
                f"TRY {attempt}: ERROR {type(e).__name__}: {e} | {elapsed:.2f}s",
                flush=True,
            )
            time.sleep(0.5)

def main():
    print("DTRYX GitHub Actions direct-access probe", flush=True)
    print(f"BrandCd={BRAND_CD} CinemaCd={CINEMA_CD}", flush=True)

    check_dns("dtryx.com")
    check_dns("www.dtryx.com")

    for name, url in URLS:
        check_url(name, url)

    print("\n=== END ===", flush=True)

if __name__ == "__main__":
    main()
