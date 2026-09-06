// Content script injected into music.youtube.com
(function () {
  const urlParams = new URLSearchParams(window.location.search);
  const sessionId = urlParams.get("ytm_sync_session");
  const callbackUrl = urlParams.get("callback");

  if (!sessionId || !callbackUrl) {
    return;
  }

  // Create UI overlay banner notifying user that linking is in progress
  const banner = document.createElement("div");
  banner.id = "ytm-sync-linker-banner";
  banner.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 9999999;
    background: #1e1e2e;
    color: #fff;
    padding: 16px 20px;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    border: 1px solid rgba(255,255,255,0.15);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    max-width: 360px;
    transition: all 0.3s ease;
  `;

  banner.innerHTML = `
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
      <div style="width: 12px; height: 12px; border: 2px solid #ff0000; border-top-color: transparent; border-radius: 50%; animation: ytm-spin 1s linear infinite;"></div>
      <strong style="font-size: 15px;">YTM Sync Account Linker</strong>
    </div>
    <div id="ytm-sync-linker-status" style="color: #aaa; font-size: 13px; line-height: 1.4;">
      Linking YouTube Music account to your YTM Sync server...
    </div>
  `;

  const style = document.createElement("style");
  style.textContent = `@keyframes ytm-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`;
  document.head.appendChild(style);
  document.body.appendChild(banner);

  const statusEl = document.getElementById("ytm-sync-linker-status");

  // Send message to background service worker to capture cookies and post callback
  chrome.runtime.sendMessage(
    {
      action: "LINK_ACCOUNT",
      callbackUrl: callbackUrl,
      sessionId: sessionId,
    },
    (response) => {
      if (chrome.runtime.lastError || !response || !response.success) {
        const err = response ? response.error : chrome.runtime.lastError.message;
        statusEl.innerHTML = `<span style="color: #ff5555; font-weight: bold;">Linking Failed:</span> ${err || "Unknown error"}`;
        banner.style.borderColor = "rgba(255, 85, 85, 0.4)";
        return;
      }

      const userName = response.data && response.data.user_name ? response.data.user_name : "Account";
      banner.style.borderColor = "rgba(76, 175, 80, 0.5)";
      banner.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
          <span style="font-size: 18px; color: #4caf50;">✓</span>
          <strong style="font-size: 15px; color: #4caf50;">Account Linked!</strong>
        </div>
        <div style="color: #ddd; font-size: 13px; line-height: 1.4;">
          Connected as <strong>${userName}</strong>.<br>
          You can now close this tab and return to YTM Sync.
        </div>
        <div style="margin-top: 12px; display: flex; gap: 8px;">
          <button id="ytm-sync-close-banner" style="background: rgba(255,255,255,0.1); border: none; color: #fff; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px;">Dismiss</button>
        </div>
      `;

      document.getElementById("ytm-sync-close-banner").addEventListener("click", () => {
        banner.remove();
      });
    }
  );
})();
