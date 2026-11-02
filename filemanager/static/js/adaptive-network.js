(function () {
  "use strict";

  // Network speed detection thresholds
  const LOW_SPEED_TYPES = new Set(["slow-2g", "2g", "3g"]);
  const LOW_SPEED_THRESHOLD = 1.5; // Mbps

  function isSlowConnection() {
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!connection) return false;

    return (
      connection.saveData ||
      LOW_SPEED_TYPES.has(connection.effectiveType) ||
      (connection.downlink && connection.downlink < LOW_SPEED_THRESHOLD)
    );
  }

  function loadAdaptiveCSS() {
    const links = document.querySelectorAll('link[data-adaptive-css]');
    if (!links.length) return;

    // Only load optional CSS if connection is good
    if (!isSlowConnection()) {
      links.forEach(function (link) {
        const href = link.getAttribute('data-adaptive-css');
        if (href) {
          link.href = href;
          link.removeAttribute('data-adaptive-css');
        }
      });
    }
  }

  function hideOptionalUI() {
    const elements = document.querySelectorAll('[data-low-bandwidth-hide]');
    if (!elements.length) return;

    if (isSlowConnection()) {
      elements.forEach(function (el) {
        el.style.display = 'none';
      });
    }
  }

  // Run when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      loadAdaptiveCSS();
      hideOptionalUI();
    });
  } else {
    loadAdaptiveCSS();
    hideOptionalUI();
  }

  // Listen for connection changes
  if (navigator.connection) {
    navigator.connection.addEventListener('change', function () {
      loadAdaptiveCSS();
      hideOptionalUI();
    });
  }
})();
