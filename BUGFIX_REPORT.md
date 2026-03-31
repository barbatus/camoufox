# Fix: Concurrent Page Creation Hang (Issue #279)

**Issue:** https://github.com/daijro/camoufox/issues/279

## Two Root Causes Found

### Root Cause 1: `newPage()` serialization bypass

**File:** `additions/juggler/TargetRegistry.js`

The `newPage()` method only serialized the *first* page creation. After `didCreateFirstPage` was set to `true` (during browser launch), all subsequent `new_page()` calls ran `_newPageInternal()` in parallel. Firefox cannot handle concurrent `Services.ww.openWindow()` calls, causing a deadlock.

**Fix:** Always serialize page creation through `globalNewPageChain`. Removed the `didCreateFirstPage` conditional bypass.

### Root Cause 2: uBlock Origin temporary addon corruption

**Discovery:** Even after fixing newPage serialization, the browser becomes permanently unresponsive after concurrent navigation timeouts (e.g., 4+ pages timing out at once).

**Root cause:** uBlock Origin is installed as a **temporary addon** via `installTemporaryAddon()` in `browser-init.js`. Temporary addons have different lifecycle behavior than permanent addons. When UBO's webRequest handlers are active during concurrent page destruction (from navigation timeouts), UBO's internal state corrupts, making ALL future network requests hang.

**Evidence:**
- Plain Playwright Firefox handles concurrent timeouts perfectly
- Camoufox WITHOUT UBO (`exclude_addons=[DefaultAddons.UBO]`) handles concurrent timeouts perfectly
- Camoufox WITH UBO: 4+ concurrent navigation timeouts permanently poison the browser

**Workaround:** Exclude UBO when using concurrent pages:
```python
from camoufox import DefaultAddons
AsyncCamoufox(headless=True, exclude_addons=[DefaultAddons.UBO])
```

## Changed Files

### Camoufox source (for full rebuilds)
- **`additions/juggler/TargetRegistry.js`** — Serialized all `newPage()` calls, removed `didCreateFirstPage`

### Installed binary (via omni.ja patching)
- **`Resources/omni.ja` → `chrome/juggler/content/TargetRegistry.js`** — Same fix as above
- **`Resources/omni.ja` → `chrome/juggler/content/TargetRegistry.js` (close method)** — Added `browsingContext.stop()` before tab removal

## How to Patch an Existing Installation

```bash
python3 -m venv /tmp/camou-venv
source /tmp/camou-venv/bin/activate
pip install camoufox[geoip]
camoufox fetch
python3 patch_omni.py     # Patches newPage serialization
```

## How to Verify

```bash
source /tmp/camou-venv/bin/activate
python3 test_concurrent_pages.py
```

## For Projects Using Camoufox Concurrently

If you open multiple pages concurrently on the same browser, apply these two fixes:

1. **Patch the camoufox binary** with `patch_omni.py` (fixes concurrent window creation hang)
2. **Exclude uBlock Origin** to prevent browser poisoning from concurrent timeouts:
   ```python
   from camoufox import DefaultAddons
   from camoufox.async_api import AsyncCamoufox

   async with AsyncCamoufox(
       headless=True,
       exclude_addons=[DefaultAddons.UBO],
   ) as browser:
       # Safe to use asyncio.gather with multiple pages
       await asyncio.gather(
           scrape(browser, url1),
           scrape(browser, url2),
           scrape(browser, url3),
       )
   ```

3. **(Optional) Limit concurrent pages** with an `asyncio.Semaphore` if scraping heavy sites:
   ```python
   sem = asyncio.Semaphore(4)

   async def scrape(browser, url):
       async with sem:
           page = await browser.new_page()
           # ...
   ```
