# MTC Bus ITS Portal â€“ 15-Minute Health Monitor

Automated headless monitoring of [https://mtcbusits.in/](https://mtcbusits.in/) via GitHub Actions (zero-cost, every 15 minutes).  
Sends push alerts to your phone via [ntfy.sh](https://ntfy.sh/) when thresholds are breached.

> **Runs on GitHub-hosted runners (ubuntu-latest)** â€” no self-hosted runner needed.  
> The script auto-fetches a free Indian proxy to bypass geo-restrictions, so the site sees an Indian IP address.

---

## ðŸ“‹ What It Monitors

| Check | Metric | Alert Threshold |
|---|---|---|
| PIS Display Connectivity | 2L PIS Down count | > 20 |
| PIS Display Connectivity | 4L PIS Down count | > 6 |
| PIS Display Connectivity | 10L PIS Down count | > 3 |
| Fleet Dashboard | Communicating fleet count | < 2500 |

---

## âš™ï¸ One-Time Setup

### Step 1 â€” Add GitHub Repository Secrets

Go to your GitHub repository â†’ **Settings** â†’ **Secrets and variables** â†’ **Actions** â†’ **New repository secret**.

Add these **3 secrets** (names must be exact):

| Secret Name | Value |
|---|---|
| `MTC_USERNAME` | Your MTC portal login ID |
| `MTC_PASSWORD` | Your MTC portal password |
| `NTFY_TOPIC` | Your secret ntfy.sh topic name (e.g. `my-mtc-alerts-xk29`) |

> **Optional:** You can also add an `INDIAN_PROXY` secret (e.g. `http://103.x.x.x:8080`) to use a specific Indian proxy instead of auto-fetching from free proxy lists.

> **Never** commit credentials to this repository. All sensitive values are loaded exclusively from the secrets above.

---

### Step 2 â€” Publish to GitHub (using VS Code GitHub Extension)

1. Open this folder in VS Code.
2. Open the **Source Control** panel (`Ctrl+Shift+G`).
3. Click **Publish to GitHub** (or use the command palette: `Publish to GitHub`).
4. Choose **Public repository**.
5. The GitHub Actions workflow will automatically activate.

---

### Step 3 â€” Install ntfy.sh on Your Phone

1. Install the **ntfy** app (Android / iOS).
2. Subscribe to the topic name you set as `NTFY_TOPIC`.
3. You will receive urgent push alerts whenever a threshold is breached.

---

## ðŸ”§ Editing Alert Thresholds

All thresholds are stored in [`config.json`](config.json) â€” no Python code changes needed:

```json
{
  "pis_display_max_down": {
    "2L": 20,
    "4L": 6,
    "10L": 3
  },
  "fleet_dashboard_min_communicating": 2500,
  "timezone": "Asia/Kolkata"
}
```

After editing, commit and push the file. The next scheduled run will use the new values.

---

## ðŸŒ Indian Proxy (Geo-Restriction Bypass)

The site `mtcbusits.in` only allows access from Indian IP addresses. This script handles it automatically:

1. **Auto-fetch mode (default):** On each run, the script fetches free Indian HTTP proxies from ProxyScrape and Geonode, tests them, and routes Playwright's browser traffic through the first working one.
2. **Manual proxy mode:** Set the `INDIAN_PROXY` GitHub Secret to a specific proxy URL (e.g. `http://103.x.x.x:8080`) to skip auto-fetching.
3. **IP verification:** Before login, the script verifies the browser's outgoing IP is in India.

---

## â–¶ï¸ Manual Trigger

Go to your repository â†’ **Actions** â†’ **MTC Portal Health Monitor** â†’ **Run workflow**.

---

## ðŸ—‚ï¸ Repository Structure

```
mtc-monitor/
â”œâ”€â”€ monitor.py                        # Main monitoring script (with Indian proxy support)
â”œâ”€â”€ config.json                       # Alert thresholds (edit freely)
â”œâ”€â”€ requirements.txt                  # Python dependencies
â”œâ”€â”€ .gitignore                        # Excludes secrets, logs, screenshots
â”œâ”€â”€ README.md                         # This file
â””â”€â”€ .github/
    â””â”€â”€ workflows/
        â””â”€â”€ mtc_monitor.yml           # GitHub Actions schedule (every 15 min, ubuntu-latest)
```

---

## ðŸ” Troubleshooting

- **Login failures**: If CAPTCHA solving repeatedly fails after all 35 retries, an alert is sent and a `debug_failure.png` screenshot is uploaded as a GitHub Actions artifact.
- **Proxy failures**: If no working Indian proxy is found, the script attempts a direct connection (which may fail from non-Indian runners). Check the Actions log for proxy-related messages.
- **Download artifact**: Go to the failed Actions run â†’ scroll to **Artifacts** â†’ download `debug-screenshot`.
- **Adjust retry logic**: The script attempts CAPTCHA solving up to 35 times before raising an error.
