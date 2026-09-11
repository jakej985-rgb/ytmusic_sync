#!/usr/bin/env python3
"""
Red Music Locker — Zero-Install Account Linker
Quickly links your YouTube Music account to Red Music Locker directly from an active
browser session without needing manual extension installation or DevTools.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

def resolve_server_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("RED_MUSIC_LOCKER_URL"):
        return os.environ["RED_MUSIC_LOCKER_URL"]
    if os.environ.get("YTM_SYNC_URL"):
        return os.environ["YTM_SYNC_URL"]
    for port in (8080, 6969):
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    return f"http://localhost:{port}"
        except Exception:
            pass
    return "http://localhost:8080"


CONFIG_API_KEY_PATHS = [
    Path.home() / ".config" / "red_music_locker" / "auth" / "api_key.txt",
    Path.home() / ".config" / "ytm_sync" / "auth" / "api_key.txt",
]


def get_api_key() -> str:
    # 1. Environment variable
    if os.environ.get("RED_MUSIC_LOCKER_API_KEY"):
        return os.environ["RED_MUSIC_LOCKER_API_KEY"].strip()
    if os.environ.get("YTM_SYNC_API_KEY"):
        return os.environ["YTM_SYNC_API_KEY"].strip()

    # 2. Check docker container red-music-locker or ytm-sync
    for container in ("red-music-locker", "ytm-sync"):
        try:
            import subprocess
            out = subprocess.check_output(
                ["docker", "exec", container, "cat", "/config/auth/api_key.txt"],
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).decode().strip()
            if out:
                return out
        except Exception:
            pass

    # 3. Host configuration file
    for p in CONFIG_API_KEY_PATHS:
        if p.exists():
            key = p.read_text(encoding="utf-8").strip()
            if key:
                return key

    return ""


async def extract_cookies_from_cdp(port: int = 9222):
    try:
        import websockets
    except ImportError:
        print("Installing websockets...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
        import websockets

    # 1. Check if Chrome debugging port is active
    tabs = None
    try:
        req = urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=2)
        tabs = json.loads(req.read())
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError):
        pass

    # 2. If not running with debugging port, launch dedicated authentication window
    if tabs is None:
        auth_dir = Path.home() / ".config" / "ytm_sync" / "browser_auth"
        auth_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n🔹 Launching YouTube Music authentication window...")
        print(f"   If prompted, sign in with the Google / YouTube Music account you wish to link.\n")
        
        import subprocess
        try:
            subprocess.Popen([
                "google-chrome",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={auth_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://music.youtube.com"
            ])
            # Wait up to 30 seconds for browser and login
            print("⏳ Waiting for browser connection...")
            for _ in range(15):
                time.sleep(1)
                try:
                    req = urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=1)
                    tabs = json.loads(req.read())
                    if tabs is not None:
                        print("✅ Browser window connected!")
                        break
                except Exception:
                    continue
        except Exception as e:
            print(f"Could not automatically launch Chrome: {e}")

    if tabs is None:
        raise RuntimeError(
            f"Could not connect to browser on port {port}.\n"
            "You can also open Red Music Locker in your browser (e.g. at http://localhost:8080 or http://localhost:6969),\n"
            "go to Settings -> 1. YouTube Music Account, and click 'Connect YouTube Music'."
        )

    # Find YouTube tab and wait for login if needed
    print("⏳ Checking for YouTube Music login session...")
    ytm_tab = None
    for _ in range(30):
        try:
            req = urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=1)
            tabs = json.loads(req.read())
        except Exception:
            pass

        for t in tabs:
            url = t.get("url", "")
            if "music.youtube.com" in url or "youtube.com" in url:
                ytm_tab = t
                break
        if ytm_tab:
            break
        time.sleep(1)

    if not ytm_tab:
        raise RuntimeError(
            "No YouTube Music tab found in your open browser.\n"
            "Please navigate to https://music.youtube.com in the opened window."
        )

    ws_url = ytm_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("Could not obtain debugger WebSocket URL for browser tab.")

    print("🔑 Capturing authorization credentials from YouTube Music...")
    cookies = []
    for attempt in range(60):
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({
                "id": 1,
                "method": "Network.getCookies",
                "params": {"urls": ["https://music.youtube.com", "https://youtube.com"]}
            }))
            res = await ws.recv()
            data = json.loads(res)
            cookies = data.get("result", {}).get("cookies", [])
            cookie_map = {c["name"]: c["value"] for c in cookies}
            sapisid = (
                cookie_map.get("SAPISID")
                or cookie_map.get("__Secure-3PAPISID")
                or cookie_map.get("__Secure-1PAPISID")
            )
            if sapisid:
                break
        if attempt % 5 == 0:
            print("   Waiting for YouTube Music sign-in to complete in browser...")
        await asyncio.sleep(2)

    if not cookies:
        raise RuntimeError("Could not capture YouTube cookies from browser.")

    return cookies


def build_raw_headers(cookies: list[dict]) -> str:
    cookie_map = {c["name"]: c["value"] for c in cookies}
    sapisid = (
        cookie_map.get("SAPISID")
        or cookie_map.get("__Secure-3PAPISID")
        or cookie_map.get("__Secure-1PAPISID")
    )
    if not sapisid:
        raise ValueError("SAPISID cookie not found. Please log into YouTube Music in your browser first.")

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    now = int(time.time())
    origin = "https://music.youtube.com"
    digest = hashlib.sha1(f"{now} {sapisid} {origin}".encode()).hexdigest()
    auth_header = f"SAPISIDHASH {now}_{digest}"

    return "\n".join([
        "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept: */*",
        "Accept-Language: en-US,en;q=0.9",
        f"Authorization: {auth_header}",
        f"Cookie: {cookie_str}",
        "X-Origin: https://music.youtube.com",
        "X-Goog-AuthUser: 0",
    ])


async def main():
    server_url = resolve_server_url()
    print(f"Connecting to Red Music Locker at {server_url}...")

    api_key = get_api_key()
    if not api_key:
        print("❌ Warning: API key could not be detected automatically.")
        api_key = input("Enter your Red Music Locker API Key: ").strip()

    print("Extracting YouTube Music session credentials from browser...")
    try:
        cookies = await extract_cookies_from_cdp()
        raw_headers = build_raw_headers(cookies)
    except Exception as e:
        print(f"\n❌ {e}\n")
        sys.exit(1)

    # Post to /api/auth/setup
    endpoint = f"{server_url.rstrip('/')}/api/auth/setup"
    payload = json.dumps({"raw_headers": raw_headers}).encode()

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            user = data.get("user_name") or data.get("account_name") or "Account"
            print("========================================================")
            print(f"🎉 Success! YouTube Music account linked as '{user}'")
            print("========================================================")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"❌ Failed to link: HTTP {e.code} - {body}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
