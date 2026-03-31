#!/usr/bin/env python3
"""
Patches camoufox's omni.ja to fix concurrent page creation hang.
See: https://github.com/daijro/camoufox/issues/279

The bug: TargetRegistry.newPage() only serializes page creation for the
very first page. After that, concurrent newPage() calls run in parallel,
causing Firefox to hang due to concurrent window creation.

The fix: Always serialize page creation through globalNewPageChain.
"""

import zipfile
import shutil
import sys
import os
import tempfile


def find_omni_ja():
    """Find the omni.ja file in the camoufox installation."""
    try:
        from camoufox.utils import launch_options
        opts = launch_options(headless=True)
        exe = opts['executable_path']
        # Navigate from executable to Resources/omni.ja
        # macOS: .../Camoufox.app/Contents/MacOS/camoufox -> .../Contents/Resources/omni.ja
        # Linux: .../camoufox -> .../omni.ja (same dir)
        exe_dir = os.path.dirname(exe)
        # Try macOS layout first
        resources = os.path.join(os.path.dirname(exe_dir), 'Resources')
        omni = os.path.join(resources, 'omni.ja')
        if os.path.exists(omni):
            return omni
        # Try Linux layout
        omni = os.path.join(exe_dir, 'omni.ja')
        if os.path.exists(omni):
            return omni
        print(f"Cannot find omni.ja near executable: {exe}")
        sys.exit(1)
    except Exception as e:
        print(f"Error finding camoufox: {e}")
        sys.exit(1)


def patch_target_registry(content: str) -> str:
    """Apply the fix to TargetRegistry.js content."""
    # Remove didCreateFirstPage variable
    old_vars = (
        "// This is a workaround for https://github.com/microsoft/playwright/issues/34586\n"
        "let didCreateFirstPage = false;\n"
        "let globalNewPageChain = Promise.resolve();"
    )
    new_vars = "let globalNewPageChain = Promise.resolve();"
    content = content.replace(old_vars, new_vars)

    # Fix the newPage method to always serialize
    old_method = (
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
    new_method = (
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
    content = content.replace(old_method, new_method)

    # Remove didCreateFirstPage = true from _newPageInternal
    content = content.replace("    didCreateFirstPage = true;\n", "")

    return content


def patch_omni_ja(omni_path: str):
    """Patch the omni.ja file in-place."""
    target_file = 'chrome/juggler/content/TargetRegistry.js'

    # Read original
    with zipfile.ZipFile(omni_path, 'r') as zin:
        original = zin.read(target_file).decode('utf-8')
        all_names = zin.namelist()

    # Check if already patched
    if 'didCreateFirstPage' not in original:
        print("Already patched! Nothing to do.")
        return

    # Apply patch
    patched = patch_target_registry(original)

    if patched == original:
        print("ERROR: Patch did not apply. The file format may have changed.")
        sys.exit(1)

    # Verify patch removed the problematic code
    assert 'didCreateFirstPage' not in patched, "Patch failed: didCreateFirstPage still present"

    # Create backup
    backup = omni_path + '.bak'
    if not os.path.exists(backup):
        shutil.copy2(omni_path, backup)
        print(f"Backup created: {backup}")

    # Repackage omni.ja
    # omni.ja uses stored (no compression) for performance
    tmp_path = omni_path + '.tmp'
    with zipfile.ZipFile(omni_path, 'r') as zin:
        with zipfile.ZipFile(tmp_path, 'w') as zout:
            for name in all_names:
                info = zin.getinfo(name)
                if name == target_file:
                    zout.writestr(info, patched.encode('utf-8'))
                else:
                    zout.writestr(info, zin.read(name))

    # Replace original
    os.replace(tmp_path, omni_path)
    print(f"Patched: {omni_path}")
    print(f"Modified: {target_file}")
    print("Fix: Serialized all newPage() calls to prevent concurrent window creation hang")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        omni_path = sys.argv[1]
    else:
        omni_path = find_omni_ja()

    print(f"Patching: {omni_path}")
    patch_omni_ja(omni_path)
    print("\nDone! The fix for issue #279 has been applied.")
