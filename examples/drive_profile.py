"""Drive a persona profile via the local automation API + Playwright.

persona exposes a local REST API (default http://127.0.0.1:8000, enable the
"Claude control server" toggle in the UI). Launch a profile in automation mode
to get a CDP endpoint, then attach any CDP client (Playwright/Puppeteer) or
Selenium (via the host:port debugger address).

Run:  python examples/drive_profile.py <profile-name>
"""

import sys

import httpx
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8000/api/v1"
PROFILE = sys.argv[1] if len(sys.argv) > 1 else "default"


def main() -> None:
    # trust_env=False: don't route the loopback call through Whonix's Tor proxy.
    client = httpx.Client(trust_env=False, timeout=60)

    # 1. Launch the profile in automation mode -> returns the CDP endpoint.
    resp = client.post(f"{API}/browser/{PROFILE}/launch")
    resp.raise_for_status()
    cdp = resp.json()["cdp"]
    ws_endpoint = cdp["ws"]["playwright"]
    print("CDP endpoint:", ws_endpoint)

    # 2. Attach Playwright. Reuse the existing context/page so the spoofed
    #    fingerprint and cookies are kept (do NOT new_context()).
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://example.com")
        print("Title:", page.title())
        # ... drive the session ...
        browser.close()  # detaches CDP; persona keeps the browser alive

    # 3. Stop the browser when done (lifecycle stays owned by persona).
    client.post(f"{API}/browser/{PROFILE}/stop")

    # Selenium alternative:
    #   from selenium import webdriver
    #   opts = webdriver.ChromeOptions()
    #   opts.add_experimental_option("debuggerAddress", cdp["ws"]["selenium"])
    #   driver = webdriver.Chrome(options=opts)


if __name__ == "__main__":
    main()
