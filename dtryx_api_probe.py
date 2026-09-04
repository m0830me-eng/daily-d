import json
import socket
import ssl
import time
from urllib.parse import urlencode

import requests

HOST = "api.dtryx.com"
PORTS = [443, 30443]

BASE = "https://api.dtryx.com:30443"
PATH = "/dtryx/cms/common/update/update-rule"

COMMON = {
    "BrandCd": "dtryx",
    "ProgramCd": "dtryx-android",
    "CinemaCd": "all",
    "WorkGuID": "663F899B-A70C-4989-A48C-ACD100F2AD40",
    "EngVerYn": "N",
}

HEADERS = {
    "User-Agent": "AndroidDTRYX/1.0.5 (com.dtryx.cinema)",
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Connection": "close",
}

def section(title):
    print("\n" + "=" * 78, flush=True)
    print(title, flush=True)
    print("=" * 78, flush=True)

def dns_probe():
    section("DNS")
    try:
        infos = socket.getaddrinfo(HOST, None)
        ips = sorted({x[4][0] for x in infos})
        print(f"{HOST} -> {', '.join(ips)}", flush=True)
        return ips
    except Exception as e:
        print(f"DNS ERROR: {type(e).__name__}: {e}", flush=True)
        return []

def tcp_probe(port):
    section(f"TCP {HOST}:{port}")
    t0 = time.monotonic()
    try:
        with socket.create_connection((HOST, port), timeout=6):
            print(f"TCP OK | {time.monotonic() - t0:.2f}s", flush=True)
            return True
    except Exception as e:
        print(f"TCP ERROR | {type(e).__name__}: {e} | {time.monotonic() - t0:.2f}s", flush=True)
        return False

def tls_probe(port):
    section(f"TLS {HOST}:{port}")
    t0 = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, port), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=HOST) as ssock:
                cert = ssock.getpeercert()
                print(f"TLS OK | version={ssock.version()} | {time.monotonic() - t0:.2f}s", flush=True)
                print(f"subject={cert.get('subject')}", flush=True)
                print(f"issuer={cert.get('issuer')}", flush=True)
                return True
    except Exception as e:
        print(f"TLS ERROR | {type(e).__name__}: {e} | {time.monotonic() - t0:.2f}s", flush=True)
        return False

def print_response(r, elapsed):
    print(f"HTTP {r.status_code} | {elapsed:.2f}s | bytes={len(r.content)}", flush=True)
    print(f"content-type={r.headers.get('content-type', '-')}", flush=True)
    print(f"server={r.headers.get('server', '-')}", flush=True)
    print(f"location={r.headers.get('location', '-')}", flush=True)
    text = r.text
    print("BODY BEGIN", flush=True)
    print(text[:6000], flush=True)
    print("BODY END", flush=True)

    # JSON이면 key 구조도 보여준다.
    try:
        obj = r.json()
        print("JSON TYPE:", type(obj).__name__, flush=True)
        if isinstance(obj, dict):
            print("JSON KEYS:", sorted(obj.keys()), flush=True)
    except Exception:
        pass

def http_probe(local_version):
    params = dict(COMMON)
    params["LocalVersion"] = local_version
    url = f"{BASE}{PATH}?{urlencode(params)}"

    section(f"HTTPS UPDATE API | LocalVersion={local_version}")
    print(url, flush=True)

    for attempt in range(1, 3):
        t0 = time.monotonic()
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=(6, 10),
                allow_redirects=False,
                verify=True,
            )
            elapsed = time.monotonic() - t0
            print(f"TRY {attempt}", flush=True)
            print_response(r, elapsed)
            return
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"TRY {attempt}: {type(e).__name__}: {e} | {elapsed:.2f}s", flush=True)
            time.sleep(0.5)

def root_probe():
    for url in [
        "https://api.dtryx.com:30443/",
        "https://api.dtryx.com/",
    ]:
        section(f"ROOT {url}")
        t0 = time.monotonic()
        try:
            r = requests.get(url, headers=HEADERS, timeout=(6, 8), allow_redirects=False)
            print_response(r, time.monotonic() - t0)
        except Exception as e:
            print(f"{type(e).__name__}: {e} | {time.monotonic() - t0:.2f}s", flush=True)

def main():
    print("DTRYX app API connectivity probe", flush=True)
    dns_probe()

    for port in PORTS:
        ok = tcp_probe(port)
        if ok:
            tls_probe(port)

    root_probe()

    # 현재 앱 버전과 아주 낮은 버전 둘 다 호출.
    # 낮은 버전은 업데이트 규칙 응답에 URL/설정이 더 많이 나올 가능성이 있다.
    http_probe("1.0.5")
    http_probe("0.0.0")

    print("\n=== END ===", flush=True)

if __name__ == "__main__":
    main()
