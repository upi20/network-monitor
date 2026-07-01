# 🎛️ ritme — Network Rhythm Monitor

Dashboard CLI real-time untuk monitoring stabilitas jaringan via ping + speedtest.
Dioptimalkan untuk **layar kecil** (mobile/portrait terminal via SSH).

```
┌────────────────────────────────────────────────┐
│ ● ONLINE  RITME  21:28:48                      │
│ 8.8.8.8  Uptime:84.9%                          │
├────────────────────────────────────────────────┤
│ ██ █ ██ ██ ································   │
│ ▸ ONLINE — 10dtk                               │
├────────────────────────────────────────────────┤
│ PING  OK:343 RTO:61 Loss:15.1%                 │
│   Avg:365ms Min:19ms Max:992ms Jitter:323ms    │
│   Dist: ▄ ▄▄▄▄▄▄▄ ▄ ▄ ▄ ▄▄▄ ▄▄▄▄▄▄▄▄         │
│   Sesi: 7m12dtk                                │
├────────────────────────────────────────────────┤
│ SPEEDTEST (S=test)                             │
│   ↓79 Mbit/s  ↑145 Mbit/s  Ping:18ms           │
├────────────────────────────────────────────────┤
│ LOG TRANSISI                                   │
│ 21:28:21  TERPUTUS  (sebelumnya 7dtk)          │
│ 21:28:21  NYAMBUNG  (sebelumnya 1dtk)          │
│ 21:28:37  TERPUTUS  (sebelumnya 16dtk)         │
│ 21:28:38  NYAMBUNG  (sebelumnya 1dtk)          │
└────────────────────────────────────────────────┘
 Q=Keluar S=Speedtest R=Reset P=Pause
```

## ✨ Fitur

| Fitur | Deskripsi |
|---|---|
| 🟢🔴 **Status Live** | Indikator ON/OFF dengan warna + uptime % |
| 📊 **Sparkline** | 50 detik terakhir — hijau (online) / merah (RTO) |
| 📈 **Histogram Latency** | Distribusi ping dalam 7 bucket warna |
| ⚡ **Speedtest** | On-demand dengan `S` — download, upload, ping |
| 📝 **Log Transisi** | 4 event putus-nyambung terakhir |
| 🧹 **Fixed Dashboard** | Redraw in-place, tanpa scroll |
| 📱 **Adaptive Width** | 42–50 kolom otomatis |

## 🚀 Quick Start

### Prasyarat
- Python 3.6+
- `speedtest-cli` (opsional, untuk fitur speedtest)

```bash
pip install speedtest-cli
```

### Menjalankan

```bash
python3 ritme.py
```

### Shortcut Keyboard

| Tombol | Fungsi |
|---|---|
| `S` | Jalankan speedtest |
| `R` | Reset semua statistik |
| `P` | Pause/resume monitoring |
| `Q` | Keluar + tampilkan ringkasan |
| `Ctrl+C` | Keluar paksa |

### Kustomisasi Target

Edit konstanta di `ritme.py`:

```python
TARGET = "8.8.8.8"          # IP/host target ping
PING_INTERVAL = 1           # detik antar ping
SPARKLINE_WIDTH = 50        # lebar sparkline
```

## 📱 Pakai di Mobile

1. Install Termius / JuiceSSH di HP
2. SSH ke server/mac yang terhubung jaringan
3. Jalankan `python3 ritme.py`
4. Dashboard akan render sempurna di layar kecil

## 📄 Lisensi

MIT
