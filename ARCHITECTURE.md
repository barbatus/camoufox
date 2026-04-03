# Camoufox Architecture

## What Is Camoufox?

Camoufox is an anti-detect browser built on Firefox 146. It spoofs browser fingerprints at the **C++ implementation level** — not via JavaScript injection — making the spoofed properties virtually undetectable by anti-bot systems.

The core idea: when a website calls `navigator.userAgent`, `screen.width`, `WebGLRenderingContext.getParameter()`, or any other fingerprinting API, the response comes from Camoufox's C++ patches that read a JSON config, not from the real hardware. JavaScript-level detection (checking for monkey-patched getters, prototype chain tampering, etc.) fails because the values are native.

## How It Works

### Data Flow

```
Python wrapper
  → generates fingerprint config (JSON)
  → chunks into CAMOU_CONFIG_1, CAMOU_CONFIG_2, ... env vars
  → launches patched Firefox binary via Playwright

Firefox process
  → MaskConfig.hpp reads CAMOU_CONFIG env vars (C++ singleton, thread-safe)
  → 26 C++ patches query MaskConfig for their spoofed values
  → Juggler protocol connects Playwright to browser
  → every fingerprinting API returns spoofed data natively
```

### Build Pipeline

1. **Fetch** Firefox 146 source (~1.5GB tarball)
2. **Prepare** source directory, init git repo, tag as `unpatched`
3. **Copy additions** — Camoufox's own C++ files, Juggler protocol, config files
4. **Apply 26 patches** to Firefox source (modifying C++ implementations of Web APIs)
5. **Compile** with `./mach build` (30-60 min, produces `camoufox-bin`)
6. **Package** into portable ZIP with fonts, config, and binary

## Main Modules

### 1. MaskConfig (C++ Config Reader)

`additions/camoucfg/MaskConfig.hpp` — the bridge between Python config and C++ patches.

- Reads `CAMOU_CONFIG_1`, `CAMOU_CONFIG_2`, ... from environment
- Concatenates and parses as JSON via nlohmann::json
- Thread-safe singleton (`std::call_once`)
- Provides typed accessors: `GetString()`, `GetUint32()`, `GetBool()`, `GetRect()`, `GetNested()`
- Every fingerprint patch module calls these to get its spoofed values

### 2. Fingerprint Patches (26 C++ Patches)

Each patch modifies Firefox's C++ source to intercept a fingerprinting API and return values from MaskConfig instead of real hardware data.

**Core fingerprinting:**

| Patch | What it spoofs | Why it matters |
|-------|---------------|----------------|
| `navigator-spoofing.patch` | `navigator.userAgent`, `.platform`, `.oscpu`, `.hardwareConcurrency` | Primary browser identification |
| `screen-spoofing.patch` | `screen.width/height`, `window.innerWidth/Height`, `devicePixelRatio` | Screen fingerprint |
| `fingerprint-injection.patch` | Window dimensions at C++ level (`nsGlobalWindowInner`) | Prevents dimension leaks |
| `webgl-spoofing.patch` | WebGL renderer, vendor, parameters, shader precision | GPU fingerprint (most unique) |
| `canvas-spoofing.patch` | Canvas pixel data with seeded noise | Canvas fingerprint |
| `font-hijacker.patch` | Available font list via `gfxPlatformFontList` | OS detection via fonts |
| `font-list-spoofing.patch` | Font metrics (letter spacing, glyph widths) | Advanced font fingerprinting |
| `audio-context-spoofing.patch` | `AudioContext.sampleRate`, latency, channel count | Audio fingerprint |
| `audio-fingerprint-manager.patch` | Playback rate, voice lists | Audio device fingerprint |
| `timezone-spoofing.patch` | `Intl.DateTimeFormat`, `Date.getTimezoneOffset()` | Location inference |
| `geolocation-spoofing.patch` | Geolocation API coordinates | Direct location |
| `locale-spoofing.patch` | `navigator.language`, `Accept-Language` header | Location/language inference |
| `webrtc-ip-spoofing.patch` | WebRTC ICE candidates (local IP) | Real IP leak |
| `media-device-spoofing.patch` | `navigator.mediaDevices.enumerateDevices()` | Device fingerprint |
| `speech-voices-spoofing.patch` | `speechSynthesis.getVoices()` | OS/locale detection |
| `network-patches.patch` | `User-Agent`, `Accept-Language`, `Accept-Encoding` HTTP headers | Network-level fingerprint |

**Stealth / anti-detection:**

| Patch | Purpose |
|-------|---------|
| `browser-init.patch` | Browser startup: window sizing, cursor follower, addon installation, certificate import |
| `chromeutil.patch` | Exposes `ChromeUtils.camouGet*()` functions for reading MaskConfig from **privileged chrome-context JS only** (browser internals, Juggler) — not accessible from web page JavaScript |
| `disable-remote-subframes.patch` | Prevents remote subframe detection |
| `shadow-root-bypass.patch` | Allows Juggler to access closed shadow DOMs |
| `cross-process-storage.patch` | IPC-based `RoverfoxStorageManager` for consistent cross-process spoofing |
| `force-default-pointer.patch` | Hides headless pointer type |
| `no-css-animations.patch` | Removes CSS animations (performance + less suspicious) |
| `global-style-sheets.patch` | Custom stylesheet injection capability |
| `config.patch` | Integrates `camoufox.cfg` and `local-settings.js` into the build |

### 3. Juggler Protocol

`additions/juggler/` — Firefox's Playwright protocol implementation.

Juggler is what lets Playwright control Firefox. It's not CDP (Chrome DevTools Protocol) — it's a custom protocol originally created by the Puppeteer team for Firefox.

