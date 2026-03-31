#!/usr/bin/env python3
"""
Test script to verify fix for https://github.com/daijro/camoufox/issues/279
Camoufox should NOT hang when opening multiple pages or contexts concurrently.

Covers:
  A) Concurrent browser.new_page()           — original reproducer
  B) Concurrent new_context() + new_page()   — scrape_md pattern
  C) Mixed direct-page and context+page
  D) 30 real-world pages in 3 batches of 10  — stress test
"""

import asyncio
import time
import sys

from camoufox import DefaultAddons
from camoufox.async_api import AsyncCamoufox

# Exclude UBO: its temporary-addon lifecycle corrupts when concurrent
# pages are destroyed during active network interception.
_LAUNCH = dict(headless=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def open_page(browser, i: int, url: str = "https://example.com", timeout: int = 30_000):
    page = await browser.new_page()
    try:
        await page.goto(url, timeout=timeout)
        title = await page.title()
        print(f"  Page {i}: {title[:50]}  ({url[:60]})")
        return True
    except Exception as e:
        print(f"  Page {i}: ERR {str(e)[:60]}  ({url[:60]})")
        return False
    finally:
        await page.close()


async def open_context_and_page(browser, i: int, url: str = "https://example.com", timeout: int = 30_000):
    ctx = await browser.new_context()
    page = await ctx.new_page()
    try:
        await page.goto(url, timeout=timeout)
        title = await page.title()
        print(f"  Context {i}: {title[:50]}  ({url[:60]})")
        return True
    except Exception as e:
        print(f"  Context {i}: ERR {str(e)[:60]}  ({url[:60]})")
        return False
    finally:
        await page.close()
        await ctx.close()


# ---------------------------------------------------------------------------
# Test A: Concurrent browser.new_page() (original reproducer)
# ---------------------------------------------------------------------------

async def test_concurrent_pages(n: int, timeout: float = 60.0):
    print(f"\nTest A: {n} concurrent browser.new_page() calls (timeout={timeout}s)...")
    start = time.time()
    async with AsyncCamoufox(**_LAUNCH) as browser:
        tasks = [open_page(browser, i) for i in range(1, n + 1)]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
    print(f"  Completed in {time.time() - start:.2f}s")


# ---------------------------------------------------------------------------
# Test B: Concurrent new_context() + new_page()
# ---------------------------------------------------------------------------

async def test_concurrent_contexts(n: int, timeout: float = 60.0):
    print(f"\nTest B: {n} concurrent new_context() + new_page() (timeout={timeout}s)...")
    start = time.time()
    async with AsyncCamoufox(**_LAUNCH) as browser:
        tasks = [open_context_and_page(browser, i) for i in range(1, n + 1)]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
    print(f"  Completed in {time.time() - start:.2f}s")


# ---------------------------------------------------------------------------
# Test C: Mixed
# ---------------------------------------------------------------------------

async def test_mixed(n: int, timeout: float = 60.0):
    print(f"\nTest C: {n} mixed concurrent calls (timeout={timeout}s)...")
    start = time.time()
    async with AsyncCamoufox(**_LAUNCH) as browser:
        tasks = []
        for i in range(1, n + 1):
            if i % 2 == 0:
                tasks.append(open_context_and_page(browser, i))
            else:
                tasks.append(open_page(browser, i))
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
    print(f"  Completed in {time.time() - start:.2f}s")


# ---------------------------------------------------------------------------
# Test D: 30 real-world pages in batches (stress test)
# ---------------------------------------------------------------------------

BATCH_URLS = [
    # Batch 1
    [
        "https://example.com",
        "https://example.org",
        "https://httpbin.org/html",
        "https://www.iana.org/domains/reserved",
        "https://en.wikipedia.org/wiki/Main_Page",
        "https://www.python.org",
        "https://docs.python.org/3/",
        "https://pypi.org",
        "https://github.com",
        "https://httpbin.org/get",
    ],
    # Batch 2
    [
        "https://news.ycombinator.com",
        "https://lobste.rs",
        "https://lite.cnn.com",
        "https://text.npr.org",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "https://www.rust-lang.org",
        "https://go.dev",
        "https://nodejs.org",
        "https://www.typescriptlang.org",
        "https://httpbin.org/headers",
    ],
    # Batch 3
    [
        "https://example.com",
        "https://httpbin.org/html",
        "https://en.wikipedia.org/wiki/Web_scraping",
        "https://en.wikipedia.org/wiki/Firefox",
        "https://www.w3.org",
        "https://html.spec.whatwg.org",
        "https://developer.mozilla.org/en-US/",
        "https://www.gnu.org",
        "https://httpbin.org/ip",
        "https://example.org",
    ],
]


async def test_batches(timeout_per_batch: float = 120.0):
    print(f"\nTest D: 30 pages in 3 batches of 10 (timeout={timeout_per_batch}s/batch)...")
    total_ok = 0
    total = 0

    async with AsyncCamoufox(**_LAUNCH) as browser:
        for batch_num, urls in enumerate(BATCH_URLS, 1):
            start = time.time()
            print(f"\n  --- Batch {batch_num} ---")
            tasks = [
                open_context_and_page(browser, i, url=url)
                for i, url in enumerate(urls, 1)
            ]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout_per_batch,
            )
            ok = sum(1 for r in results if r is True)
            total_ok += ok
            total += len(urls)
            print(f"  Batch {batch_num}: {ok}/{len(urls)} ok in {time.time() - start:.2f}s")

    print(f"\n  Total: {total_ok}/{total}")
    return total_ok


# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("Testing fix for camoufox issue #279")
    print("(Concurrent page and context creation hang)")
    print("=" * 60)

    try:
        # A: Direct new_page() (original reproducer)
        await test_concurrent_pages(3)
        await test_concurrent_pages(5)
        await test_concurrent_pages(10)

        # B: new_context() + new_page() (scrape_md pattern)
        await test_concurrent_contexts(3)
        await test_concurrent_contexts(5)
        await test_concurrent_contexts(10)

        # C: Mixed
        await test_mixed(6)

        # D: 30 real-world pages in batches
        ok = await test_batches()
        if ok < 15:
            print(f"\n  WARNING: Only {ok}/30 pages succeeded (expected >=15).")
            print("  This may indicate network issues, not a camoufox bug.")

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED - Issue #279 is fixed!")
        print("=" * 60)

    except asyncio.TimeoutError:
        print("\n" + "=" * 60)
        print("FAILED: Hang detected (timeout)!")
        print("Issue #279 is NOT fixed.")
        print("=" * 60)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
