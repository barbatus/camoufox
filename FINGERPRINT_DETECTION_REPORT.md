# FingerprintJS Pro Detection Report: Camoufox vs Plain Playwright

## Test Setup

- **FingerprintJS Pro** v4.0.3 with server-side Events API
- **Camoufox** v146.0.1-alpha.50 (macOS arm64, headless with virtual display)
- **Plain Playwright Firefox** v146 (headless) as control
- Test URL: `https://httpbin.org/html` with injected FP script

## Results Comparison

| Signal | Camoufox | Plain Playwright | Winner |
|--------|----------|-----------------|--------|
| **Bot Detection** | `notDetected` | `bad` (type: `webdriver`) | **Camoufox** |
| Anti-detect Browser | `false` | `false` | Tie |
| Tampering | `true` (anomaly=1, medium) | `false` (anomaly=0, high) | **Playwright** |
| Developer Tools | `true` | `true` | Tie (both detected) |
| Suspect Score | 22 | 21 | Similar |
| Emulator | `false` | — | — |
| VPN/Proxy | `false` | — | — |
| Incognito | `false` | — | — |

## Key Finding

**Camoufox passes bot detection.** FingerprintJS does NOT flag Camoufox as a bot (`notDetected`), while plain Playwright is immediately caught as `bad` bot type `webdriver`.

However, Camoufox is flagged for **tampering** (anomalyScore=1, confidence=medium), which plain Playwright is NOT flagged for. This is counterintuitive — the anti-detect browser introduces a detectable anomaly that the vanilla browser doesn't have.

## Detailed Analysis

### 1. Bot Detection: PASSED ✅

Camoufox successfully evades FingerprintJS's bot detection. The `webdriver` flag that catches plain Playwright is not triggered. This means:
- `navigator.webdriver` is properly spoofed
- Playwright's Juggler protocol doesn't leak `webdriver` markers to content JS
- No headless browser artifacts detected

### 2. Tampering: FLAGGED ⚠️

Camoufox is flagged with `anomalyScore=1` and `confidence=medium`. Plain Playwright has `anomalyScore=0`. This means Camoufox's fingerprint spoofing introduces a detectable inconsistency.

**Likely causes** (based on FingerprintJS open-source code analysis):

1. **OS mismatch**: Camoufox reports `Windows 10` user agent but runs on macOS arm64. FingerprintJS checks consistency between:
   - User agent OS vs actual rendering behavior
   - Font metrics (Windows fonts rendered by macOS font engine)
   - WebGL renderer (Apple GPU but Windows UA)

2. **Font metrics inconsistency**: Camoufox spoofs the font list but can't change how the underlying OS renders fonts. A Windows font list rendered by macOS creates measurable metric differences.

3. **Canvas/WebGL artifacts**: The canvas noise seed produces deterministic randomization, but the base rendering is still from the real GPU. FingerprintJS may detect that the rendering artifacts don't match the declared GPU.

### 3. Developer Tools: FLAGGED ⚠️

Both Camoufox and Playwright are flagged for developer tools. This is the Juggler protocol — FingerprintJS detects the automation connection. This is expected and hard to fix since Juggler adds observable side effects to the page lifecycle.

**How FingerprintJS detects it** (from source code):
- Checks for `__playwright` or Juggler-specific global objects
- Detects timing anomalies in page lifecycle events
- Checks for the `remote-debugging` preference being set

### 4. Suspect Score: 22 (Borderline)

Score of 22 is borderline — many sites set their threshold at 20-25. The score combines:
- Tampering (+weight)
- Developer tools (+weight)
- High activity (3 requests in 24h, small +weight)

## Root Cause of Tampering Detection

Detailed signal analysis reveals the specific causes:

### 1. `navigator.webdriver` property EXISTS (even though value is false)
```
"webdriver" in navigator  → true   (should be false in normal Firefox)
navigator.webdriver       → false  (correct value, but property shouldn't exist)
```
FingerprintJS Pro checks for property **existence** on the prototype, not just the value. A normal non-automated Firefox doesn't have `webdriver` on the navigator at all. Camoufox adds it and sets it to `false`, which is a known anti-detect pattern that FP Pro flags as tampering.

### 2. Error stack traces — NOT a detection vector
```
# From page.evaluate(): contains "debugger eval code" (Playwright marker)
# From injected module script: CLEAN — "@https://httpbin.org/html line 7 > injectedScript:2:23"
```
FingerprintJS loads via ES module `import()`, so its error traces are **clean** — no `debugger eval code` or `chrome://juggler/` markers are visible. This is NOT the tampering signal source. Only code run via `page.evaluate()` leaks these markers, and FP doesn't use evaluate.

### 3. OS/Hardware inconsistencies
When fingerprint randomization selects a different OS than the actual host:
- UA says `Intel Mac OS X 10.15` but GPU is `Apple M1` (Intel CPU + M1 GPU mismatch)
- Previous run had `Windows NT 10.0` UA on macOS hardware

## Recommendations for Improving Camoufox

### High Priority (would reduce suspect score significantly)

0. **Remove `navigator.webdriver` property entirely**
   - Currently: property exists with value `false`
   - Should: property not exist at all (like normal Firefox)
   - This is likely the primary tampering signal
   - Fix in C++ patch: delete the property from `Navigator.webidl` instead of setting it to false

1. **Fix Accept-Encoding header override**
   - The C++ `network-patches.patch` overrides `SetAcceptEncodings()` but breaks ES module decompression
   - Camoufox contexts need `accept-encoding: identity` workaround to load JS modules
   - This is detectable — sites can check if the browser requests `identity` encoding

2. **Fix OS consistency**
   - When spoofing Windows UA on macOS hardware, ensure:
     - WebGL renderer matches Windows GPU strings (not Apple M-series)
     - Canvas rendering matches Windows font rasterization
     - Screen dimensions match common Windows monitors (not MacBook retina)
   - Consider running fingerprint generation based on the ACTUAL host OS, not random

3. **Reduce Juggler detectability**
   - FingerprintJS detects `developerTools: true` in both Camoufox and Playwright
   - Investigate if Juggler's `remote-debugging` pref can be masked
   - Check if there are observable global objects from Juggler in content scope

### Medium Priority

4. **Font rendering consistency**
   - Camoufox spoofs the font list but rendering metrics are from the real OS
   - Consider using the real host OS fonts instead of spoofing a different OS's font list
   - Or add font metrics normalization at the C++ level

5. **Canvas fingerprint consistency**
   - The seeded noise is deterministic but the base image is from real GPU
   - A Windows-declared browser producing Apple GPU canvas artifacts is detectable
   - Consider matching canvas noise patterns to the declared GPU

### Low Priority

6. **Timing side channels**
   - Camoufox's C++ patches add measurable overhead to fingerprinting API calls
   - Property access timing differs from native Firefox
   - This is extremely hard to fix without performance regressions

7. **Developer tools evasion**
   - Would require deep Juggler modifications
   - Risk of breaking Playwright functionality
   - May not be worth the effort since bot detection already passes

## Conclusion

**Camoufox is effective against FingerprintJS bot detection** — it's not flagged as a bot. The remaining tampering signal (anomalyScore=1) is likely from OS mismatch between spoofed identity and real hardware. For most scraping use cases, this is sufficient since sites primarily act on the `bot.result` field, not the tampering score.

The most impactful improvement would be matching the spoofed OS to the actual host OS (or at least matching the GPU/font rendering characteristics), which would eliminate the tampering signal entirely.
