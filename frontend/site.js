(() => {
  const VALID_SLOT_REGEX = /^\d{6,20}$/;
  const CONSENT_KEY = "mp360_cookie_consent_v1";
  const CONSENT_ACCEPTED = "accepted";
  const CONSENT_DECLINED = "declined";
  const CONTENT_READY_EVENT = "mp360:content-ready";
  const CONTENT_EMPTY_EVENT = "mp360:content-empty";
  let hasPublisherContent = false;
  let gaInitialized = false;

  const getConsent = () => {
    try {
      return localStorage.getItem(CONSENT_KEY);
    } catch (_) {
      return null;
    }
  };

  const setConsent = (value, banner) => {
    try {
      localStorage.setItem(CONSENT_KEY, value);
    } catch (_) {
      // Ignore storage errors in restricted browser modes.
    }
    if (banner) banner.hidden = true;
  };

  const getGaMeasurementId = () => {
    const cfg = window.MP360_SITE_CONFIG || {};
    const gaId = String(cfg.ga4MeasurementId || "").trim().toUpperCase();
    return /^G-[A-Z0-9]+$/.test(gaId) ? gaId : "";
  };

  const initGaIfConsented = (consentValue) => {
    if (gaInitialized) return;
    if (consentValue !== CONSENT_ACCEPTED) return;
    const gaId = getGaMeasurementId();
    if (!gaId) return;

    const tag = document.createElement("script");
    tag.async = true;
    tag.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(gaId)}`;
    document.head.appendChild(tag);

    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function gtag() {
      window.dataLayer.push(arguments);
    };
    window.gtag("js", new Date());
    window.gtag("config", gaId, {
      anonymize_ip: true,
      allow_google_signals: false,
    });
    gaInitialized = true;
  };

  const hideAdSurfaces = (state = "waiting-content") => {
    const surfaces = Array.from(document.querySelectorAll(".ad-surface"));
    surfaces.forEach((surface) => {
      surface.hidden = true;
      surface.setAttribute("data-ad-state", state);
    });
  };

  const showAdSurfaces = () => {
    const surfaces = Array.from(document.querySelectorAll(".ad-surface"));
    surfaces.forEach((surface) => {
      surface.hidden = false;
      surface.setAttribute("data-ad-state", "eligible");
    });
  };

  const hasValidSlot = (unit) => {
    const slot = String(unit.getAttribute("data-ad-slot") || "").trim();
    if (!VALID_SLOT_REGEX.test(slot)) {
      unit.remove();
      return false;
    }
    return true;
  };

  const mountAdUnits = () => {
    const units = Array.from(
      document.querySelectorAll(".ad-surface:not([hidden]) ins.adsbygoogle[data-ad-slot]")
    );
    if (!units.length) return;

    units.forEach((unit) => {
      if (unit.dataset.adMounted === "1") return;
      if (!hasValidSlot(unit)) return;

      try {
        (window.adsbygoogle = window.adsbygoogle || []).push({});
        unit.dataset.adMounted = "1";
      } catch (_) {
        // Ignore ad init errors for local preview.
      }
    });
  };

  const refreshAdVisibility = () => {
    if (!hasPublisherContent) {
      hideAdSurfaces("waiting-content");
      return;
    }
    showAdSurfaces();
    mountAdUnits();
  };

  const bindContentSignals = () => {
    document.addEventListener(CONTENT_READY_EVENT, (event) => {
      hasPublisherContent = Boolean(event?.detail?.contentRich);
      refreshAdVisibility();
    });

    document.addEventListener(CONTENT_EMPTY_EVENT, () => {
      hasPublisherContent = false;
      hideAdSurfaces("waiting-content");
    });
  };

  const setupConsentBanner = () => {
    const banner = document.getElementById("consentBanner");
    const consent = getConsent();
    initGaIfConsented(consent);

    if (!banner) return;

    const acceptBtn = document.getElementById("consentAccept");
    const declineBtn = document.getElementById("consentDecline");

    if (consent !== CONSENT_ACCEPTED && consent !== CONSENT_DECLINED) {
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }

    if (acceptBtn) {
      acceptBtn.addEventListener("click", () => {
        setConsent(CONSENT_ACCEPTED, banner);
        initGaIfConsented(CONSENT_ACCEPTED);
        refreshAdVisibility();
      });
    }
    if (declineBtn) {
      declineBtn.addEventListener("click", () => {
        setConsent(CONSENT_DECLINED, banner);
        refreshAdVisibility();
      });
    }
  };

  const boot = () => {
    setupConsentBanner();
    bindContentSignals();
    hideAdSurfaces("waiting-content");

    const root = document.getElementById("liveIntelligence");
    if (root && root.dataset.hasData === "1") {
      hasPublisherContent = true;
      refreshAdVisibility();
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
