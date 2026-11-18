"""
MTC Bus ITS Portal – 15-Minute Health Monitor
Headless Playwright monitoring script for GitHub Actions & Local execution.
All credentials loaded from environment variables (GitHub Secrets or Local Env).
"""

import os
import sys
import json
import base64
import string
import time
import re
from datetime import datetime, timezone, timedelta

# Ensure unbuffered output
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import numpy as np
import cv2
import ddddocr
import requests
from playwright.sync_api import sync_playwright

# ── Load config ────────────────────────────────────────────────────────────────
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
if not os.path.exists(_CONFIG_PATH):
    # Fallback to local directory
    _CONFIG_PATH = "config.json"

with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    CONFIG = json.load(_f)

PIS_MAX_DOWN     = CONFIG["pis_display_max_down"]          # {"2L": 20, "4L": 6, "10L": 3}
FLEET_MIN_COMM   = CONFIG["fleet_dashboard_min_communicating"]  # 2500

# ── Credentials from environment (GitHub Secrets or defaults) ─────────────────
USERNAME  = os.getenv("MTC_USERNAME", "").strip()
PASSWORD  = os.getenv("MTC_PASSWORD", "").strip()

# ── IST helper ────────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


# ── Alert via ntfy.sh ─────────────────────────────────────────────────────────
def send_alert(message: str):
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        print("[WARN] NTFY_TOPIC secret not set. Skipping mobile push alert.")
        return
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic.strip()}",
            data=message.encode("utf-8"),
            headers={
                "Title": "MTC ITS Portal Alert",
                "Priority": "urgent",
                "Tags": "warning,bus"
            },
            timeout=15
        )
        print(f"[ALERT] ntfy.sh Status: {resp.status_code}")
    except Exception as e:
        print(f"[ALERT] Failed to send ntfy alert: {e}")


# ── Network & Indian Proxy Detection ──────────────────────────────────────────
def is_direct_connection_available() -> bool:
    """Test if mtcbusits.in can be reached directly (e.g. runner is already in India)."""
    try:
        r = requests.get("https://mtcbusits.in/", timeout=5)
        if r.status_code == 200:
            print("[CONNECT] Direct connection to mtcbusits.in succeeded (Indian IP active ✅)")
            return True
    except Exception:
        pass
    return False


def get_working_indian_proxy() -> str:
    """
    If INDIAN_PROXY is provided in env, use it.
    Otherwise scan public lists for an active proxy that can connect to mtcbusits.in.
    """
    explicit_proxy = os.getenv("INDIAN_PROXY", "").strip()
    if explicit_proxy:
        print(f"[PROXY] Using configured INDIAN_PROXY: {explicit_proxy}")
        return explicit_proxy

    print("[PROXY] Scanning for free Indian proxies with HTTPS CONNECT support...")
    sources = [
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text&country=in&protocol=http,socks5",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt"
    ]

    candidate_proxies = []
    for s in sources:
        try:
            resp = requests.get(s, timeout=8)
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    line = line.strip()
                    if line and ":" in line and not line.startswith("#"):
                        candidate_proxies.append(f"http://{line}")
        except Exception:
            pass

    print(f"[PROXY] Found {len(candidate_proxies)} candidate proxies to test...")
    # Test up to 15 candidates against mtcbusits.in with a short timeout
    for i, p in enumerate(candidate_proxies[:15]):
        try:
            r = requests.get("https://mtcbusits.in/", proxies={"http": p, "https": p}, timeout=4)
            if r.status_code == 200:
                print(f"[PROXY] ✅ Working proxy found: {p}")
                return p
        except Exception:
            pass

    print("[PROXY] No working public proxy found.")
    return ""


