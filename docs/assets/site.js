/* ATM documentation - shared chrome and behavior.
   Renders header, sidebar, footer and on-page TOC from one nav config,
   then wires theme switching, the mobile drawer, copy buttons, light code
   highlighting and reveal-on-scroll. Article content is static HTML; only the
   surrounding chrome depends on this script. */
(function () {
  "use strict";

  document.documentElement.classList.add("js");

  /* ---- site structure: one source of truth for nav + pager ---- */
  var PAGES = [
    { id: "index",        file: "index.html",        title: "Overview",                 group: "Getting started" },
    { id: "install",      file: "install.html",      title: "Installation",             group: "Getting started" },
    { id: "quickstart",   file: "quickstart.html",   title: "Quick start",              group: "Getting started" },
    { id: "connect",      file: "connect.html",      title: "Connect an AI client",     group: "Getting started" },
    { id: "permissions",  file: "permissions.html",  title: "Permissions",              group: "Concepts" },
    { id: "capabilities", file: "capabilities.html", title: "Capabilities & pass-through", group: "Concepts" },
    { id: "mesa",         file: "mesa.html",         title: "MESA",                     group: "Concepts" },
    { id: "panel",        file: "panel.html",        title: "Panel guide",              group: "Guides" },
    { id: "tools",        file: "tools.html",        title: "Tools reference",          group: "Reference" },
    { id: "admin-api",    file: "admin-api.html",    title: "Admin API",                group: "Reference" },
    { id: "security",     file: "security.html",     title: "Security",                 group: "Reference" },
    { id: "operations",   file: "operations.html",   title: "Operations",               group: "Reference" }
  ];
  var REPO = "https://github.com/sfox38/ATM";

  var SVG = {
    sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.3 6.3 0 0 0 10.5 10.5z"/></svg>',
    auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" fill="none"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>',
    menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    github: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 1.5A10.5 10.5 0 0 0 8.7 22c.52.1.71-.23.71-.5v-1.9c-2.9.63-3.52-1.24-3.52-1.24-.48-1.2-1.16-1.53-1.16-1.53-.95-.65.07-.64.07-.64 1.05.07 1.6 1.08 1.6 1.08.93 1.6 2.45 1.14 3.05.87.1-.68.36-1.14.66-1.4-2.32-.26-4.76-1.16-4.76-5.16 0-1.14.4-2.07 1.07-2.8-.1-.27-.46-1.33.1-2.78 0 0 .88-.28 2.88 1.07a9.9 9.9 0 0 1 5.24 0c2-1.35 2.87-1.07 2.87-1.07.57 1.45.21 2.51.11 2.78.67.73 1.07 1.66 1.07 2.8 0 4.01-2.45 4.9-4.78 5.15.38.32.71.95.71 1.92v2.85c0 .28.19.61.72.5A10.5 10.5 0 0 0 12 1.5z"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2.2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
  };

  var here = document.body.getAttribute("data-page") || "index";
  function rel(file) { return file; }

  /* ---- theme ---- */
  var THEME_KEY = "atm-docs-theme";
  function applyTheme(t) {
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
  }
  function currentTheme() { try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; } }
  applyTheme(currentTheme());
  function cycleTheme() {
    var order = ["auto", "light", "dark"];
    var next = order[(order.indexOf(currentTheme()) + 1) % 3];
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    applyTheme(next);
    paintThemeBtn();
  }
  function paintThemeBtn() {
    var btn = document.getElementById("theme-btn");
    if (!btn) return;
    var t = currentTheme();
    btn.innerHTML = t === "light" ? SVG.sun : t === "dark" ? SVG.moon : SVG.auto;
    btn.setAttribute("aria-label", "Theme: " + t + ". Click to change.");
    btn.setAttribute("title", "Theme: " + t);
  }

  /* ---- header ---- */
  function buildHeader() {
    var h = document.getElementById("site-header");
    if (!h) return;
    var mark =
      '<img class="mark" src="assets/atm-logo.png" alt="" width="30" height="30" />';
    h.innerHTML =
      '<button class="icon-btn menu-btn" id="menu-btn" aria-label="Open navigation" aria-expanded="false">' + SVG.menu + '</button>' +
      '<a class="brand" href="' + rel("index.html") + '">' + mark +
        '<span class="brand-text"><span>ATM</span><span class="brand-sub">Token Management</span></span>' +
      '</a>' +
      '<span class="header-spacer"></span>' +
      '<div class="header-actions">' +
        '<a class="header-link" href="' + REPO + '" target="_blank" rel="noopener">' + SVG.github + '<span class="hide-sm">GitHub</span></a>' +
        '<button class="icon-btn" id="theme-btn" aria-label="Change theme"></button>' +
      '</div>';
    paintThemeBtn();
    document.getElementById("theme-btn").addEventListener("click", cycleTheme);
    document.getElementById("menu-btn").addEventListener("click", toggleNav);
  }

  /* ---- sidebar ---- */
  function buildSidebar() {
    var s = document.getElementById("sidebar");
    if (!s) return;
    var groups = [];
    PAGES.forEach(function (p) {
      var g = groups.filter(function (x) { return x.name === p.group; })[0];
      if (!g) { g = { name: p.group, items: [] }; groups.push(g); }
      g.items.push(p);
    });
    var idx = 0;
    var html = '<nav aria-label="Documentation">';
    groups.forEach(function (g) {
      html += '<div class="nav-group"><p class="nav-group-title">' + g.name + '</p><ul class="nav-list">';
      g.items.forEach(function (p) {
        idx++;
        var active = p.id === here;
        var num = ("0" + idx).slice(-2);
        html += '<li><a class="nav-link" href="' + rel(p.file) + '"' + (active ? ' aria-current="page"' : "") + '>' +
                '<span class="ix">' + num + '</span>' + p.title + '</a>';
        if (active) html += '<ul class="nav-sub" id="nav-sub"></ul>';
        html += '</li>';
      });
      html += '</ul></div>';
    });
    html += '</nav>';
    s.innerHTML = html;
  }

  /* ---- footer ---- */
  function buildFooter() {
    var f = document.getElementById("site-footer");
    if (!f) return;
    var year = new Date().getFullYear();
    f.innerHTML =
      '<div class="footer-inner">' +
        '<p>Advanced Token Management &middot; scoped, audited, revocable access to Home Assistant for AI agents.</p>' +
        '<div class="footer-links">' +
          '<a href="' + rel("index.html") + '">Docs home</a>' +
          '<a href="' + REPO + '" target="_blank" rel="noopener">GitHub</a>' +
          '<a href="' + REPO + '/issues" target="_blank" rel="noopener">Report an issue</a>' +
        '</div>' +
      '</div>';
  }

  /* ---- pager (prev / next) ---- */
  function buildPager() {
    var host = document.getElementById("pager");
    if (!host) return;
    var i = PAGES.map(function (p) { return p.id; }).indexOf(here);
    var prev = i > 0 ? PAGES[i - 1] : null;
    var next = i < PAGES.length - 1 ? PAGES[i + 1] : null;
    var html = "";
    if (prev) html += '<a href="' + rel(prev.file) + '"><span class="dir">Previous</span><span class="pg-title">' + prev.title + '</span></a>';
    if (next) html += '<a class="next" href="' + rel(next.file) + '"><span class="dir">Next</span><span class="pg-title">' + next.title + '</span></a>';
    host.innerHTML = html;
  }

  /* ---- headings: ensure ids + anchor links, build TOC ---- */
  function slugify(s) {
    return s.toLowerCase().trim()
      .replace(/[^\w\s-]/g, "")
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-");
  }
  function buildToc() {
    var main = document.querySelector(".content");
    var toc = document.getElementById("toc");
    var subnav = document.getElementById("nav-sub");
    if (!main) return;
    var hs = main.querySelectorAll("h2, h3");
    var items = [];
    var used = {};
    hs.forEach(function (h) {
      if (h.classList.contains("no-toc") || h.closest(".no-toc")) return;
      var id = h.id;
      if (!id) {
        id = slugify(h.textContent) || "section";
        if (used[id]) { var n = 2; while (used[id + "-" + n]) n++; id = id + "-" + n; }
        h.id = id;
      }
      used[id] = true;
      h.classList.add("anchor");
      if (!h.querySelector(".anchor-link")) {
        var a = document.createElement("a");
        a.className = "anchor-link";
        a.href = "#" + id;
        a.setAttribute("aria-label", "Link to this section");
        a.textContent = "#";
        h.appendChild(a);
      }
      items.push({ id: id, text: h.firstChild ? h.textContent.replace(/#$/, "").trim() : "", level: h.tagName === "H3" ? 3 : 2 });
    });
    if (toc && items.length >= 3 && document.body.hasAttribute("data-toc")) {
      document.getElementById("layout").classList.add("has-toc");
      var html = '<p class="toc-title">On this page</p><ul>';
      items.forEach(function (it) {
        html += '<li><a class="' + (it.level === 3 ? "h3" : "") + '" href="#' + it.id + '">' + escapeHtml(it.text) + '</a></li>';
      });
      html += '</ul>';
      toc.innerHTML = html;
    } else if (toc) {
      toc.remove();
    }
    if (subnav) {
      subnav.innerHTML = items.filter(function (it) { return it.level === 2; })
        .map(function (it) { return '<li><a href="#' + it.id + '">' + escapeHtml(it.text) + '</a></li>'; }).join("");
    }
    setupScrollSpy(items);
  }

  function setupScrollSpy(items) {
    var links = {};
    document.querySelectorAll(".toc a").forEach(function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    if (!Object.keys(links).length) return;
    var active = null;
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          if (active) active.classList.remove("active");
          active = links[e.target.id];
          if (active) active.classList.add("active");
        }
      });
    }, { rootMargin: "-12% 0px -78% 0px", threshold: 0 });
    items.forEach(function (it) { var el = document.getElementById(it.id); if (el) obs.observe(el); });
  }

  /* ---- mobile nav drawer ---- */
  function toggleNav() {
    var open = document.body.classList.toggle("nav-open");
    var btn = document.getElementById("menu-btn");
    if (btn) { btn.setAttribute("aria-expanded", String(open)); btn.innerHTML = open ? SVG.close : SVG.menu; }
  }
  function closeNav() {
    if (!document.body.classList.contains("nav-open")) return;
    document.body.classList.remove("nav-open");
    var btn = document.getElementById("menu-btn");
    if (btn) { btn.setAttribute("aria-expanded", "false"); btn.innerHTML = SVG.menu; }
  }

  /* ---- code blocks: copy + light highlight ---- */
  function escapeHtml(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  function highlight(code, lang) {
    var src = escapeHtml(code);
    if (lang === "bash" || lang === "shell" || lang === "sh") {
      return src.split("\n").map(function (line) {
        var m = line.match(/^(\s*)(#.*)$/);
        if (m) return m[1] + '<span class="tok-comment">' + m[2] + "</span>";
        line = line.replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;|"[^"]*"|'[^']*')/g, '<span class="tok-str">$1</span>');
        line = line.replace(/(^|\s)(--?[a-zA-Z][\w-]*)/g, '$1<span class="tok-flag">$2</span>');
        return line;
      }).join("\n");
    }
    if (lang === "json") {
      src = src.replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="tok-key">$1</span>$2');
      src = src.replace(/:(\s*)(&quot;[^&]*?&quot;)/g, ':$1<span class="tok-str">$2</span>');
      src = src.replace(/\b(true|false|null)\b/g, '<span class="tok-flag">$1</span>');
      src = src.replace(/\b(-?\d+\.?\d*)\b/g, '<span class="tok-flag">$1</span>');
      return src;
    }
    if (lang === "http" || lang === "routes") {
      return src.replace(/^(GET|POST|PUT|PATCH|DELETE)(\/[A-Z]+)*/gm, '<span class="tok-key">$&</span>')
                .replace(/(\{[^}]+\})/g, '<span class="tok-flag">$1</span>');
    }
    return src;
  }

  function enhanceCode() {
    document.querySelectorAll("pre.code").forEach(function (pre) {
      var codeEl = pre.querySelector("code");
      if (!codeEl) return;
      var raw = codeEl.textContent;
      var lang = pre.getAttribute("data-lang") || "";
      var label = pre.getAttribute("data-label") || lang || "code";

      var wrap = document.createElement("div");
      wrap.className = "codeblock";
      var head = document.createElement("div");
      head.className = "codeblock-head";
      head.innerHTML = '<span class="codeblock-lang">' + escapeHtml(label) + '</span><span class="spacer"></span>';
      var btn = document.createElement("button");
      btn.className = "copy-btn";
      btn.type = "button";
      btn.innerHTML = SVG.copy + "<span>Copy</span>";
      btn.addEventListener("click", function () {
        navigator.clipboard.writeText(raw).then(function () {
          btn.classList.add("copied");
          btn.innerHTML = SVG.check + "<span>Copied</span>";
          setTimeout(function () { btn.classList.remove("copied"); btn.innerHTML = SVG.copy + "<span>Copy</span>"; }, 1600);
        });
      });
      head.appendChild(btn);

      if (lang) codeEl.innerHTML = highlight(raw, lang);
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(head);
      wrap.appendChild(pre);
    });
  }

  /* ---- reveal on scroll ---- */
  function setupReveal() {
    var els = document.querySelectorAll(".reveal");
    if (!els.length) return;
    if (!("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      els.forEach(function (el) { el.classList.add("in"); });
      return;
    }
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); obs.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    els.forEach(function (el) { obs.observe(el); });
    // failsafe: never leave content hidden if the observer never fires for an element
    window.addEventListener("load", function () {
      setTimeout(function () { els.forEach(function (el) { el.classList.add("in"); }); }, 2200);
    });
  }

  /* ---- tools page: search + filter ---- */
  function setupToolFilter() {
    var search = document.getElementById("tool-search-input");
    var chips = document.querySelectorAll(".filter-chip");
    if (!search && !chips.length) return;
    var tools = Array.prototype.slice.call(document.querySelectorAll(".tool"));
    var groups = Array.prototype.slice.call(document.querySelectorAll(".tool-group"));
    var activeCap = "all";
    var noResults = document.getElementById("no-results");

    function apply() {
      var q = (search ? search.value : "").trim().toLowerCase();
      var any = false;
      tools.forEach(function (t) {
        var text = t.getAttribute("data-search") || t.textContent.toLowerCase();
        var caps = t.getAttribute("data-cap") || "";
        var matchQ = !q || text.toLowerCase().indexOf(q) !== -1;
        var matchCap = activeCap === "all" || caps.split(" ").indexOf(activeCap) !== -1;
        var show = matchQ && matchCap;
        t.classList.toggle("is-hidden", !show);
        if (show) any = true;
      });
      groups.forEach(function (g) {
        var visible = g.querySelectorAll(".tool:not(.is-hidden)").length;
        g.classList.toggle("is-hidden", visible === 0);
      });
      if (noResults) noResults.classList.toggle("is-hidden", any);
    }
    if (search) search.addEventListener("input", apply);
    chips.forEach(function (c) {
      c.addEventListener("click", function () {
        chips.forEach(function (x) { x.setAttribute("aria-pressed", "false"); });
        c.setAttribute("aria-pressed", "true");
        activeCap = c.getAttribute("data-cap") || "all";
        apply();
      });
    });
  }

  /* ---- global listeners ---- */
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-close-nav]")) closeNav();
    var navlink = e.target.closest(".sidebar a");
    if (navlink && window.innerWidth <= 1000) closeNav();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeNav(); });

  /* ---- boot ---- */
  function boot() {
    buildHeader();
    buildSidebar();
    buildFooter();
    buildPager();
    buildToc();
    enhanceCode();
    setupReveal();
    setupToolFilter();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
