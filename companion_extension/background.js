// Background Service Worker for YTM Sync Account Linker

async function sha1(str) {
  const enc = new TextEncoder();
  const data = enc.encode(str);
  const hashBuffer = await crypto.subtle.digest("SHA-1", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function extractAuthHeaders() {
  const cookies = await chrome.cookies.getAll({ domain: "youtube.com" });
  if (!cookies || cookies.length === 0) {
    throw new Error("No YouTube cookies found. Please make sure you are signed in to music.youtube.com.");
  }

  const cookieMap = {};
  for (const c of cookies) {
    cookieMap[c.name] = c.value;
  }

  const sapisid = cookieMap["SAPISID"] || cookieMap["__Secure-3PAPISID"] || cookieMap["__Secure-1PAPISID"];
  if (!sapisid) {
    throw new Error("SAPISID cookie not found. Please ensure you are fully logged into YouTube Music.");
  }

  const cookieStr = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  const timestamp = Math.floor(Date.now() / 1000);
  const origin = "https://music.youtube.com";
  const digest = await sha1(`${timestamp} ${sapisid} ${origin}`);
  const authHeader = `SAPISIDHASH ${timestamp}_${digest}`;

  const headers = [
    `User-Agent: ${navigator.userAgent}`,
    `Accept: */*`,
    `Accept-Language: en-US,en;q=0.9`,
    `Authorization: ${authHeader}`,
    `Cookie: ${cookieStr}`,
    `X-Origin: https://music.youtube.com`,
    `X-Goog-AuthUser: 0`,
  ].join("\n");

  return headers;
}

async function linkAccount(callbackUrl, sessionId) {
  const headers = await extractAuthHeaders();
  const endpoint = `${callbackUrl.replace(/\/+$/, "")}/api/auth/callback`;

  const resp = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      raw_headers: headers,
    }),
  });

  if (!resp.ok) {
    const errData = await resp.json().catch(() => ({}));
    throw new Error(errData.detail || `Server responded with ${resp.status}`);
  }

  return await resp.json();
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "LINK_ACCOUNT") {
    linkAccount(request.callbackUrl, request.sessionId)
      .then((data) => sendResponse({ success: true, data }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true; // Keep channel open for async response
  }

  if (request.action === "GET_HEADERS") {
    extractAuthHeaders()
      .then((headers) => sendResponse({ success: true, headers }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }
});