Key properties:
- **Page isolation**: Juggler gets an isolated copy of the page DOM. Playwright's reads/writes don't affect the real page JavaScript can see.
- **Native input routing**: Inputs go through Firefox's real user input handlers (indistinguishable from manual interaction)
- **Frame isolation**: Cross-process frames prevent context leakage between tabs

Important files:
- `TargetRegistry.js` — manages page targets, window creation, downloads
- `protocol/Dispatcher.js` — message dispatch between Playwright client and browser
- `protocol/BrowserHandler.js` — handles `Browser.newPage`, `Browser.close`, etc.
- `protocol/PageHandler.js` — handles `Page.navigate`, `Page.close`, etc.
- `content/FrameTree.js` — frame lifecycle and navigation tracking
- `NetworkObserver.js` — network request interception and monitoring

### 4. Python Wrapper

`pythonlib/camoufox/` — the user-facing package.

**`utils.py` — `launch_options()`**: The core function that:
1. Generates a realistic fingerprint via BrowserForge (or accepts user-provided)
2. Selects consistent fonts, WebGL params, audio config for the target OS
3. Resolves geolocation → timezone → locale from proxy IP (via GeoIP database)
4. Serializes config to JSON, chunks into env vars (2047 chars on Windows, 32767 on Linux)
5. Returns a dict of Playwright launch options

**`fingerprints.py`**: Generates per-context fingerprints with unique seeds for canvas, audio, and font noise. Each browser context gets a distinct identity.

**`async_api.py` / `sync_api.py`**: Context managers that wrap Playwright's `firefox.launch()` with Camoufox config:
```python
async with AsyncCamoufox(headless=True) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
```

### 5. Firefox Preferences

`settings/camoufox.cfg` — 767 lines of Firefox preferences:
- **Debloat** (~200 prefs): Disable telemetry, ads, recommendations, Pocket, Shield, crash reports
- **Juggler**: Synthetic input security bypass, process isolation, cookie behavior
- **Memory**: Disable prefetch, DNS prediction, enable disk cache
- **Security**: Disable safe browsing, block list downloads, extension restrictions

## Why All These Patches?

### The Fingerprinting Problem

Modern anti-bot systems (Cloudflare, PerimeterX/HUMAN, DataDome, Akamai) create a **browser fingerprint** — a unique identifier derived from dozens of browser properties. A real Chrome on Windows 11 with an NVIDIA GPU produces a specific combination of:

- User agent string
- Screen dimensions
- GPU renderer string (`ANGLE (NVIDIA, NVIDIA GeForce RTX 4090...)`)
- Available fonts (Windows system fonts)
- Canvas rendering artifacts (GPU-specific pixel patterns)
- Audio processing characteristics
- WebRTC local IPs
- Timezone/locale
- And 50+ more signals

If any single property is inconsistent (e.g., macOS user agent but Windows fonts), the fingerprint is flagged as synthetic.

### Why JavaScript-Level Spoofing Fails

Simple approaches like `Object.defineProperty(navigator, 'userAgent', ...)` are detectable:

1. **Prototype chain inspection**: Anti-bot JS checks if `navigator.__proto__.userAgent` returns a getter, or if the property descriptor is writable
2. **Cross-frame consistency**: An iframe's `navigator.userAgent` must match the parent — JS patches often miss iframes
3. **Web Worker isolation**: Workers have their own `navigator` object that JS patches can't reach
4. **Performance timing**: JS patches add measurable overhead to property access
5. **Internal consistency**: `navigator.userAgent` says Chrome but `window.chrome` is undefined

### Why C++ Level Works

Camoufox patches the actual C++ functions that implement these APIs. When Firefox's JavaScript engine calls `navigator.userAgent`, it invokes `nsNavigator::GetUserAgent()` in C++, which Camoufox patches to return the spoofed value. There's no JavaScript layer to detect — the spoofing is invisible to any JavaScript inspection.

## Would a Simple Playwright Wrapper Work?

**Short answer: No, not for serious anti-bot evasion.**

A wrapper that just sets `user_agent` in Playwright's context options:
- Only spoofs the HTTP `User-Agent` header and `navigator.userAgent`
- Leaves 50+ other fingerprinting surfaces untouched
- WebGL renderer/vendor still shows real hardware
- Canvas fingerprint still unique to real GPU
- Font list reveals real OS
- Screen dimensions inconsistent with spoofed OS
- Audio fingerprint unchanged
- Timezone/locale from system settings

**What you'd need at minimum** (without C++ patches):
- `playwright-stealth` — patches some JavaScript-level detections
- Custom init scripts to override `navigator.*` properties
- WebGL parameter override via extension
- Canvas noise injection via extension

**What you'd still miss**:
- Cross-frame consistency
- Web Worker spoofing
- Font metrics (not just font list — actual glyph measurements)
- Audio processing characteristics
- Native C++ API calls that bypass JavaScript entirely
- HTTP/2 fingerprint (TLS, cipher suite order)
- Subprocess/iframe isolation

**When a simple wrapper IS enough**:
- Sites with basic bot detection (checking only user agent + headless flag)
- Sites using only JavaScript-level checks (no canvas/WebGL/font fingerprinting)
- Rate-limited scraping where you're not triggering advanced detection
- Internal tools / sites without anti-bot protection

**When you need Camoufox**:
- Sites with Cloudflare Bot Management, PerimeterX/HUMAN, DataDome, Akamai Bot Manager
- High-volume scraping where fingerprint rotation matters
- Sites that actively check for automation (banking, ticketing, social media)
- Any site that uses canvas/WebGL/font fingerprinting
