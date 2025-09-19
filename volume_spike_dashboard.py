import requests
from datetime import datetime, timedelta, time
import pytz
from collections import defaultdict
import wcwidth
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ====== CONFIG ======
API_KEY = "5a0f5c6147a2bd7c832d63a6252f0c01-041561ca55b1549327e8c00f3d645f13"
ACCOUNT_ID = "101-004-37091392-001"
BASE_URL = "https://api-fxpractice.oanda.com/v3"

INSTRUMENTS = {
    "XAUUSD": "XAU_USD",
    "NAS100": "NAS100_USD",
    "US30": "US30_USD"
}

THRESHOLD_MULTIPLIER = 1.4

TELEGRAM_BOT_TOKEN = "7860254495:AAG2s2X6M30XDWSHyGGqg2aJmn0xbtg_DfQ"
TELEGRAM_CHAT_ID = "7598801380"

IST = pytz.timezone("Asia/Kolkata")
UTC = pytz.utc
headers = {"Authorization": f"Bearer {API_KEY}"}

# ====== AUTO-REFRESH EVERY 5 MINUTES ======
st_autorefresh(interval=300_000, limit=None, key="volume-refresh")

# ====== TELEGRAM ALERT ======
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        st.error(f"Telegram alert failed: {e}")

# ====== OANDA DATA FETCH ======
def fetch_candles(instrument_code, from_time, to_time):
    now_utc = datetime.now(UTC)
    from_time = min(from_time, now_utc)
    to_time = min(to_time, now_utc)

    params = {
        "granularity": "M15",
        "price": "M",
        "from": from_time.isoformat(),
        "to": to_time.isoformat()
    }
    url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/instruments/{instrument_code}/candles"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
    except Exception as e:
        st.error(f"❌ Network error for {instrument_code}: {e}")
        return []

    if resp.status_code != 200:
        st.error(f"❌ Failed to fetch {instrument_code} data: {resp.text}")
        return []
    return resp.json().get("candles", [])

# ====== UTILITIES ======
def get_time_bucket(dt_ist):
    bucket_start = dt_ist.replace(minute=0, second=0, microsecond=0)
    bucket_end = bucket_start + timedelta(hours=1)
    return f"{bucket_start.strftime('%I:%M %p')}–{bucket_end.strftime('%I:%M %p')}"

def compute_bucket_averages(code):
    bucket_volumes = defaultdict(list)
    today_ist = datetime.now(IST).date()
    now_utc = datetime.now(UTC)

    for i in range(21):
        day_ist = today_ist - timedelta(days=i)
        start_ist = IST.localize(datetime.combine(day_ist, time(0, 0)))
        end_ist = IST.localize(datetime.combine(day_ist + timedelta(days=1), time(0, 0)))

        start_utc = start_ist.astimezone(UTC)
        end_utc = min(end_ist.astimezone(UTC), now_utc)

        candles = fetch_candles(code, start_utc, end_utc)
        for c in candles:
            t_utc = datetime.strptime(c["time"], "%Y-%m-%dT%H:%M:%S.%f000Z")
            t_ist = t_utc.replace(tzinfo=UTC).astimezone(IST)
            bucket = get_time_bucket(t_ist)
            bucket_volumes[bucket].append(c["volume"])

    return {b: (sum(vs) / len(vs)) for b, vs in bucket_volumes.items() if vs}

def get_sentiment(candle):
    o = float(candle["mid"]["o"])
    c = float(candle["mid"]["c"])
    return "🟩" if c > o else "🟥" if c < o else "▪️"

def pad_display(s, width):
    pad_len = width - sum(wcwidth.wcwidth(ch) for ch in s)
    return s + " " * max(pad_len, 0)

def get_spike_bar(multiplier):
    if multiplier < 1.2:
        return pad_display("", 5)
    bars = int((multiplier - 1.2) * 5)
    bar_str = "┃" * max(1, min(bars, 5))
    return pad_display(bar_str, 5)

# ====== CORE PROCESS ======
def process_instrument(name, code):
    bucket_avg = compute_bucket_averages(code)
    now_utc = datetime.now(UTC)
    from_time = now_utc - timedelta(minutes=15 * 30)
    candles = fetch_candles(code, from_time, now_utc)
    if not candles:
        return [], []

    rows = []
    spikes_last_two = []
    last_two_candles = candles[-2:] if len(candles) >= 2 else candles

    for c in candles:
        t_utc = datetime.strptime(c["time"], "%Y-%m-%dT%H:%M:%S.%f000Z")
        t_ist = t_utc.replace(tzinfo=UTC).astimezone(IST)

        bucket = get_time_bucket(t_ist)
        vol = c["volume"]
        avg = bucket_avg.get(bucket, 0)
        threshold = avg * THRESHOLD_MULTIPLIER if avg else 0
        over = (threshold > 0 and vol > threshold)
        mult = (vol / threshold) if over and threshold > 0 else 0

        spike_diff = f"🔺{vol - int(threshold)}" if over else ""
        strength = get_spike_bar(mult) if over else pad_display("", 5)
        sentiment = get_sentiment(c)

        rows.append([
            t_ist.strftime("%Y-%m-%d %I:%M %p"),
            bucket,
            f"{float(c['mid']['o']):.1f}",
            f"{float(c['mid']['h']):.1f}",
            f"{float(c['mid']['l']):.1f}",
            f"{float(c['mid']['c']):.1f}",
            vol,
            spike_diff,
            strength,
            sentiment
        ])

        if c in last_two_candles and over:
            spikes_last_two.append(
                f"{name} {t_ist.strftime('%I:%M %p')} spike — Vol {vol} ({spike_diff}) {sentiment}"
            )

    return rows, spikes_last_two

def render_table_streamlit(name, rows):
    st.subheader(f"{name} — Last 30 × 15‑min candles")
    st.dataframe(rows, width='stretch', height=800)



# ====== DASHBOARD EXECUTION ======
def run_volume_check():
    all_spike_msgs = []
    for name, code in INSTRUMENTS.items():
        rows, spikes = process_instrument(name, code)
        if rows:
            render_table_streamlit(name, rows)
        if spikes:
            all_spike_msgs.extend(spikes)

    if all_spike_msgs:
        msg = "⚡ Volume Spikes Detected:\n" + "\n".join(all_spike_msgs)
        st.warning(msg)
        send_telegram_alert(msg)
    else:
        st.info("ℹ️ No spikes in the last two candles.")

# ====== MAIN ======
st.set_page_config(page_title="Volume Spike Dashboard", layout="wide")
st.title("📊 Volume Anomaly Detector")
run_volume_check()
