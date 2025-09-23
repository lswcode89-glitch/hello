import os
import asyncio
import re
import time
import requests
from playwright.async_api import async_playwright

# ---------------------------
# CONFIGURATION
# ---------------------------

URL = "https://www.exponent.finance/income"

# Selectors for the two xSOL items' Fixed APY
# These CSS selectors are approximate; adjust them if the website structure changes.
X_SOL_SELECTOR = "div:contains('xSOL')"

# Within each xSOL container, find the Fixed APY value
APY_SELECTOR = ".FixedAPY, .fixed-apy, .apy, div.apy"  # example classes, update as needed

# Threshold for alert (percentage points)
THRESHOLD = 3  # e.g. alert if differ by ≥ 1%

# Telegram bot config
#BOT_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = "@lswcoinm"


# How many attempts to wait for the dynamic content before giving up
MAX_ATTEMPTS = 4
BASE_WAIT_SECONDS = 2.0

# regex to capture a numeric percent (e.g. 44.80%)
PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")

# ---------------------------
# Helpers
# ---------------------------

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Failed to send telegram message:", e)
        return None

async def scrape_xsol_apys():
    """
    Launch a headless browser, evaluate the live DOM and return a list of numeric APYs found
    for rows that mention 'xSOL'.
    Will retry a few times to tolerate slow loading.
    Returns: (apys_list, debug_raw_list)
    where debug_raw_list is a list of dicts {'rowText': ..., 'apyText': ...}
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL, timeout=60000)
        # try several attempts in case data loads slowly
        attempt = 0
        last_debug = []
        while attempt < MAX_ATTEMPTS:
            # allow network / JS to settle a bit
            await page.wait_for_load_state("networkidle")
            # small extra wait (increasing with attempts)
            await page.wait_for_timeout(int(BASE_WAIT_SECONDS * 1000 * (1 + attempt)))

            # Run JS inside the page to collect rows mentioning "xSOL"
            raw = await page.evaluate(
                """() => {
                    const rows = Array.from(document.querySelectorAll('tr'));
                    const out = [];
                    rows.forEach(r => {
                        const rowText = (r.innerText || '').trim();
                        if (rowText.toLowerCase().includes('xsol')) {
                            // try the 4th TD first (index 3)
                            let apyText = '';
                            const tds = r.querySelectorAll('td');
                            if (tds && tds.length >= 4) {
                                apyText = (tds[3].innerText || '').trim();
                            }
                            // fallback: find any descendant text that contains a '%' sign
                            if (!apyText || !apyText.includes('%')) {
                                const descendantTexts = Array.from(r.querySelectorAll('*')).map(el => el.innerText || '');
                                const found = descendantTexts.find(t => t && t.includes('%'));
                                if (found) apyText = found.trim();
                            }
                            out.push({ rowText: rowText, apyText: apyText });
                        }
                    });
                    return out;
                }"""
            )

            # Parse percent numbers using regex
            apys = []
            debug = []
            for item in raw:
                row_text = item.get("rowText", "")
                apy_text = item.get("apyText", "")
                m = PCT_RE.search(apy_text) or PCT_RE.search(row_text)
                if m:
                    try:
                        apys.append(float(m.group(1)))
                    except Exception:
                        debug.append({"rowText": row_text, "apyText": apy_text, "parsed": None})
                else:
                    debug.append({"rowText": row_text, "apyText": apy_text, "parsed": None})

            last_debug = debug
            if len(apys) >= 2:
                await browser.close()
                return apys, raw  # success: return numeric list and raw debug
            # not enough data yet: wait and retry
            attempt += 1

        # out of attempts
        await browser.close()
        return [], raw

# ---------------------------
# Main
# ---------------------------

async def main():
    print("Scraping", URL)
    apys, raw = await scrape_xsol_apys()

    if not apys:
        # print helpful debug info for you to paste back if needed
        print("⚠️ Found fewer than 2 xSOL APY values.")
        print("Raw rows found that mention 'xSOL' (apyText may be empty):")
        for i, item in enumerate(raw):
            print(f"  [{i}] rowText: {repr(item.get('rowText',''))}")
            print(f"       apyText: {repr(item.get('apyText',''))}")
        return

    # Use first two for comparison
    apy1, apy2 = apys[0], apys[1]
    diff = abs(apy1 - apy2)
    print(f"Found APYs for xSOL: {apy1}% vs {apy2}% (diff = {diff:.2f}%)")

    if diff >= THRESHOLD:
        msg = (f"⚠️ xSOL APY difference alert: {diff:.2f}%\n"
               f"First: {apy1}%\nSecond: {apy2}%\n"
               f"Threshold: {THRESHOLD}%\n"
               f"Source: {URL}")
        send_telegram_message(msg)
        print("Alert sent to Telegram.")
    else:
        print("Difference below threshold; no alert.")

# ---------------------------
# Run every minute
# ---------------------------

if __name__ == "__main__":
    asyncio.run(main())