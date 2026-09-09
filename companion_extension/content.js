// Content script injected into music.youtube.com
(function () {
  const searchParams = new URLSearchParams(window.location.search);
  const hashParams = new URLSearchParams(window.location.hash.replace(/^#\??/, ""));

  const sessionId = searchParams.get("ytm_sync_session") || hashParams.get("ytm_sync_session");
  const extToken = searchParams.get("token") || hashParams.get("token");
  const callbackUrl = hashParams.get("callback") || searchParams.get("ytm_callback") || searchParams.get("cb") || searchParams.get("callback") || hashParams.get("ytm_callback") || hashParams.get("cb");

  if (!sessionId || !callbackUrl) {
    return;
  }

  let banner, headerRow, headerTitle, spinner, statusEl, buttonRow;

  function ensureBanner() {
    if (banner) return;
    if (!document.body) return;

    const style = document.createElement("style");
    style.textContent = `@keyframes ytm-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`;
    (document.head || document.body).appendChild(style);

    banner = document.createElement("div");
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

    headerRow = document.createElement("div");
    headerRow.style.cssText = "display: flex; align-items: center; gap: 12px; margin-bottom: 8px;";

    spinner = document.createElement("div");
    spinner.id = "ytm-sync-spinner";
    spinner.style.cssText = "width: 12px; height: 12px; border: 2px solid #ff0000; border-top-color: transparent; border-radius: 50%; animation: ytm-spin 1s linear infinite;";

    headerTitle = document.createElement("strong");
    headerTitle.style.fontSize = "15px";
    headerTitle.textContent = "YTM Sync Account Linker";

    headerRow.appendChild(spinner);
    headerRow.appendChild(headerTitle);
    banner.appendChild(headerRow);

    statusEl = document.createElement("div");
    statusEl.id = "ytm-sync-linker-status";
    statusEl.style.cssText = "color: #aaa; font-size: 13px; line-height: 1.4;";
    statusEl.textContent = "Linking YouTube Music account to your YTM Sync server...";
    banner.appendChild(statusEl);

    buttonRow = document.createElement("div");
    buttonRow.id = "ytm-sync-button-row";
    buttonRow.style.cssText = "margin-top: 12px; display: none; gap: 8px;";
    banner.appendChild(buttonRow);

    document.body.appendChild(banner);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ensureBanner, { once: true });
  } else {
    ensureBanner();
  }

  function showDismissButton() {
    if (!buttonRow) return;
    buttonRow.style.display = "flex";
    const dismissBtn = document.createElement("button");
    dismissBtn.textContent = "Dismiss";
    dismissBtn.style.cssText = "background: rgba(255,255,255,0.1); border: none; color: #fff; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px;";
    dismissBtn.addEventListener("click", () => banner?.remove());
    buttonRow.appendChild(dismissBtn);
  }

  // Send message to background service worker to capture cookies and post callback
  chrome.runtime.sendMessage(
    {
      action: "LINK_ACCOUNT",
      callbackUrl: callbackUrl,
      sessionId: sessionId,
      token: extToken,
    },
    (response) => {
      ensureBanner();
      if (!banner) return;

      if (chrome.runtime.lastError || !response || !response.success) {
        const err = response ? response.error : (chrome.runtime.lastError ? chrome.runtime.lastError.message : "Unknown error");
        if (spinner) spinner.style.display = "none";
        if (headerTitle) {
          headerTitle.textContent = "Linking Failed";
          headerTitle.style.color = "#ff5555";
        }
        banner.style.borderColor = "rgba(255, 85, 85, 0.4)";
        if (statusEl) {
          statusEl.textContent = String(err || "Failed to link account");
          statusEl.style.color = "#ff8888";
        }
        showDismissButton();
        return;
      }

      const userName = response.data && response.data.user_name ? response.data.user_name : "Account";
      if (spinner) spinner.remove();

      // Checkmark icon safely created
      const checkSpan = document.createElement("span");
      checkSpan.style.cssText = "font-size: 18px; color: #4caf50;";
      checkSpan.textContent = "✓";
      if (headerRow) headerRow.prepend(checkSpan);

      if (headerTitle) {
        headerTitle.textContent = "Account Linked!";
        headerTitle.style.color = "#4caf50";
      }
      banner.style.borderColor = "rgba(76, 175, 80, 0.5)";

      if (statusEl) {
        statusEl.textContent = "";
        statusEl.style.color = "#ddd";

        const line1 = document.createTextNode("Connected as ");
        const userBold = document.createElement("strong");
        userBold.textContent = userName;
        const line2 = document.createElement("div");
        line2.style.marginTop = "4px";
        line2.textContent = "You can now close this tab and return to YTM Sync.";

        statusEl.appendChild(line1);
        statusEl.appendChild(userBold);
        statusEl.appendChild(line2);
      }

      showDismissButton();
    }
  );
})();
