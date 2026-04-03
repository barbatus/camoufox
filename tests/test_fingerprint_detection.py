#!/usr/bin/env python3
"""
Test Camoufox against FingerprintJS bot detection.

Loads the FingerprintJS Pro agent in the browser, captures the requestId,
then queries the server-side API to check if the visit was flagged as bot.

Requirements:
  - FPJS_SECRET_KEY env var (server API key from fingerprint.com dashboard)
  - camoufox installed with patched binary

Usage:
  FPJS_SECRET_KEY=xxx python tests/test_fingerprint_detection.py
  FPJS_SECRET_KEY=xxx python tests/test_fingerprint_detection.py --playwright  # compare with plain Playwright
"""

import asyncio
import json
import os
import sys
import time

import httpx

FPJS_PUBLIC_KEY = "Sr1OsLmEth3KOrhETG09"
FPJS_SECRET_KEY = os.environ.get("FPJS_SECRET_KEY", "")
FPJS_REGION = "eu"
FPJS_API_BASE = "https://eu.api.fpjs.io"


# Minimal HTML page that loads FingerprintJS and captures the result
def _build_test_page() -> str:
    return (
        "<!DOCTYPE html><html><head><title>FP Test</title></head><body>"
        '<div id="status">Loading...</div>'
        "<script>"
        "window.__fpResult = null;"
        "window.__fpError = null;"
        "var fpUrl = 'https://fpjscdn.net/v4/" + FPJS_PUBLIC_KEY + "';"
        "import(fpUrl)"
        ".then(function(FP) { return FP.start({region: '" + FPJS_REGION + "'}); })"
        ".then(function(fp) { return fp.get(); })"
        ".then(function(r) {"
        "  window.__fpResult = r;"
        '  document.getElementById("status").textContent = "Done: " + r.requestId;'
        "})"
        ".catch(function(e) {"
        "  window.__fpError = e.message || String(e);"
        '  document.getElementById("status").textContent = "Error: " + window.__fpError;'
        "});"
        "</script></body></html>"
    )


TEST_PAGE_HTML = _build_test_page()


async def _run_fingerprint(page) -> dict:  # type: ignore[no-untyped-def]
    """Inject FingerprintJS into an open page and capture the result."""
    await page.evaluate("window.__fpResult = null; window.__fpError = null;")

    await page.add_script_tag(
        content=(
            "import('https://fpjscdn.net/v4/" + FPJS_PUBLIC_KEY + "')"
            ".then(FP => FP.start({region: '" + FPJS_REGION + "'}))"
            ".then(fp => fp.get())"
            ".then(r => { window.__fpResult = r; })"
            ".catch(e => { window.__fpError = e.message || String(e); });"
        ),
        type="module",
    )

    for _ in range(60):
        result = await page.evaluate("window.__fpResult")
        error = await page.evaluate("window.__fpError")
        if result or error:
            break
        await asyncio.sleep(0.5)

    if error:
        return {"error": error}
    if not result:
        return {"error": "FingerprintJS did not complete in 30s"}

    # Normalize field names (FP Pro v4 uses snake_case)
    return {
        "requestId": result.get("event_id") or result.get("requestId"),
        "visitorId": result.get("visitor_id") or result.get("visitorId"),
        "confidence": result.get("confidence", {}),
        "suspectScore": result.get("suspect_score"),
    }