# ── CAPTCHA OCR case refinement ───────────────────────────────────────────────
def refine_captcha_case(img_bytes: bytes, pred_text: str) -> str:
    """
    Refines uppercase/lowercase case of characters based on character bounding box heights
    in the uncompressed PNG image.
    """
    if len(pred_text) != 6:
        return pred_text

    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
        if img is None:
            return pred_text

        if len(img.shape) >= 3 and img.shape[2] == 4:
            b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
            fg = ((b < 240) | (g < 240) | (r < 240)) & (img[:, :, 3] > 50)
        else:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            fg = gray < 240

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(fg.astype(np.uint8))
        boxes = []
        for i in range(1, num_labels):
            x, y, w, h, area = stats[i]
            if area > 12 and h >= 6:
                boxes.append((x, y, w, h))

        boxes.sort(key=lambda b: b[0])
        merged = []
        for b in boxes:
            x, y, w, h = b
            if not merged:
                merged.append([x, y, w, h])
            else:
                px, py, pw, ph = merged[-1]
                if x < px + pw or (x - (px + pw) <= 2 and len(merged) >= 6):
                    nx1 = min(px, x)
                    ny1 = min(py, y)
                    nx2 = max(px + pw, x + w)
                    ny2 = max(py + ph, y + h)
                    merged[-1] = [nx1, ny1, nx2 - nx1, ny2 - ny1]
                else:
                    merged.append([x, y, w, h])

        if len(merged) == 6:
            chars = list(pred_text)
            for idx in range(6):
                ch = chars[idx]
                w, h = merged[idx][2], merged[idx][3]
                lower_ch = ch.lower()

                if lower_ch in ['c', 'o', 's', 'u', 'v', 'w', 'x', 'z']:
                    if h >= 27:
                        chars[idx] = lower_ch.upper()
                    elif h <= 25:
                        chars[idx] = lower_ch.lower()
                elif lower_ch in ['n', 'm']:
                    if h >= 28:
                        chars[idx] = lower_ch.upper()
                    else:
                        chars[idx] = lower_ch.lower()
                elif lower_ch in ['p', 'q']:
                    if w >= 28 or h >= 35:
                        chars[idx] = lower_ch.upper()
                    else:
                        chars[idx] = lower_ch.lower()
                elif lower_ch == 'k':
                    if h >= 28 and w >= 25:
                        chars[idx] = 'K'
                    else:
                        chars[idx] = 'k'
            return "".join(chars)
    except Exception:
        pass

    return pred_text


