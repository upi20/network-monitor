#!/usr/bin/env python3
"""
RITME — Network Rhythm Monitor
Dashboard CLI untuk monitoring koneksi via ping + speedtest.
Adaptif: mobile (min 42 cols) sampai laptop (full terminal width).
Stateless & memory-safe untuk dijalankan 24 jam nonstop.
"""

import subprocess
import time
import re
import sys
import os
import signal
import threading
import gc
from datetime import datetime
from collections import deque

# ─── KONFIGURASI ───────────────────────────────────────────
TARGET = "8.8.8.8"
PING_INTERVAL = 1                 # detik antar ping
LOG_MAX = 200                     # simpan 200 transisi terakhir (~aman 24 jam)
LATENCY_BUFFER = 500              # simpan 500 latency terakhir (cukup untuk histogram akurat)
SPARKLINE_DEFAULT = 80            # sparkline fallback kalau terminal kecil
GC_INTERVAL = 3600                # gc colektor setiap ~1 jam ping

# ─── ANSI COLORS ────────────────────────────────────────────
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_MAGENTA = "\033[95m"
C_BG_GREEN = "\033[42m"
C_BG_RED = "\033[41m"
C_BG_YELLOW = "\033[43m\033[30m"
C_HIDE_CURSOR = "\033[?25l"
C_SHOW_CURSOR = "\033[?25h"
C_CLEAR_SCREEN = "\033[2J"
C_HOME = "\033[H"

# ─── BOX DRAWING ───────────────────────────────────────────
BOX_TL = "┌"; BOX_TR = "┐"; BOX_BL = "└"; BOX_BR = "┘"
BOX_H = "─"; BOX_V = "│"; BOX_ML = "├"; BOX_MR = "┤"

# ─── STATE (semua bounded deque → no memory leak) ───────────
sukses = 0
gagal = 0
latencies = deque(maxlen=LATENCY_BUFFER)
sparkline_data = deque(maxlen=SPARKLINE_DEFAULT)
log_transisi = deque(maxlen=LOG_MAX)
status_terakhir = None
waktu_perubahan = datetime.now()
session_start = datetime.now()
speedtest_result = None
speedtest_pending = False
speedtest_error = None
speedtest_lock = threading.Lock()
running = True
ping_counter = 0
_redraw_lock = threading.Lock()  # cegah recursive SIGWINCH redraw
dirty = True                     # redraw on next loop
last_redraw = 0                  # timestamp redraw terakhir

# ─── FUNCTIONS ──────────────────────────────────────────────

