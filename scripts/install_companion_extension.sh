#!/usr/bin/env bash
# ==============================================================================
# YTM Sync — Automated Companion Extension Installer for Linux Browsers
# ==============================================================================
# Automatically installs and enables the YTM Sync Companion Extension for
# Google Chrome, Chromium, Brave, and Microsoft Edge on Linux without
# requiring manual "Developer mode" or "Load unpacked" file picking.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
EXT_DIR="${REPO_ROOT}/companion_extension"

if [ ! -d "${EXT_DIR}" ]; then
    echo "❌ Error: Extension directory not found at: ${EXT_DIR}"
    exit 1
fi

echo "========================================================"
echo "  YTM Sync Companion Extension Automated Installer"
echo "========================================================"
echo "Extension source: ${EXT_DIR}"
echo ""

# 1. Desktop Entry Configuration (~/.local/share/applications)
# Adding --load-extension ensures the extension is loaded automatically
# whenever the browser is launched from the desktop menu, dock, or CLI.
DESKTOP_DIR="${HOME}/.local/share/applications"
mkdir -p "${DESKTOP_DIR}"

install_desktop_wrapper() {
    local browser_bin="$1"
    local desktop_name="$2"
    local sys_desktop="/usr/share/applications/${desktop_name}"
    local user_desktop="${DESKTOP_DIR}/${desktop_name}"

    if command -v "${browser_bin}" >/dev/null 2>&1 && [ -f "${sys_desktop}" ]; then
        echo "🔹 Configuring ${browser_bin} desktop launcher..."
        cp "${sys_desktop}" "${user_desktop}"
        
        # Modify Exec= lines to include --load-extension
        sed -i -E "s|Exec=([^ ]+)(.*)|Exec=\1 --load-extension=\"${EXT_DIR}\"\2|g" "${user_desktop}"
        echo "   ✅ Added --load-extension to ${user_desktop}"
    fi
}

# Configure for popular Chromium-based browsers
install_desktop_wrapper "google-chrome" "google-chrome.desktop"
install_desktop_wrapper "google-chrome-stable" "google-chrome.desktop"
install_desktop_wrapper "chromium" "chromium.desktop"
install_desktop_wrapper "chromium-browser" "chromium-browser.desktop"
install_desktop_wrapper "brave-browser" "brave-browser.desktop"
install_desktop_wrapper "microsoft-edge" "microsoft-edge.desktop"

# Refresh desktop database
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
fi

# 2. Chrome External Extensions Registration
# Chrome checks ~/.config/google-chrome/External Extensions/<id>.json
CHROME_EXT_DIR="${HOME}/.config/google-chrome/External Extensions"
mkdir -p "${CHROME_EXT_DIR}"

# Pack extension if google-chrome is available to get consistent ID
if command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; then
    CHROME_CMD="$(command -v google-chrome || command -v google-chrome-stable)"
    CRX_FILE="${REPO_ROOT}/companion_extension.crx"
    PEM_FILE="${REPO_ROOT}/companion_extension.pem"

    if [ ! -f "${CRX_FILE}" ] || [ ! -f "${PEM_FILE}" ]; then
        echo "🔹 Packaging extension CRX..."
        "${CHROME_CMD}" --pack-extension="${EXT_DIR}" >/dev/null 2>&1 || true
    fi

    if [ -f "${PEM_FILE}" ]; then
        # Calculate extension ID
        EXT_ID=$(python3 -c "
import hashlib
from cryptography.hazmat.primitives import serialization
with open('${PEM_FILE}', 'rb') as f:
    key = serialization.load_pem_private_key(f.read(), password=None)
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    digest = hashlib.sha256(pub).hexdigest()[:32]
    print(''.join(chr(ord('a') + int(c, 16)) for c in digest))
" 2>/dev/null || echo "")

        if [ -n "${EXT_ID}" ]; then
            cat <<EOF > "${CHROME_EXT_DIR}/${EXT_ID}.json"
{
  "external_crx": "${CRX_FILE}",
  "external_version": "1.1.0"
}
EOF
            echo "   ✅ Registered External Extension: ${CHROME_EXT_DIR}/${EXT_ID}.json"
        fi
    fi
fi

echo ""
echo "========================================================"
echo "  🎉 Installation Complete!"
echo "========================================================"
echo "The YTM Sync Companion Extension is now configured to load"
echo "automatically when you open Google Chrome / Chromium."
echo ""
echo "To test or open Chrome with the extension right now:"
echo "  google-chrome --load-extension=\"${EXT_DIR}\""
echo "========================================================"