async def _start_test_server() -> tuple:
    """Start a local HTTP server serving the FP test page."""
    import http.server
    import threading

    html = (
        "<!DOCTYPE html><html><head><title>FP Test</title>"
        '<script type="module">'
        "import('https://fpjscdn.net/v4/" + FPJS_PUBLIC_KEY + "')"
        ".then(FP => FP.start({region: '" + FPJS_REGION + "'}))"
        ".then(fp => fp.get())"
        ".then(r => { window.__fpResult = r; })"
        ".catch(e => { window.__fpError = e.message || String(e); });"
        "</script></head><body>"
        "<script>window.__fpResult = null; window.__fpError = null;</script>"
        "</body></html>"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


async def run_fingerprint_in_camoufox() -> dict:
    """Run FingerprintJS in Camoufox and return the client-side result."""
    from camoufox.async_api import AsyncCamoufox, AsyncNewContext

    server, port = await _start_test_server()
    try:
        async with AsyncCamoufox(headless=True) as browser:
            ctx = await AsyncNewContext(
                browser,
                # Workaround: C++ Accept-Encoding override breaks ES module decompression (U+FFFD).
                # Remove once binary is rebuilt with the network-patches.patch fix.
                extra_http_headers={"accept-encoding": "identity"},
            )
            page = await ctx.new_page()
            # Navigate to a real domain first, then inject FP script
            # (FingerprintJS Pro validates the origin domain)
            await page.goto("https://httpbin.org/html", wait_until="load")
            return await _run_fingerprint(page)
    finally:
        server.shutdown()


async def run_fingerprint_in_playwright() -> dict:
    """Run FingerprintJS in plain Playwright Firefox for comparison."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://httpbin.org/html", wait_until="load")
        result = await _run_fingerprint(page)
        await page.close()
        await browser.close()
        return result


def query_server_api(request_id: str) -> dict:
    """Query FingerprintJS server API for bot detection results."""
    if not FPJS_SECRET_KEY:
        return {"error": "FPJS_SECRET_KEY not set — cannot query server API"}

    resp = httpx.get(
        f"{FPJS_API_BASE}/events/{request_id}",
        headers={"Auth-API-Key": FPJS_SECRET_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def print_detection_report(label: str, client_result: dict, server_result: dict | None) -> None:
    """Print a detection report for one browser run."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    if "error" in client_result:
        print(f"  Client error: {client_result['error']}")
        return

    print(f"  Request ID:   {client_result.get('requestId', '?')}")
    print(f"  Visitor ID:   {client_result.get('visitorId', '?')}")
    print(f"  Confidence:   {client_result.get('confidence', {}).get('score', '?')}")

    if not server_result or "error" in server_result:
        print(f"  Server error: {(server_result or {}).get('error', 'no server result')}")
        return

    # Bot detection
    products = server_result.get("products", {})
    botd = products.get("botd", {}).get("data", {})
    bot = botd.get("bot", {})
    print(f"\n  --- Bot Detection ---")
    print(f"  Result:       {bot.get('result', '?')}")
    print(f"  Type:         {bot.get('type', 'N/A')}")

    # Tampering
    tampering = products.get("tampering", {}).get("data", {})
    print(f"\n  --- Tampering ---")
    print(f"  Anomaly:      {tampering.get('anomalyScore', '?')}")
    print(f"  Confidence:   {tampering.get('confidence', '?')}")
    print(f"  Anti-detect:  {tampering.get('antiDetectBrowser', '?')}")
    print(f"  Raw:          {json.dumps(tampering)}")

    # Suspect score
    suspect = products.get("suspectScore", {}).get("data", {})
    print(f"\n  --- Suspect Score ---")
    print(f"  Score:        {suspect.get('result', '?')}")

    # VPN
    vpn = products.get("vpn", {}).get("data", {})
    print(f"\n  --- VPN ---")
    print(f"  Result:       {vpn.get('result', '?')}")
    print(f"  Confidence:   {vpn.get('confidence', '?')}")

    # Developer tools
    devtools = products.get("developerTools", {}).get("data", {})
    print(f"\n  --- Developer Tools ---")
    print(f"  Result:       {devtools.get('result', '?')}")

    # IP info
    ip_info = products.get("ipInfo", {}).get("data", {}).get("v4", {})
    print(f"\n  --- IP Info ---")
    print(f"  IP:           {ip_info.get('address', '?')}")
    print(f"  Datacenter:   {ip_info.get('datacenter', {}).get('result', '?')}")
    print(f"  Geolocation:  {ip_info.get('geolocation', {}).get('city', {}).get('name', '?')}, {ip_info.get('geolocation', {}).get('country', {}).get('name', '?')}")

    # Raw JSON for debugging
    print(f"\n  --- Raw Bot Detection ---")
    print(f"  {json.dumps(botd, indent=2)}")


async def main():
    use_playwright = "--playwright" in sys.argv

    # Run Camoufox
    print("Running FingerprintJS in Camoufox...")
    camou_result = await run_fingerprint_in_camoufox()

    camou_server = None
    if "requestId" in camou_result:
        print(f"  Got requestId: {camou_result['requestId']}")
        time.sleep(2)  # Let server process
        camou_server = query_server_api(camou_result["requestId"])

    print_detection_report("CAMOUFOX", camou_result, camou_server)

    # Optionally run plain Playwright for comparison
    if use_playwright:
        print("\n\nRunning FingerprintJS in plain Playwright Firefox...")
        pw_result = await run_fingerprint_in_playwright()

        pw_server = None
        if "requestId" in pw_result:
            print(f"  Got requestId: {pw_result['requestId']}")
            time.sleep(2)
            pw_server = query_server_api(pw_result["requestId"])

        print_detection_report("PLAIN PLAYWRIGHT FIREFOX", pw_result, pw_server)

    # Summary
    print(f"\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}")

    camou_bot = "?"
    if camou_server and "products" in camou_server:
        camou_bot = camou_server["products"].get("botd", {}).get("data", {}).get("bot", {}).get("result", "?")
    print(f"  Camoufox:           bot={camou_bot}")

    if use_playwright:
        pw_bot = "?"
        if pw_server and "products" in pw_server:
            pw_bot = pw_server["products"].get("botd", {}).get("data", {}).get("bot", {}).get("result", "?")
        print(f"  Plain Playwright:   bot={pw_bot}")

    is_detected = camou_bot != "notDetected"
    print(f"\n  Camoufox detected as bot: {'YES' if is_detected else 'NO'}")
    print(f"{'='*70}")

    return 0 if not is_detected else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
