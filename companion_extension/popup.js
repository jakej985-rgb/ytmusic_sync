// Popup logic for YTM Sync Companion Extension

document.addEventListener("DOMContentLoaded", async () => {
  const serverInput = document.getElementById("server-url");
  const linkBtn = document.getElementById("link-btn");
  const copyBtn = document.getElementById("copy-headers-btn");
  const statusMsg = document.getElementById("status-msg");
  const btnText = document.getElementById("btn-text");

  // Load saved server URL
  chrome.storage.local.get(["ytm_sync_server"], (res) => {
    if (res.ytm_sync_server) {
      serverInput.value = res.ytm_sync_server;
    } else {
      serverInput.value = "http://localhost:8080";
    }
  });

  function showStatus(text, isError = false) {
    statusMsg.textContent = text;
    statusMsg.className = isError ? "error" : "success";
    statusMsg.style.display = "block";
  }

  function hideStatus() {
    statusMsg.style.display = "none";
  }

  // Handle 1-click Link Account
  linkBtn.addEventListener("click", async () => {
    hideStatus();
    const serverUrl = serverInput.value.trim().replace(/\/+$/, "");
    if (!serverUrl) {
      showStatus("Please enter your YTM Sync server URL", true);
      return;
    }

    chrome.storage.local.set({ ytm_sync_server: serverUrl });

    linkBtn.disabled = true;
    btnText.textContent = "Connecting...";

    try {
      // 1. Request new auth session from YTM Sync
      const startResp = await fetch(`${serverUrl}/api/auth/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ origin_url: serverUrl }),
      });

      if (!startResp.ok) {
        throw new Error(`Failed to start session on server (${startResp.status})`);
      }

      const session = await startResp.json();
      const sessionId = session.session_id;
      const extToken = session.extension_token;

      // 2. Call background script to capture headers and complete auth
      chrome.runtime.sendMessage(
        {
          action: "LINK_ACCOUNT",
          callbackUrl: serverUrl,
          sessionId: sessionId,
          token: extToken,
        },
        (response) => {
          linkBtn.disabled = false;
          btnText.textContent = "Link Account Now";

          if (chrome.runtime.lastError || !response || !response.success) {
            const err = response ? response.error : chrome.runtime.lastError.message;
            showStatus(err || "Linking failed. Are you logged in to music.youtube.com?", true);
            return;
          }

          const userName = response.data && response.data.user_name ? response.data.user_name : "account";
          showStatus(`✓ Successfully linked as ${userName}! You can now return to YTM Sync.`);
        }
      );
    } catch (err) {
      linkBtn.disabled = false;
      btnText.textContent = "Link Account Now";
      showStatus(err.message || "Failed to reach YTM Sync server", true);
    }
  });

  // Handle Copy Headers Fallback
  copyBtn.addEventListener("click", () => {
    hideStatus();
    copyBtn.disabled = true;
    copyBtn.textContent = "Extracting...";

    chrome.runtime.sendMessage({ action: "GET_HEADERS" }, async (response) => {
      copyBtn.disabled = false;
      copyBtn.textContent = "Copy Headers (Manual Fallback)";

      if (chrome.runtime.lastError || !response || !response.success) {
        const err = response ? response.error : chrome.runtime.lastError.message;
        showStatus(err || "Failed to extract headers. Make sure you are logged in to music.youtube.com.", true);
        return;
      }

      try {
        await navigator.clipboard.writeText(response.headers);
        showStatus("✓ Headers copied to clipboard! You can paste them into Advanced Authentication.");
      } catch (_) {
        showStatus("Failed to write to clipboard. Please check browser permissions.", true);
      }
    });
  });
});
