#!/usr/bin/env python3
"""
RITME - Network Rhythm Monitor
Dashboard CLI untuk monitoring jaringan via ping + speedtest.
Dioptimalkan untuk layar kecil (mobile/portrait terminal).
"""

import subprocess
import time
import re
import sys
import os
import signal
from datetime import datetime
from collections import deque

# ─── KONFIGURASI ───────────────────────────────────────────
TARGET = "8.8.8.8"
PING_INTERVAL = 1  # detik
SPARKLINE_WIDTH = 50  # jumlah bar di sparkline
LOG_MAX = 5  # jumlah log transisi yang ditampilkan

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

# ─── BOX DRAWING (unicode) ─────────────────────────────────
# Fallback ke ASCII kalau terminal nggak support
BOX_TL = "┌"; BOX_TR = "┐"; BOX_BL = "└"; BOX_BR = "┘"
BOX_H = "─"; BOX_V = "│"; BOX_ML = "├"; BOX_MR = "┤"

# ─── STATE ──────────────────────────────────────────────────
sukses = 0
gagal = 0
latencies = deque(maxlen=100)  # simpan 100 latency terakhir
sparkline_data = deque(maxlen=SPARKLINE_WIDTH)  # True=online, False=offline
log_transisi = deque(maxlen=LOG_MAX)
status_terakhir = None
waktu_perubahan = datetime.now()
session_start = datetime.now()
speedtest_result = None
running = True

# ─── FUNCTIONS ──────────────────────────────────────────────

def strip_ansi(text):
    """Hapus ANSI escape codes untuk hitung panjang visual."""
    return re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)


