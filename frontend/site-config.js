window.MP360_SITE_CONFIG = Object.freeze({
  // Set this to your GA4 Measurement ID (example: G-ABC123XYZ9).
  ga4MeasurementId: "G-4RJFDR53TW",
  // Keep this token as reference; add the same value in index.html meta tag.
  gscVerificationToken: "",
  // Optional: public join link for your Telegram channel/group (used in UI CTAs).
  telegramJoinUrl: "https://t.me/+pxioeLtwyZVkMmQ1",
  // Cloudflare Turnstile (optional; recommended to enable on production).
  turnstileEnabled: false,
  turnstileSiteKey: ""
});

// Backward-compatible globals used by app.js.
window.TURNSTILE_ENABLED = Boolean(window.MP360_SITE_CONFIG?.turnstileEnabled);
window.TURNSTILE_SITE_KEY = String(window.MP360_SITE_CONFIG?.turnstileSiteKey || "");