def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def ping_once(target):
    try:
        hasil = subprocess.run(
            ["ping", "-c", "1", "-W", "1", target],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        if hasil.returncode == 0:
            out = hasil.stdout
            m = re.search(r"time=(\d+\.?\d*)\s*ms", out)
            if m: return True, float(m.group(1))
            m = re.search(r"min/avg/max/stddev\s*=\s*[\d.]+/([\d.]+)/", out)
            if m: return True, float(m.group(1))
            if "1 packets received" in out and "0.0% packet loss" in out:
                m = re.search(r"=\s*([\d.]+)/([\d.]+)/", out)
                if m: return True, float(m.group(2))
            return True, None
        return False, None
    except Exception:
        return False, None


def run_speedtest():
    try:
        hasil = subprocess.run(
            ["speedtest-cli", "--simple"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=60,
        )
        if hasil.returncode == 0:
            data = {}
            for line in hasil.stdout.strip().split("\n"):
                if "Ping:" in line:
                    data["ping"] = line.split(":")[1].strip().split()[0]
                if "Download:" in line:
                    data["download"] = line.split(":")[1].strip()
                if "Upload:" in line:
                    data["upload"] = line.split(":")[1].strip()
            return data
    except Exception:
        pass
    return None


def format_durasi(detik):
    if detik < 0: return "0dtk"
    if detik < 60: return f"{detik:.0f}dtk"
    elif detik < 3600:
        m, s = divmod(int(detik), 60); return f"{m}m{s}dtk"
    elif detik < 86400:
        h, r = divmod(int(detik), 3600); m, s = divmod(r, 60); return f"{h}j{m}m"
    else:
        d, r = divmod(int(detik), 86400); h, m = divmod(r, 3600); return f"{d}h{h}j"


def get_terminal_size():
    try:
        sz = os.get_terminal_size(); return sz.columns, sz.lines
    except Exception:
        return 80, 24


def latency_histogram(lat_list, bar_max=10):
    if not lat_list: return f"{C_DIM}--{C_RESET}"
    buckets = [
        ("<10", 0, 10), ("10-25", 10, 25), ("25-50", 25, 50),
        ("50-100", 50, 100), ("100-200", 100, 200),
        ("200-500", 200, 500), ("500+", 500, float("inf")),
    ]
    counts = [0] * len(buckets)
    for lat in lat_list:
        for i, (_, lo, hi) in enumerate(buckets):
            if lo <= lat < hi: counts[i] += 1; break
    m = max(counts) if max(counts) > 0 else 1
    result = ""
    for i, (_, _, _) in enumerate(buckets):
        n = int(counts[i] / m * bar_max) if counts[i] > 0 else 0
        if counts[i] > 0:
            color = C_GREEN if i < 2 else (C_YELLOW if i < 4 else C_RED)
            result += f"{color}{'▄' * max(1, n)}{C_RESET}"
        else:
            result += f"{C_DIM}▄{C_RESET}"
        result += " "
    return result.rstrip()


def format_latency(lat_ms):
    if lat_ms is None: return f"{C_DIM}---{C_RESET}"
    if lat_ms < 30: color = C_GREEN
    elif lat_ms < 80: color = C_YELLOW
    else: color = C_RED
    return f"{color}{lat_ms:.0f}ms{C_RESET}"


def pad_visible(text, target_width):
    return text + (" " * max(0, target_width - len(strip_ansi(text))))


# ─── DASHBOARD ──────────────────────────────────────────────

def draw_dashboard():
    now = datetime.now()
    elapsed = (now - session_start).total_seconds()
    total = sukses + gagal
    loss_pct = (gagal / total * 100) if total > 0 else 0
    uptime_pct = 100 - loss_pct

    tw, th = get_terminal_size()
    BOX_W = max(42, min(tw, 100))   # batasi max 100 biar ringan
    inner = BOX_W - 2
    SPARK_W = min(inner - 1, 100)   # sparkline max 100 kolom
    HIST_BARS = max(5, min(10, (inner - 16) // 2))
    FIXED = 16
    LOG_AREA = max(2, min(th - FIXED, 20))  # max 20 baris log

    lat_list = [l for l in latencies if l is not None]
    avg_lat = sum(lat_list) / len(lat_list) if lat_list else 0
    min_lat = min(lat_list) if lat_list else 0
    max_lat = max(lat_list) if lat_list else 0
    jitter = (
        sum(abs(lat_list[i] - lat_list[i - 1]) for i in range(1, len(lat_list)))
        / (len(lat_list) - 1) if len(lat_list) > 1 else 0
    )

    if status_terakhir is None:
        sc, si, st = C_YELLOW, "⏳", "MENUNGGU"
    elif status_terakhir:
        sc, si, st = C_GREEN, "●", "ONLINE"
    else:
        sc, si, st = C_RED, "●", "TERPUTUS"

    def box_row(t):
        return f"{BOX_V}{pad_visible(t, inner)}{BOX_V}"

    # \033[3J clears scrollback buffer juga — cegah scroll-up aneh
    sys.stdout.write("\033[3J" + C_CLEAR_SCREEN + C_HOME + C_HIDE_CURSOR)
    sys.stdout.flush()

    out = []

    # HEADER
    out.append(f"{C_BOLD}{C_CYAN}{BOX_TL}{BOX_H * inner}{BOX_TR}{C_RESET}")
    out.append(box_row(f" {sc}{si} {st}{C_RESET}   {C_BOLD}RITME{C_RESET}   {C_DIM}{now.strftime('%H:%M:%S')}{C_RESET}"))
    out.append(box_row(f" {TARGET}   Uptime:{C_GREEN}{uptime_pct:.1f}%{C_RESET}   Sesi:{C_DIM}{format_durasi(elapsed)}{C_RESET}"))

    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # SPARKLINE — iterasi deque langsung, tanpa copy ke list
    spark = ""
    n = len(sparkline_data)
    skip = max(0, n - SPARK_W)
    i = 0
    for online in sparkline_data:
        if i >= skip:
            spark += f"{C_BG_GREEN} {C_RESET}" if online else f"{C_BG_RED} {C_RESET}"
        i += 1
    spark += f"{C_DIM}·{C_RESET}" * max(0, SPARK_W - (n - skip))
    out.append(box_row(f" {spark}"))

    dur = format_durasi((now - waktu_perubahan).total_seconds())
    if status_terakhir:
        out.append(box_row(f" {C_GREEN}ONLINE{C_RESET} — {C_BOLD}{dur}{C_RESET}"))
    elif status_terakhir is False:
        out.append(box_row(f" {C_RED}TERPUTUS{C_RESET} — {C_BOLD}{dur}{C_RESET}"))
    else:
        out.append(box_row(f" {C_YELLOW}Menunggu data...{C_RESET}"))

    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # PING STATS
    out.append(box_row(f" {C_BOLD}PING{C_RESET}   {C_GREEN}OK:{sukses}{C_RESET}  {C_RED}RTO:{gagal}{C_RESET}  Loss:{loss_pct:.1f}%  Pkts:{total}"))
    out.append(box_row(f"   Avg:{format_latency(avg_lat)}  Min:{format_latency(min_lat)}  Max:{format_latency(max_lat)}  Jit:{jitter:.0f}ms"))
    if lat_list:
        out.append(box_row(f"   Dist: {latency_histogram(lat_list, bar_max=HIST_BARS)}"))
    out.append(box_row(f" {C_DIM}buf: {len(latencies)}/{LATENCY_BUFFER} lat · {len(log_transisi)}/{LOG_MAX} log · v4 laptop{C_RESET}"))

    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # SPEEDTEST
    out.append(box_row(f" {C_BOLD}SPEEDTEST{C_RESET}  {C_YELLOW}[S]{C_RESET}=test  {C_YELLOW}[R]{C_RESET}=reset  {C_YELLOW}[Q]{C_RESET}=quit"))
    if speedtest_pending:
        out.append(box_row(f"   {C_YELLOW}⏳ Menjalankan...{C_RESET}"))
    elif speedtest_error:
        out.append(box_row(f"   {C_RED}✗ {speedtest_error}{C_RESET}"))
    elif speedtest_result:
        st = speedtest_result
        dl = st.get('download','?').replace(' Mbit/s','M').replace(' Kbit/s','K')
        ul = st.get('upload','?').replace(' Mbit/s','M').replace(' Kbit/s','K')
        out.append(box_row(f"   {C_GREEN}↓{C_RESET}{dl}  {C_CYAN}↑{C_RESET}{ul}  Ping:{st.get('ping','?')}ms"))
    else:
        out.append(box_row(f"   {C_DIM}Tekan S untuk speedtest{C_RESET}"))

    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # LOG — dinamis sesuai tinggi terminal
    out.append(box_row(f" {C_BOLD}LOG TRANSISI{C_RESET}  ({len(log_transisi)} event)"))
    if log_transisi:
        # Iterasi dari ujung deque tanpa copy penuh
        entries = list(log_transisi)
        for entry in entries[-LOG_AREA:]:
            out.append(box_row(f" {entry}"))
    else:
        out.append(box_row(f"   {C_DIM}stabil{C_RESET}"))

    # FOOTER
    out.append(f"{C_CYAN}{BOX_BL}{BOX_H * inner}{BOX_BR}{C_RESET}")
    out.append(f" {C_DIM}Q=Keluar  S=Speedtest  R=Reset  P=Pause{C_RESET}")

    sys.stdout.write("\n".join(out))
    sys.stdout.flush()


def draw_final_summary():
    os.system("clear" if os.name == "posix" else "cls")
    print(C_SHOW_CURSOR, end="")
    total = sukses + gagal
    loss_pct = (gagal / total * 100) if total > 0 else 0
    elapsed = (datetime.now() - session_start).total_seconds()
    lat_list = [l for l in latencies if l is not None]
    avg_lat = sum(lat_list) / len(lat_list) if lat_list else 0
    min_lat = min(lat_list) if lat_list else 0
    max_lat = max(lat_list) if lat_list else 0
    tw, _ = get_terminal_size()
    w = max(42, min(tw, 120))
    line = "═" * (w - 2)
    print(f"{C_BOLD}{line}{C_RESET}")
    print(f"{C_BOLD}  HASIL AKHIR MONITORING RITME{C_RESET}")
    print(f"{C_BOLD}{line}{C_RESET}")
    print(f"  Target : {TARGET}     Durasi : {format_durasi(elapsed)}")
    print(f"  Mulai  : {session_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Akhir  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {'─' * (w - 4)}")
    print(f"  Total Ping  : {total}")
    print(f"  Sukses      : {C_GREEN}{sukses}{C_RESET}")
    print(f"  Gagal (RTO) : {C_RED}{gagal}{C_RESET}")
    print(f"  Packet Loss : {loss_pct:.2f}%")
    print(f"  {'─' * (w - 4)}")
    print(f"  Avg / Min / Max : {avg_lat:.1f} / {min_lat:.1f} / {max_lat:.1f} ms")
    print(f"  {'─' * (w - 4)}")
    if log_transisi:
        print(f"  {C_BOLD}Timeline ({len(log_transisi)} events):{C_RESET}")
        for entry in log_transisi:
            print(f"    {entry}")
    print(f"{C_BOLD}{line}{C_RESET}")


def on_resize(signum, frame):
    global dirty
    dirty = True  # trigger redraw dari main loop, jangan langsung


signal.signal(signal.SIGWINCH, on_resize)


# ─── SPEEDTEST THREAD ───────────────────────────────────────

def start_speedtest_thread():
    global speedtest_pending, speedtest_result, speedtest_error
    with speedtest_lock:
        if speedtest_pending: return
        speedtest_pending = True; speedtest_error = None
    def _run():
        global speedtest_pending, speedtest_result, speedtest_error
        try:
            r = run_speedtest()
            with speedtest_lock:
                speedtest_result = r; speedtest_error = None if r else "gagal"
                speedtest_pending = False
        except Exception as e:
            with speedtest_lock:
                speedtest_error = str(e)[:30]; speedtest_pending = False
    threading.Thread(target=_run, daemon=True).start()


# ─── MAIN ───────────────────────────────────────────────────

def main():
    global sukses, gagal, status_terakhir, waktu_perubahan
    global speedtest_result, speedtest_pending, speedtest_error, running
    global ping_counter, sparkline_data
    global dirty, last_redraw

    tw, _ = get_terminal_size()
    sparkline_data = deque(maxlen=max(46, min(tw - 4, 100)))

    os.system("clear" if os.name == "posix" else "cls")
    sys.stdout.write(C_HIDE_CURSOR); sys.stdout.flush()

    try:
        import tty, termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd); tty.setcbreak(fd)
        has_raw = True
    except (ImportError, termios.error):
        has_raw = False

    last_ping_time = 0

    try:
        while running:
            now = time.time()

            if has_raw:
                import select
                while select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    if ch.lower() == "q": running = False
                    elif ch.lower() == "s":
                        if not speedtest_pending: start_speedtest_thread()
                    elif ch.lower() == "r":
                        sukses = 0; gagal = 0; ping_counter = 0
                        latencies.clear(); sparkline_data.clear(); log_transisi.clear()
                        status_terakhir = None
                        waktu_perubahan = datetime.now()
                        with speedtest_lock:
                            speedtest_result = None; speedtest_error = None; speedtest_pending = False
                    elif ch.lower() == "p":
                        sys.stdout.write(
                            f"\033[{get_terminal_size()[1]};0H"
                            f"{C_BOLD}{C_YELLOW}  ⏸  PAUSED — tekan apa saja...{C_RESET}"
                        )
                        sys.stdout.flush(); sys.stdin.read(1)

            if now - last_ping_time >= PING_INTERVAL:
                last_ping_time = now
                is_online, latency = ping_once(TARGET)
                ping_counter += 1

                if is_online: sukses += 1
                else: gagal += 1

                sparkline_data.append(is_online)
                if latency is not None: latencies.append(latency)

                waktu_sekarang = datetime.now()
                if status_terakhir is not None and status_terakhir != is_online:
                    durasi = (waktu_sekarang - waktu_perubahan).total_seconds()
                    kondisi = f"{C_GREEN}NYAMBUNG{C_RESET}" if is_online else f"{C_RED}TERPUTUS{C_RESET}"
                    log_transisi.append(
                        f"{waktu_sekarang.strftime('%H:%M:%S')}  {kondisi}  "
                        f"(sebelumnya {format_durasi(durasi)})"
                    )
                    waktu_perubahan = waktu_sekarang
                status_terakhir = is_online

                if ping_counter % GC_INTERVAL == 0:
                    gc.collect()

                # Redraw hanya setelah ping baru — tidak setiap loop
                draw_dashboard()
                dirty = False
                last_redraw = now

            # Speedtest selesai? trigger redraw
            if not speedtest_pending and dirty:
                draw_dashboard()
                dirty = False
                last_redraw = now

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        if has_raw: termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    draw_final_summary()


if __name__ == "__main__":
    main()