# ── Login ─────────────────────────────────────────────────────────────────────
def login_to_portal(page):
    print("[LOGIN]  Navigating to MTC Portal...")
    for nav_attempt in range(3):
        try:
            page.goto("https://mtcbusits.in/", timeout=45000, wait_until="domcontentloaded")
            break
        except Exception as e:
            print(f"[LOGIN]  Navigation attempt {nav_attempt+1} failed: {e}")
            if nav_attempt == 2:
                raise RuntimeError(f"Cannot reach https://mtcbusits.in/ after 3 attempts: {e}")
            page.wait_for_timeout(3000)

    page.wait_for_selector("input[formcontrolname='captcha'], input[name='captcha']", timeout=30000)

    # Fast OCR
    ocr = ddddocr.DdddOcr(beta=True, show_ad=False)
    ocr.set_ranges(string.ascii_letters + string.digits)

    logged_in = False
    max_auto_attempts = 35

    for attempt in range(1, max_auto_attempts + 1):
        cur_url = page.url.lower().rstrip("/")
        if "login" not in cur_url and cur_url != "https://mtcbusits.in":
            logged_in = True
            break

        print(f"[LOGIN]  Auto-solving CAPTCHA (attempt {attempt}/{max_auto_attempts})...")

        # Fill credentials
        page.locator("input[name='UserName'], input[name='username']").first.fill(USERNAME)
        page.locator("input[id='password'], input[name='password']").first.fill(PASSWORD)

        # Wait for captcha image
        img_el = page.query_selector("img[src^='data:image']")
        if not img_el:
            page.wait_for_timeout(1000)
            img_el = page.query_selector("img[src^='data:image']")

        if not img_el:
            print("[LOGIN]  Captcha image not found, reloading...")
            try:
                page.locator(".k-i-reload").click(timeout=1500)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            continue

        src = img_el.get_attribute("src") or ""
        if not src.startswith("data:image"):
            page.wait_for_timeout(1000)
            continue

        base64_data = src.split(",")[1]
        image_data = base64.b64decode(base64_data)

        # Fast OCR & refine case
        raw_pred = ocr.classification(image_data).strip()
        refined_captcha = refine_captcha_case(image_data, raw_pred)
        print(f"[LOGIN]  Extracted CAPTCHA: '{refined_captcha}'")

        if len(refined_captcha) != 6:
            print(f"[LOGIN]  Captcha length is {len(refined_captcha)} (expected 6). Refreshing...")
            old_src = src
            try:
                page.locator(".k-i-reload").click(timeout=1500)
            except Exception:
                pass
            for _ in range(20):
                page.wait_for_timeout(150)
                new_img = page.query_selector("img[src^='data:image']")
                if new_img and (new_img.get_attribute("src") or "") != old_src:
                    break
            continue

        # Enter captcha
        cap_input = page.locator("input[formcontrolname='captcha'], input[name='captcha']").first
        cap_input.fill("")
        cap_input.fill(refined_captcha)
        page.wait_for_timeout(200)

        # Click Login
        old_src = src
        page.locator("button:has-text('Login')").first.click()

        # Check for success or error toast
        t0 = time.time()
        while time.time() - t0 < 4.0:
            page.wait_for_timeout(200)
            cur_url = page.url.lower().rstrip("/")
            if "login" not in cur_url and cur_url != "https://mtcbusits.in":
                logged_in = True
                break
            toast = page.query_selector(".toast-message, .alert")
            if toast and "invalid captcha" in toast.inner_text().lower():
                break

        if logged_in:
            print("[LOGIN]  Successfully logged in!")
            return True

        print("[LOGIN]  CAPTCHA was rejected. Waiting for next CAPTCHA...")
        for _ in range(25):
            page.wait_for_timeout(150)
            new_img = page.query_selector("img[src^='data:image']")
            if new_img and (new_img.get_attribute("src") or "") != old_src:
                break
        else:
            try:
                page.locator(".k-i-reload").click(timeout=1000)
                page.wait_for_timeout(800)
            except Exception:
                pass

    if not logged_in:
        raise RuntimeError(
            f"[LOGIN]  Login failed after {max_auto_attempts} CAPTCHA attempts."
        )

    return True


# ── Check 1: PIS Display Connectivity ─────────────────────────────────────────
def check_pis_display(page) -> dict:
    """Navigate to PIS Display Connectivity page and extract down counts."""
    print("[PIS]  Navigating to PIS Display Connectivity...")
    page.goto("https://mtcbusits.in/avls/Vehicletracking/PISDisplayConnectivity", timeout=30000)

    page.wait_for_selector("kendo-grid-toolbar", timeout=15000)

    try:
        page.wait_for_selector(".k-loading-mask", state="hidden", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2000)

    xpaths = {
        "2L":  '//*[@id="gridlistid"]/kendo-grid-toolbar/div/div[7]/div[1]/span[3]',
        "4L":  '//*[@id="gridlistid"]/kendo-grid-toolbar/div/div[7]/div[2]/span[3]',
        "10L": '//*[@id="gridlistid"]/kendo-grid-toolbar/div/div[7]/div[3]/span[3]',
    }

    results = {}
    alerts = []

    for key, xpath in xpaths.items():
        try:
            el = page.locator(f"xpath={xpath}")
            el.wait_for(timeout=10000)
            raw = el.inner_text().strip()
            value = int("".join(filter(str.isdigit, raw)))
            results[key] = value
            limit = PIS_MAX_DOWN.get(key, 999)
            status = "OK" if value <= limit else "BREACH"
            print(f"[PIS]  {key} PIS Down = {value} (limit: {limit}) [{status}]")
            if value > limit:
                alerts.append(f"{key} PIS Down = {value} (limit: {limit})")
        except Exception as e:
            results[key] = None
            alerts.append(f"{key} PIS Down: could not read value ({e})")
            print(f"[PIS]  ERROR reading {key} PIS Down: {e}")

    return {"values": results, "alerts": alerts}


