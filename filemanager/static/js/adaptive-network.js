(() => {
    "use strict";

    const connection =
        navigator.connection ||
        navigator.mozConnection ||
        navigator.webkitConnection;

    const LOW_SPEED_TYPES = new Set(["slow-2g", "2g", "3g"]);

    function isSlowConnection() {
        if (!connection) {
            // Browserले network information नदिएमा normal mode प्रयोग गर्ने
            return false;
        }

        return (
            connection.saveData === true ||
            LOW_SPEED_TYPES.has(connection.effectiveType) ||
            (
                typeof connection.downlink === "number" &&
                connection.downlink > 0 &&
                connection.downlink < 0.7
            )
        );
    }

    function loadOptionalStyles() {
        document.querySelectorAll("link[data-adaptive-css]").forEach(link => {
            if (link.dataset.loaded === "true") return;

            const stylesheet = document.createElement("link");
            stylesheet.rel = "stylesheet";
            stylesheet.href = link.dataset.adaptiveCss;
            stylesheet.onload = () => {
                link.dataset.loaded = "true";
            };

            document.head.appendChild(stylesheet);
        });
    }

    function applyNetworkMode() {
        const slow = isSlowConnection();

        document.documentElement.classList.toggle(
            "low-bandwidth",
            slow
        );

        document.documentElement.dataset.networkMode =
            slow ? "low" : "normal";

        if (!slow) {
            loadOptionalStyles();
        }

        // Optional UI elementsलाई मात्र hide/show गर्ने
        document.querySelectorAll("[data-low-bandwidth-hide]").forEach(el => {
            el.hidden = slow;
        });

        document.dispatchEvent(
            new CustomEvent("networkmodechange", {
                detail: {
                    mode: slow ? "low" : "normal"
                }
            })
        );
    }

    function start() {
        applyNetworkMode();

        if (connection && connection.addEventListener) {
            connection.addEventListener("change", applyNetworkMode);
        }

        // Browser ले connection change event नदिने अवस्थामा पनि
        // page visible हुँदा mode फेरि जाँच गर्ने
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) applyNetworkMode();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, {
            once: true
        });
    } else {
        start();
    }
})();