def ping_once(target):
    """Ping satu kali, return (online: bool, latency_ms: float or None)."""
    try:
        hasil = subprocess.run(
            ["ping", "-c", "1", "-W", "1", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if hasil.returncode == 0:
            out = hasil.stdout
            # Format 1: "time=X.XX ms" (Linux/standar)
            match = re.search(r"time=(\d+\.?\d*)\s*ms", out)
            if match:
                return True, float(match.group(1))
            # Format 2: macOS summary "min/avg/max/stddev = X/Y/Z/W ms"
            match = re.search(r"min/avg/max/stddev\s*=\s*[\d.]+/([\d.]+)/", out)
            if match:
                return True, float(match.group(1))
            # Format 3: macOS dengan -W "packets out of wait time" tapi masih received
            if "1 packets received" in out and "0.0% packet loss" in out:
                match = re.search(r"=\s*([\d.]+)/([\d.]+)/", out)
                if match:
                    return True, float(match.group(2))
            return True, None
        return False, None
    except Exception:
        return False, None


def run_speedtest():
    """Jalankan speedtest-cli, return dict hasil atau None."""
    try:
        hasil = subprocess.run(
            ["speedtest-cli", "--simple"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
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
    """Format detik ke string ringkas."""
    if detik < 60:
        return f"{detik:.0f}dtk"
    elif detik < 3600:
        m, s = divmod(detik, 60)
        return f"{int(m)}m{int(s)}dtk"
    else:
        h, r = divmod(detik, 3600)
        m, s = divmod(r, 60)
        return f"{int(h)}j{int(m)}m"


def get_terminal_width():
    """Dapatkan lebar terminal, fallback ke 60."""
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 60


def latency_histogram(lat_list, bar_max=8):
    """Buat histogram mini ASCII dari distribusi latency."""
    if not lat_list:
        return f"{C_DIM}tidak ada data{C_RESET}"
    
    buckets = [
        ("<10", 0, 10), ("10-25", 10, 25), ("25-50", 25, 50),
        ("50-100", 50, 100), ("100-200", 100, 200),
        ("200-500", 200, 500), ("500+", 500, float("inf")),
    ]
    
    counts = [0] * len(buckets)
    for lat in lat_list:
        for i, (_, lo, hi) in enumerate(buckets):
            if lo <= lat < hi:
                counts[i] += 1
                break
    
    m = max(counts) if max(counts) > 0 else 1
    result = ""
    for i, (label, _, _) in enumerate(buckets):
        n = int(counts[i] / m * bar_max) if counts[i] > 0 else 0
        if counts[i] > 0:
            color = C_GREEN if i < 2 else (C_YELLOW if i < 4 else C_RED)
            result += f"{color}{'▄' * max(1, n)}{C_RESET}"
        else:
            result += f"{C_DIM}▄{C_RESET}"
        result += " "
    return result.strip()


def format_latency(lat_ms):
    """Format latency dengan warna."""
    if lat_ms is None:
        return f"{C_DIM}---{C_RESET}"
    if lat_ms < 30:
        color = C_GREEN
    elif lat_ms < 80:
        color = C_YELLOW
    else:
        color = C_RED
    return f"{color}{lat_ms:.0f}ms{C_RESET}"


def pad_visible(text, target_width):
    """Pad string ke target_width berdasarkan lebar visual (tanpa ANSI)."""
    visible_len = len(strip_ansi(text))
    padding = target_width - visible_len
    return text + (" " * max(0, padding))


def draw_dashboard():
    """Render seluruh dashboard dalam satu frame, adaptif lebar terminal."""
    now = datetime.now()
    elapsed = (now - session_start).total_seconds()
    total = sukses + gagal
    loss_pct = (gagal / total * 100) if total > 0 else 0
    uptime_pct = 100 - loss_pct

    # Adaptive width: min 42, max deteksi terminal
    tw = get_terminal_width()
    BOX_W = max(42, min(tw, 50))  # batasi 42-50 kolom
    SPARK_W = BOX_W - 4  # sparkline mengikuti lebar box

    # Rata-rata, min, max latency
    lat_list = [l for l in latencies if l is not None]
    avg_lat = sum(lat_list) / len(lat_list) if lat_list else 0
    min_lat = min(lat_list) if lat_list else 0
    max_lat = max(lat_list) if lat_list else 0
    jitter = (
        sum(abs(lat_list[i] - lat_list[i - 1]) for i in range(1, len(lat_list)))
        / (len(lat_list) - 1)
        if len(lat_list) > 1
        else 0
    )

    # Status warna
    if status_terakhir is None:
        status_color, status_icon, status_text = C_YELLOW, "⏳", "MENUNGGU"
    elif status_terakhir:
        status_color, status_icon, status_text = C_GREEN, "●", "ONLINE"
    else:
        status_color, status_icon, status_text = C_RED, "●", "TERPUTUS"

    def box_row(left_text):
        inner_w = BOX_W - 2
        return f"{BOX_V}{pad_visible(left_text, inner_w)}{BOX_V}"

    out = []
    out.append(C_CLEAR_SCREEN + C_HOME + C_HIDE_CURSOR)

    # ─── HEADER ───
    inner = BOX_W - 2
    out.append(f"{C_BOLD}{C_CYAN}{BOX_TL}{BOX_H * inner}{BOX_TR}{C_RESET}")
    
    # Baris status
    status_line = f" {status_color}{status_icon} {status_text}{C_RESET}"
    title = f"{C_BOLD}RITME{C_RESET}"
    time_str = f"{C_DIM}{now.strftime('%H:%M:%S')}{C_RESET}"
    out.append(box_row(f"{status_line}  {title}  {time_str}"))
    
    # Baris uptime + target
    out.append(box_row(
        f" {TARGET}  Uptime:{C_GREEN}{uptime_pct:.1f}%{C_RESET}"
    ))

    # ─── SEPARATOR ───
    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # ─── SPARKLINE ───
    spark = ""
    for online in sparkline_data:
        spark += f"{C_BG_GREEN} {C_RESET}" if online else f"{C_BG_RED} {C_RESET}"
    spark += f"{C_DIM}·{C_RESET}" * max(0, SPARK_W - len(sparkline_data))
    out.append(box_row(f" {spark}"))

    # Status durasi
    dur = format_durasi((now - waktu_perubahan).total_seconds())
    if status_terakhir:
        out.append(box_row(f" {C_GREEN}▸ ONLINE{C_RESET} — {C_BOLD}{dur}{C_RESET}"))
    elif status_terakhir is False:
        out.append(box_row(f" {C_RED}▸ TERPUTUS{C_RESET} — {C_BOLD}{dur}{C_RESET}"))
    else:
        out.append(box_row(f" {C_YELLOW}▸ Menunggu data...{C_RESET}"))

    # ─── SEPARATOR ───
    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # ─── STATISTIK ───
    out.append(box_row(f" {C_BOLD}PING{C_RESET}  OK:{C_GREEN}{sukses}{C_RESET} RTO:{C_RED}{gagal}{C_RESET} Loss:{loss_pct:.1f}%"))
    out.append(box_row(
        f"   Avg:{format_latency(avg_lat)} Min:{format_latency(min_lat)} "
        f"Max:{format_latency(max_lat)} Jitter:{jitter:.0f}ms"
    ))

    # ─── HISTOGRAM LATENCY ───
    if lat_list:
        hist = latency_histogram(lat_list, bar_max=min(8, (BOX_W - 20) // 2))
        out.append(box_row(f"   Dist: {hist}"))

    out.append(box_row(f"   Sesi: {format_durasi(elapsed)}"))

    # ─── SEPARATOR ───
    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # ─── SPEEDTEST ───
    out.append(box_row(f" {C_BOLD}SPEEDTEST{C_RESET} ({C_YELLOW}S{C_RESET}=test)"))
    if speedtest_result:
        st = speedtest_result
        out.append(box_row(
            f"   {C_GREEN}↓{C_RESET}{st.get('download','?')} "
            f"{C_CYAN}↑{C_RESET}{st.get('upload','?')} "
            f"Ping:{st.get('ping','?')}ms"
        ))
    else:
        out.append(box_row(f"   {C_DIM}Tekan S untuk mulai{C_RESET}"))

    # ─── SEPARATOR ───
    out.append(f"{BOX_ML}{BOX_H * inner}{BOX_MR}")

    # ─── LOG ───
    out.append(box_row(f" {C_BOLD}LOG TRANSISI{C_RESET}"))
    if log_transisi:
        for entry in list(log_transisi)[-4:]:  # max 4 baris
            out.append(box_row(f" {entry}"))
    else:
        out.append(box_row(f"   {C_DIM}stabil — belum ada transisi{C_RESET}"))

    # ─── FOOTER ───
    out.append(f"{C_CYAN}{BOX_BL}{BOX_H * inner}{BOX_BR}{C_RESET}")
    out.append(f" {C_DIM}Q=Keluar S=Speedtest R=Reset P=Pause{C_RESET}")

    # Flush
    sys.stdout.write("\n".join(out))
    sys.stdout.flush()


def draw_final_summary():
    """Tampilkan ringkasan akhir setelah user quit."""
    os.system("clear" if os.name == "posix" else "cls")
    print(C_SHOW_CURSOR, end="")

    total = sukses + gagal
    loss_pct = (gagal / total * 100) if total > 0 else 0
    elapsed = (datetime.now() - session_start).total_seconds()

    lat_list = [l for l in latencies if l is not None]
    avg_lat = sum(lat_list) / len(lat_list) if lat_list else 0
    min_lat = min(lat_list) if lat_list else 0
    max_lat = max(lat_list) if lat_list else 0

    print(f"{C_BOLD}{'═' * 50}{C_RESET}")
    print(f"{C_BOLD}  HASIL AKHIR MONITORING RITME{C_RESET}")
    print(f"{C_BOLD}{'═' * 50}{C_RESET}")
    print(f"  Target      : {TARGET}")
    print(f"  Durasi      : {format_durasi(elapsed)}")
    print(f"  Mulai       : {session_start.strftime('%H:%M:%S')}")
    print(f"  Selesai     : {datetime.now().strftime('%H:%M:%S')}")
    print(f"  {'─' * 46}")
    print(f"  Total Ping  : {total}")
    print(f"  Sukses      : {C_GREEN}{sukses}{C_RESET}")
    print(f"  Gagal (RTO) : {C_RED}{gagal}{C_RESET}")
    print(f"  Packet Loss : {loss_pct:.1f}%")
    print(f"  {'─' * 46}")
    print(f"  Avg Latency : {avg_lat:.1f} ms")
    print(f"  Min Latency : {min_lat:.1f} ms")
    print(f"  Max Latency : {max_lat:.1f} ms")
    print(f"  {'─' * 46}")

    # Transisi timeline
    if log_transisi:
        print(f"  {C_BOLD}Timeline Putus-Nyambung:{C_RESET}")
        for entry in log_transisi:
            print(f"    {entry}")
    print(f"{C_BOLD}{'═' * 50}{C_RESET}")


def on_resize(signum, frame):
    """Handle terminal resize."""
    draw_dashboard()


# ─── SIGNAL HANDLERS ────────────────────────────────────────
signal.signal(signal.SIGWINCH, on_resize)  # Terminal resize

# ─── MAIN LOOP ──────────────────────────────────────────────
def main():
    global sukses, gagal, status_terakhir, waktu_perubahan
    global speedtest_result, running

    # Clear screen & hide cursor
    os.system("clear" if os.name == "posix" else "cls")
    sys.stdout.write(C_HIDE_CURSOR)
    sys.stdout.flush()

    # Setup terminal untuk raw input (non-blocking key detection)
    try:
        import tty
        import termios

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        has_raw = True
    except (ImportError, termios.error):
        has_raw = False

    last_ping_time = 0

    try:
        while running:
            now = time.time()

            # ─── CHECK KEYBOARD INPUT ───
            if has_raw:
                import select

                while select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    if ch.lower() == "q":
                        running = False
                    elif ch.lower() == "s":
                        # Speedtest mode
                        sys.stdout.write(C_SHOW_CURSOR)
                        sys.stdout.flush()
                        draw_dashboard()
                        # Overlay speedtest status
                        sys.stdout.write(
                            f"\033[15;2H{C_BOLD}{C_YELLOW}  ⏳ Menjalankan speedtest...{C_RESET}"
                        )
                        sys.stdout.flush()
                        speedtest_result = run_speedtest()
                        sys.stdout.write(C_HIDE_CURSOR)
                        sys.stdout.flush()
                    elif ch.lower() == "r":
                        # Reset stats
                        sukses = 0
                        gagal = 0
                        latencies.clear()
                        sparkline_data.clear()
                        log_transisi.clear()
                        status_terakhir = None
                        waktu_perubahan = datetime.now()
                        speedtest_result = None
                    elif ch.lower() == "p":
                        # Pause - tunggu key lagi
                        sys.stdout.write(
                            f"\033[20;0H{C_BOLD}{C_YELLOW}  ⏸  PAUSED - Press any key to resume...{C_RESET}"
                        )
                        sys.stdout.flush()
                        sys.stdin.read(1)

            # ─── PING ───
            if now - last_ping_time >= PING_INTERVAL:
                last_ping_time = now
                is_online, latency = ping_once(TARGET)

                if is_online:
                    sukses += 1
                else:
                    gagal += 1

                sparkline_data.append(is_online)
                if latency is not None:
                    latencies.append(latency)

                # Track transisi
                waktu_sekarang = datetime.now()
                if status_terakhir is not None and status_terakhir != is_online:
                    durasi = (waktu_sekarang - waktu_perubahan).total_seconds()
                    kondisi = (
                        f"{C_GREEN}NYAMBUNG{C_RESET}"
                        if is_online
                        else f"{C_RED}TERPUTUS{C_RESET}"
                    )
                    log_transisi.append(
                        f"{waktu_sekarang.strftime('%H:%M:%S')}  {kondisi}  "
                        f"(sebelumnya {format_durasi(durasi)})"
                    )
                    waktu_perubahan = waktu_sekarang

                status_terakhir = is_online

            # ─── REDRAW ───
            draw_dashboard()
            time.sleep(0.1)

    except KeyboardInterrupt:
        pass
    finally:
        if has_raw:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    draw_final_summary()


if __name__ == "__main__":
    main()