# ── Check 2: Fleet Dashboard (Communicating) ───────────────────────────────────
def check_fleet_dashboard(page) -> dict:
    """Navigate to Fleet Dashboard, filter by Communicating, extract total fleet count."""
    print("[FLEET] Navigating to Fleet Dashboard...")
    page.goto("https://mtcbusits.in/avls/Dashboard/Fleetdashboard", timeout=30000)
    page.wait_for_timeout(3000)

    alerts = []
    fleet_count = None

    try:
        dropdown = page.locator("input[placeholder='Select Data Delay'], [placeholder='Select Data Delay']").first
        dropdown.wait_for(timeout=10000)
        dropdown.click()
        page.wait_for_timeout(1000)

        selected = False
        try:
            opt = page.get_by_role("option", name="Communicating").first
            if opt.is_visible(timeout=2000):
                opt.click()
                selected = True
        except Exception:
            pass

        if not selected:
            try:
                opt = page.locator("li.k-item, .k-list-item, li").filter(has_text="Communicating").first
                if opt.is_visible(timeout=2000):
                    opt.click()
                    selected = True
            except Exception:
                pass

        if not selected:
            try:
                opt = page.get_by_text("Communicating", exact=True).first
                if opt.is_visible(timeout=2000):
                    opt.click()
                    selected = True
            except Exception:
                pass

        if not selected:
            page.locator("li").filter(has_text="Communicating").first.click()

        page.wait_for_timeout(1000)
        print("[FLEET] Selected 'Communicating' from dropdown.")
    except Exception as e:
        alerts.append(f"Fleet Dashboard: dropdown selection failed ({e})")
        print(f"[FLEET] ERROR selecting dropdown: {e}")
        return {"value": None, "alerts": alerts}

    try:
        search_btn = page.locator("button:has-text('Search'), a:has-text('Search')").first
        search_btn.click()
        print("[FLEET] Clicked Search button.")
    except Exception as e:
        alerts.append(f"Fleet Dashboard: search button click failed ({e})")
        print(f"[FLEET] ERROR clicking search: {e}")
        return {"value": None, "alerts": alerts}

    try:
        page.wait_for_selector(".k-loading-mask", state="hidden", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    try:
        pager_el = page.locator(".k-pager-info").first
        if pager_el.is_visible(timeout=5000):
            raw_pager = pager_el.inner_text().strip()
            print(f"[FLEET] Pager text: {repr(raw_pager)}")
            m = re.search(r"of\s+([\d,]+)\s+items", raw_pager, re.IGNORECASE)
            if m:
                fleet_count = int(m.group(1).replace(",", ""))

        if fleet_count is None:
            total_el = page.locator(
                "xpath=/html/body/ngx-app/ngx-pages/ngx-one-column-layout/nb-layout"
                "/div/div/div/div/div/nb-layout-column/ngx-fleetdashboard/div/div"
                "/div[2]/div[2]"
            )
            total_el.wait_for(timeout=5000)
            raw = total_el.inner_text().strip()
            print(f"[FLEET] Header raw text: {repr(raw)}")
            tokens = re.findall(r'\b\d+\b', raw)
            if tokens:
                fleet_count = int(tokens[-1])

        if fleet_count is not None:
            status = "OK" if fleet_count >= FLEET_MIN_COMM else "BREACH"
            print(f"[FLEET] Communicating Fleet = {fleet_count} (min: {FLEET_MIN_COMM}) [{status}]")
            if fleet_count < FLEET_MIN_COMM:
                alerts.append(f"Communicating Fleet = {fleet_count} (minimum: {FLEET_MIN_COMM})")
        else:
            alerts.append("Fleet Dashboard: failed to parse numeric count from pager or header")
    except Exception as e:
        alerts.append(f"Fleet Dashboard: could not read Communicating Fleet value ({e})")
        print(f"[FLEET] ERROR reading fleet count: {e}")

    return {"value": fleet_count, "alerts": alerts}


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    ts = now_ist()
    print(f"\n{'='*65}")
    print(f"  MTC Portal Health Monitor — {ts}")
    print(f"{'='*65}\n")

    if not USERNAME or not PASSWORD:
        msg = f"[{ts}] CRITICAL: MTC_USERNAME or MTC_PASSWORD env variable not set."
        print(msg)
        send_alert(msg)
        raise RuntimeError(msg)

    all_alerts = []

    # Check network connectivity mode
    proxy_config = None
    if is_direct_connection_available():
        print("[NETWORK] Running via direct Indian connection.")
    else:
        print("[NETWORK] Direct connection unavailable (outside India). Attempting proxy routing...")
        proxy_server = get_working_indian_proxy()
        if proxy_server:
            proxy_config = {"server": proxy_server}
            print(f"[NETWORK] Browser routed through proxy: {proxy_server}")
        else:
            print("[NETWORK] WARNING: No Indian proxy available. Trying direct fallback...")

    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Login
            login_to_portal(page)

            # Check 1 — PIS Display Connectivity
            pis_result = check_pis_display(page)
            all_alerts.extend(pis_result["alerts"])

            # Check 2 — Fleet Dashboard
            fleet_result = check_fleet_dashboard(page)
            all_alerts.extend(fleet_result["alerts"])

            # Format report
            pis_vals = pis_result["values"]
            comm_val = fleet_result["value"]
            
            v2 = pis_vals.get("2L", "N/A")
            v4 = pis_vals.get("4L", "N/A")
            v10 = pis_vals.get("10L", "N/A")
            
            st_2 = "❌ ERROR" if v2 is None else ("⚠️ BREACH" if (isinstance(v2, int) and v2 > PIS_MAX_DOWN.get("2L", 999)) else "✅ OK")
            st_4 = "❌ ERROR" if v4 is None else ("⚠️ BREACH" if (isinstance(v4, int) and v4 > PIS_MAX_DOWN.get("4L", 999)) else "✅ OK")
            st_10 = "❌ ERROR" if v10 is None else ("⚠️ BREACH" if (isinstance(v10, int) and v10 > PIS_MAX_DOWN.get("10L", 999)) else "✅ OK")
            st_comm = "❌ ERROR" if comm_val is None else ("⚠️ BREACH" if (isinstance(comm_val, int) and comm_val < FLEET_MIN_COMM) else "✅ OK")

            full_report = (
                f"MTC Portal Health @ {ts}\n\n"
                f"• 2L PIS Down: {v2} (max: {PIS_MAX_DOWN.get('2L')}) {st_2}\n"
                f"• 4L PIS Down: {v4} (max: {PIS_MAX_DOWN.get('4L')}) {st_4}\n"
                f"• 10L PIS Down: {v10} (max: {PIS_MAX_DOWN.get('10L')}) {st_10}\n"
                f"• Communicating Fleet: {comm_val} (min: {FLEET_MIN_COMM}) {st_comm}"
            )

            print(f"\n[SUMMARY] {ts}")
            print(f"  PIS Down   — 2L: {v2}, 4L: {v4}, 10L: {v10}")
            print(f"  Fleet Comm — {comm_val}")

            if all_alerts:
                print(f"\n[ALERT] Threshold breach(es) detected:\n{full_report}")
                send_alert(full_report)
            else:
                print("\n[OK] All metrics within thresholds. Sending status report...")
                send_alert(full_report)

        except Exception as exc:
            print(f"\n[ERROR] Unexpected exception: {exc}")
            try:
                page.screenshot(path="debug_failure.png")
                print("[DEBUG] Screenshot saved: debug_failure.png")
            except Exception:
                pass
            error_msg = f"MTC Portal Monitor ERROR @ {ts}\n{exc}"
            send_alert(error_msg)
            raise

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
