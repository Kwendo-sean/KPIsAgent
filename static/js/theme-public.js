/* ─────────────────────────────────────────────────────────────────────────
   Theme engine for the public pages (landing, login, register).

   Uses the same contract as the dashboard — the `data-theme` attribute on
   <html> and the `kpi-theme` localStorage key — so a preference set anywhere
   in the product applies everywhere.

   Loaded synchronously from <head> so the theme is applied before first
   paint. A deferred/async load here would show a flash of the wrong theme.
   ───────────────────────────────────────────────────────────────────────── */
(function () {
    "use strict";

    var STORAGE_KEY = "kpi-theme";
    var root = document.documentElement;
    var listeners = [];

    // Line/edge colours for the three.js scenes. These sit on a transparent
    // canvas over the page background, so the mint tones that read well on
    // near-black wash out completely on a light ground and have to change.
    var PALETTE_3D = {
        dark:  { edge: 0x7BD3AE, edge2: 0xA7E8CA, solid: 0x1A4D3E, dot: 0x7BD3AE, opacity: 1.00 },
        light: { edge: 0x1A4D3E, edge2: 0x2F7A55, solid: 0x1A4D3E, dot: 0x2F7A55, opacity: 1.60 }
    };

    function stored() {
        try {
            return localStorage.getItem(STORAGE_KEY);
        } catch (e) {
            return null; // private mode / blocked storage
        }
    }

    function persist(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (e) { /* non-fatal: the theme still applies for this page */ }
    }

    function prefersDark() {
        return window.matchMedia
            && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }

    function current() {
        return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
    }

    /* Apply a theme. `remember` is false for the initial load and for
       OS-driven changes, so that following the OS stays possible — writing to
       storage on load would permanently pin the theme after one visit. */
    function apply(theme, remember) {
        root.setAttribute("data-theme", theme);
        if (remember) persist(theme);
        syncButtons(theme);
        for (var i = 0; i < listeners.length; i++) {
            try {
                listeners[i](theme, PALETTE_3D[theme]);
            } catch (e) {
                /* a failing scene must not break the toggle */
            }
        }
    }

    /* Inline SVG rather than a Font Awesome glyph: the landing page does not
       load Font Awesome, and the icon must render on all three pages. */
    var SVG_OPEN = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" '
        + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        + 'stroke-linejoin="round" aria-hidden="true" focusable="false">';
    var ICON_SUN = SVG_OPEN + '<circle cx="12" cy="12" r="4"/>'
        + '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
        + 'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
    var ICON_MOON = SVG_OPEN + '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

    function syncButtons(theme) {
        var dark = theme === "dark";
        var buttons = document.querySelectorAll("[data-theme-toggle]");
        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            // Show the action, not the state: in dark mode offer the sun.
            btn.innerHTML = dark ? ICON_SUN : ICON_MOON;
            btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
            btn.setAttribute("title", dark ? "Light mode" : "Dark mode");
        }
    }

    // Apply immediately, before the body paints.
    apply(stored() || (prefersDark() ? "dark" : "light"), false);

    function wire() {
        syncButtons(current());
        document.addEventListener("click", function (ev) {
            var btn = ev.target.closest && ev.target.closest("[data-theme-toggle]");
            if (!btn) return;
            ev.preventDefault();
            apply(current() === "dark" ? "light" : "dark", true);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", wire);
    } else {
        wire();
    }

    // Follow the OS while the visitor has not made an explicit choice.
    if (window.matchMedia) {
        var mq = window.matchMedia("(prefers-color-scheme: dark)");
        var onChange = function (e) {
            if (!stored()) apply(e.matches ? "dark" : "light", false);
        };
        if (mq.addEventListener) mq.addEventListener("change", onChange);
        else if (mq.addListener) mq.addListener(onChange);
    }

    window.KPITheme = {
        get: current,
        set: function (theme) { apply(theme === "dark" ? "dark" : "light", true); },
        palette3D: function () { return PALETTE_3D[current()]; },
        /* Register a scene for retinting. Called immediately with the current
           palette so callers do not need separate init and update paths. */
        onChange: function (fn) {
            if (typeof fn !== "function") return;
            listeners.push(fn);
            fn(current(), PALETTE_3D[current()]);
        },
        /* Retint three.js materials when the theme changes.
           specs: [{ material, role: 'edge'|'edge2'|'solid'|'dot', opacity }]
           `opacity` is the material's base value; it is scaled per theme so
           lines stay legible on a light ground. */
        tintMaterials: function (specs) {
            if (!specs || !specs.length) return;
            this.onChange(function (theme, p) {
                for (var i = 0; i < specs.length; i++) {
                    var s = specs[i];
                    if (!s || !s.material || !s.material.color) continue;
                    var hex = p[s.role];
                    s.material.color.setHex(hex === undefined ? p.edge : hex);
                    if (typeof s.opacity === "number") {
                        s.material.opacity = Math.min(1, s.opacity * p.opacity);
                    }
                    s.material.needsUpdate = true;
                }
            });
        }
    };
})();
