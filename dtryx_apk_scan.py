import os
import re
import sys
import zipfile
from pathlib import Path

SRC = Path("download/com.dtryx.cinema.xapk")
OUT = Path("apk_extract")

URL_RE = re.compile(rb'https?://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]{6,}')
HOST_RE = re.compile(rb'(?i)(?:[A-Za-z0-9-]+\.)+(?:dtryx|dtryxapp|dtryxapi)\.[A-Za-z]{2,}')
ASCII_RE = re.compile(rb'[\x20-\x7e]{5,}')

KEYWORDS = [
    b"dtryx",
    b"CinemaCd",
    b"BrandCd",
    b"TimeTable",
    b"reserve",
    b"movie",
    b"cinema",
    b"schedule",
    b"screen",
    b"showseq",
    b"play",
    b"api",
    b".do",
    b"ajax",
    b"graphql",
    b"retrofit",
    b"okhttp",
]

def safe_decode(b: bytes) -> str:
    return b.decode("utf-8", "ignore").strip()

def extract_zip(src: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        z.extractall(dst)

def recursive_unpack():
    if OUT.exists():
        import shutil
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    if not SRC.exists():
        print(f"ERROR: missing {SRC}", flush=True)
        sys.exit(2)

    print(f"INPUT: {SRC} ({SRC.stat().st_size:,} bytes)", flush=True)

    top = OUT / "xapk"
    try:
        extract_zip(SRC, top)
        print("XAPK unzip: OK", flush=True)
    except Exception as e:
        print(f"XAPK unzip failed: {type(e).__name__}: {e}", flush=True)
        # 혹시 실제로 APK가 직접 내려온 경우
        top.mkdir(parents=True, exist_ok=True)
        (top / "base.apk").write_bytes(SRC.read_bytes())

    apks = list(top.rglob("*.apk"))
    print(f"APK files: {len(apks)}", flush=True)

    for i, apk in enumerate(apks, 1):
        dst = OUT / f"apk_{i:02d}_{apk.stem}"
        try:
            extract_zip(apk, dst)
            print(f"APK unzip OK: {apk.name} -> {dst}", flush=True)
        except Exception as e:
            print(f"APK unzip FAIL: {apk}: {e}", flush=True)

def scan_bytes(path: Path, data: bytes, hits: dict):
    for m in URL_RE.findall(data):
        s = safe_decode(m)
        if s:
            hits.setdefault("urls", set()).add(s)

    for m in HOST_RE.findall(data):
        s = safe_decode(m)
        if s:
            hits.setdefault("hosts", set()).add(s)

    low = data.lower()
    if any(k.lower() in low for k in KEYWORDS):
        for sraw in ASCII_RE.findall(data):
            sl = sraw.lower()
            if any(k.lower() in sl for k in KEYWORDS):
                s = safe_decode(sraw)
                if 5 <= len(s) <= 500:
                    hits.setdefault("strings", set()).add(s)

def main():
    recursive_unpack()

    hits = {"urls": set(), "hosts": set(), "strings": set()}
    scanned = 0

    for p in OUT.rglob("*"):
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > 40_000_000:
                continue
            data = p.read_bytes()
        except Exception:
            continue
        scanned += 1
        scan_bytes(p, data, hits)

    print("\n" + "="*80, flush=True)
    print(f"SCANNED FILES: {scanned}", flush=True)
    print("="*80, flush=True)

    print("\n### URLS containing dtryx / api / reserve / cinema / movie ###", flush=True)
    urls = sorted(hits["urls"])
    filtered_urls = [
        u for u in urls
        if any(k in u.lower() for k in [
            "dtryx", "api", "reserve", "cinema", "movie",
            "schedule", "screen", "ticket", "show", "play"
        ])
    ]
    for u in filtered_urls[:500]:
        print(u, flush=True)

    print("\n### DTRYX-LIKE HOSTS ###", flush=True)
    for h in sorted(hits["hosts"]):
        print(h, flush=True)

    print("\n### INTERESTING STRINGS ###", flush=True)
    interesting = []
    for s in hits["strings"]:
        low = s.lower()
        if (
            "dtryx" in low
            or "cinemacd" in low
            or "brandcd" in low
            or "timetable" in low
            or "/api/" in low
            or "/reserve/" in low
            or "movie.do" in low
            or "cinema/" in low
            or "schedule" in low
        ):
            interesting.append(s)

    for s in sorted(set(interesting))[:1200]:
        print(s, flush=True)

    print("\n### SUMMARY ###", flush=True)
    print(f"URLs total={len(urls)} filtered={len(filtered_urls)}", flush=True)
    print(f"Hosts={len(hits['hosts'])}", flush=True)
    print(f"Interesting strings={len(interesting)}", flush=True)

    # Artifact text file도 남김
    report = Path("dtryx_apk_scan_report.txt")
    with report.open("w", encoding="utf-8") as f:
        f.write("### FILTERED URLS\n")
        for u in filtered_urls:
            f.write(u + "\n")
        f.write("\n### HOSTS\n")
        for h in sorted(hits["hosts"]):
            f.write(h + "\n")
        f.write("\n### INTERESTING STRINGS\n")
        for s in sorted(set(interesting)):
            f.write(s + "\n")

    print(f"REPORT: {report}", flush=True)

if __name__ == "__main__":
    main()
