"""
Post-install binary patcher for camoufox fork.

Applies Juggler fixes to the downloaded camoufox binary's omni.ja files.
Run after `camoufox fetch`:

    python -m camoufox.patch

Fixes applied:
  1. Serialize all newPage() calls (TargetRegistry.js in Resources/omni.ja)
  2. Install addons as permanent profile extensions instead of temporary
     addons (browser-init.js in browser/omni.ja)

See https://github.com/daijro/camoufox/issues/279
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile


def _find_omni_paths() -> tuple[str, str]:
    """Return (resources_omni, browser_omni) paths."""
    from .utils import launch_options

    opts = launch_options(headless=True)
    exe: str = opts["executable_path"]
    exe_dir = os.path.dirname(exe)

    # macOS layout
    resources = os.path.join(os.path.dirname(exe_dir), "Resources")
    if os.path.isdir(resources):
        return (
            os.path.join(resources, "omni.ja"),
            os.path.join(resources, "browser", "omni.ja"),
        )
    # Linux layout
    return (
        os.path.join(exe_dir, "omni.ja"),
        os.path.join(exe_dir, "browser", "omni.ja"),
    )


def _backup(path: str) -> None:
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)


def _patch_zip(omni_path: str, target: str, transform) -> bool:
    """Read *target* from the zip, run *transform(content)->content|None*, rewrite if changed."""
    with zipfile.ZipFile(omni_path, "r") as z:
        original = z.read(target).decode("utf-8")
        all_names = z.namelist()

    patched = transform(original)
    if patched is None or patched == original:
        return False

    _backup(omni_path)
    tmp = omni_path + ".tmp"
    with zipfile.ZipFile(omni_path, "r") as zin, zipfile.ZipFile(tmp, "w") as zout:
        for name in all_names:
            info = zin.getinfo(name)
            data = patched.encode("utf-8") if name == target else zin.read(name)
            zout.writestr(info, data)
    os.replace(tmp, omni_path)
    return True


# ── Patch 1: newPage serialization ──────────────────────────────────────────

_OLD_NEWPAGE_VARS = (
    "// This is a workaround for https://github.com/microsoft/playwright/issues/34586\n"
    "let didCreateFirstPage = false;\n"
    "let globalNewPageChain = Promise.resolve();"
)
_NEW_NEWPAGE_VARS = "let globalNewPageChain = Promise.resolve();"

_OLD_NEWPAGE_METHOD = (
    "  async newPage({browserContextId}) {\n"
    "    // When creating the very first page, we cannot create multiple in parallel.\n"
    "    // See https://github.com/microsoft/playwright/issues/34586.\n"
    "    if (didCreateFirstPage)\n"
    "      return this._newPageInternal({browserContextId});\n"
    "    const result = globalNewPageChain.then(() => this._newPageInternal({browserContextId}));\n"
    "    globalNewPageChain = result.catch(error => { /* swallow errors to keep chain running */ });\n"
    "    return result;\n"
    "  }"
)
_NEW_NEWPAGE_METHOD = (
    "  async newPage({browserContextId}) {\n"
    "    // Serialize all page creation to avoid Firefox hangs when\n"
    "    // multiple windows are opened concurrently.\n"
    "    // See https://github.com/microsoft/playwright/issues/34586\n"
    "    // and https://github.com/daijro/camoufox/issues/279.\n"
    "    const result = globalNewPageChain.then(() => this._newPageInternal({browserContextId}));\n"
    "    globalNewPageChain = result.catch(error => { /* swallow errors to keep chain running */ });\n"
    "    return result;\n"
    "  }"
)


def _patch_target_registry(content: str) -> str | None:
    if "didCreateFirstPage" not in content:
        return None  # already patched
    content = content.replace(_OLD_NEWPAGE_VARS, _NEW_NEWPAGE_VARS)
    content = content.replace(_OLD_NEWPAGE_METHOD, _NEW_NEWPAGE_METHOD)
    content = content.replace("    didCreateFirstPage = true;\n", "")
    return content


# ── Patch 2: profile-based addon install ────────────────────────────────────

_OLD_ADDON_INSTALL = """\
    // Install addons if specified
    let addonPaths = ChromeUtils.camouGetStringList("addons");
    if (addonPaths?.length) {
      Promise.all(addonPaths.map(path => this.installTemporaryAddon(path)))
        .then(addons => ChromeUtils.camouDebug("Installed " + addons.length + " addon(s)"))
        .catch(e => ChromeUtils.camouDebug("Failed to install addons: " + e));
    }"""

_NEW_ADDON_INSTALL = """\
    // Install addons by writing pointer files into the profile's extensions/
    // directory. This installs them as permanent profile extensions instead of
    // temporary addons. Temporary addons (installTemporaryAddon) corrupt when
    // concurrent pages are destroyed during active network interception.
    // See https://github.com/daijro/camoufox/issues/279.
    if (!gBrowserInit.__addonsInstalled) {
      gBrowserInit.__addonsInstalled = true;
      let addonPaths = ChromeUtils.camouGetStringList("addons");
      if (addonPaths?.length) {
        try {
          const profDir = Services.dirsvc.get("ProfD", Ci.nsIFile);
          const extDir = profDir.clone();
          extDir.append("extensions");
          if (!extDir.exists()) {
            extDir.create(Ci.nsIFile.DIRECTORY_TYPE, 0o755);
          }
          for (const addonPath of addonPaths) {
            const manifestFile = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
            manifestFile.initWithPath(addonPath);
            manifestFile.append("manifest.json");
            const stream = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
            stream.init(manifestFile, 0x01, 0, 0);
            const sis = Cc["@mozilla.org/scriptableinputstream;1"].createInstance(Ci.nsIScriptableInputStream);
            sis.init(stream);
            const manifestJSON = sis.read(sis.available());
            sis.close();
            const manifest = JSON.parse(manifestJSON);
            const addonId = manifest?.browser_specific_settings?.gecko?.id
                         || manifest?.applications?.gecko?.id;
            if (!addonId) {
              ChromeUtils.camouDebug("Skipping addon without ID: " + addonPath);
              continue;
            }
            const pointerFile = extDir.clone();
            pointerFile.append(addonId);
            if (!pointerFile.exists()) {
              const fos = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
              fos.init(pointerFile, 0x02 | 0x08 | 0x20, 0o644, 0);
              const data = addonPath + "\\n";
              fos.write(data, data.length);
              fos.close();
              ChromeUtils.camouDebug("Installed addon: " + addonId);
            }
          }
        } catch(e) {
          ChromeUtils.camouDebug("Failed to install addons: " + e);
        }
      }
    }"""


def _patch_browser_init(content: str) -> str | None:
    if "__addonsInstalled" in content:
        return None  # already patched
    if _OLD_ADDON_INSTALL not in content:
        return None  # unrecognised version
    return content.replace(_OLD_ADDON_INSTALL, _NEW_ADDON_INSTALL)


# ── Entry point ─────────────────────────────────────────────────────────────

def patch() -> None:
    resources_omni, browser_omni = _find_omni_paths()

    ok1 = _patch_zip(resources_omni, "chrome/juggler/content/TargetRegistry.js", _patch_target_registry)
    print(f"  TargetRegistry (newPage serialization): {'patched' if ok1 else 'already up-to-date'}")

    ok2 = _patch_zip(browser_omni, "chrome/browser/content/browser/browser-init.js", _patch_browser_init)
    print(f"  browser-init   (addon install fix):     {'patched' if ok2 else 'already up-to-date'}")

    if ok1 or ok2:
        print("Done — camoufox binary patched.")
    else:
        print("Nothing to do — all patches already applied.")


if __name__ == "__main__":
    patch()
