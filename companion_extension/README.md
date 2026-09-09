# YTM Sync — Companion Extension

A lightweight browser companion for **1-click YouTube Music Account Linking** with your self-hosted YTM Sync server.

---

## Why Is This Needed?

Google does not provide public OAuth scopes for YouTube Music cloud locker uploads. Uploading private music files requires browser-session authentication tokens (`SAPISIDHASH` and session cookies). 

Previously, users had to press `F12`, open Developer Tools, inspect network requests, copy raw header lines, and paste them manually into YTM Sync.

With this companion extension:
- **No F12 / DevTools needed.**
- **No header copying or pasting.**
- **1-Click account linking directly to your server.**

---

## Installation (Chrome, Brave, Edge, Chromium)
 
### Option 1: Automated 1-Click Installer (Linux)
Run the automated installation script from the repository root:
```bash
./scripts/install_companion_extension.sh
```
This automatically registers the extension with Chrome/Chromium and configures desktop launchers so the extension loads automatically without needing manual "Load unpacked" file picking.

### Option 2: Zero-Install Auto-Linker (No Extension Needed)
If you are already signed into YouTube Music in your browser:
```bash
./scripts/auto_link_ytm.py
```
This automatically reads the active session and connects your account in 1 second.

### Option 3: Manual Installation
1. Open your browser's extension manager:
   - Chrome / Brave: `chrome://extensions`
   - Edge: `edge://extensions`
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select the `companion_extension` directory in this repository.

---

## Usage

### Method A: Direct Sync from YTM Sync (Recommended)
1. In YTM Sync, navigate to **Settings** $\rightarrow$ **1. YouTube Music Connection**.
2. Click **Connect YouTube Music**.
3. A browser tab will open to `music.youtube.com`.
4. If signed in, the companion extension will automatically capture authorization and securely link your account.
5. Return to YTM Sync — your account will be **Connected**.

### Method B: 1-Click from Extension Popup
1. Sign in to [music.youtube.com](https://music.youtube.com).
2. Click the **YTM Sync Linker** icon in your browser toolbar.
3. Enter your YTM Sync server address (e.g. `http://localhost:8080` or `http://192.168.1.100:8080`).
4. Click **Link Account Now**.
