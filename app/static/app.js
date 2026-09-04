/* Shared client-side behavior: form hydration (re-run after a fragment is
   injected via fetch, since innerHTML does not execute <script> tags) plus
   the single-page apply flow (student panel -> review panel) that replaces
   full-page navigation when its containers are present on the page. Pages
   without those containers (form.html / review.html loaded directly, or
   with JS off) fall back to normal navigation -- the server decides which
   to send based on the X-Partial header these fetches set. */
window.App = (function () {
  function hydratePeriodSync(root) {
    root.querySelectorAll(".js-period-start").forEach(function (startInput) {
      if (startInput.dataset.hydrated) return;
      var form = startInput.closest("form");
      var endInput = form && form.querySelector(".js-period-end");
      if (!endInput) return;
      startInput.dataset.hydrated = "1";
      function syncEndMin() {
        endInput.min = startInput.value;
        if (endInput.value && endInput.value < startInput.value) {
          endInput.value = startInput.value;
        }
      }
      startInput.addEventListener("change", syncEndMin);
      syncEndMin();
    });
  }

  function hydrateDateSync(root) {
    // Each 수업일 choice is a checkbox "chip" with its own 시작/끝 교시
    // selects right below it (multiple dates can be checked for one
    // course at once). Unchecking a date just disables its period
    // selects, both so they read as inactive and so their stale values
    // don't get submitted as if that date were still picked.
    root.querySelectorAll(".date-chip-block").forEach(function (block) {
      if (block.dataset.hydrated) return;
      block.dataset.hydrated = "1";
      var checkbox = block.querySelector(".date-chip-check");
      var selects = block.querySelectorAll(".date-chip-periods select");
      if (!checkbox || !selects.length) return;
      function sync() {
        selects.forEach(function (sel) { sel.disabled = !checkbox.checked; });
      }
      checkbox.addEventListener("change", sync);
      sync();
    });
  }

  function hydrateSubmitGuard(root) {
    root.querySelectorAll("form.js-guard-submit").forEach(function (form) {
      if (form.dataset.hydrated) return;
      form.dataset.hydrated = "1";
      form.addEventListener("submit", function () {
        var btn = form.querySelector("button[type=submit]");
        if (btn && !btn.disabled) {
          btn.disabled = true;
          btn.textContent = "처리 중...";
        }
      });
    });
  }

  // Delete buttons (and the full-list view) stay locked until the admin
  // token has actually been checked against the server -- .js-admin-token
  // is the token input, .js-admin-verified a hidden flag flipped to "1"
  // only by a successful /records/admin_view confirm, .js-admin-gated the
  // buttons it gates (row/bulk/per-student delete). Editing the token
  // after confirming un-verifies it, so a stale token can't stay "trusted".
  function applyAdminGate(scopeEl) {
    var doc = scopeEl.ownerDocument || document;
    var verifiedInput = doc.querySelector(".js-admin-verified");
    var verified = !!verifiedInput && verifiedInput.value === "1";
    scopeEl.querySelectorAll(".js-admin-gated").forEach(function (btn) {
      btn.disabled = !verified;
    });
  }

  function hydrateAdminGate(root) {
    root.querySelectorAll(".js-admin-token").forEach(function (input) {
      if (input.dataset.hydrated) return;
      input.dataset.hydrated = "1";
      input.addEventListener("input", function () {
        var doc = input.ownerDocument;
        var verifiedInput = doc.querySelector(".js-admin-verified");
        if (verifiedInput) verifiedInput.value = "";
        applyAdminGate(doc);
      });
    });
    applyAdminGate(root);
  }

  function hydrateSelectAll(root) {
    root.querySelectorAll(".js-select-all").forEach(function (master) {
      if (master.dataset.hydrated) return;
      master.dataset.hydrated = "1";
      master.addEventListener("change", function () {
        var scope = master.closest("form") || document;
        scope.querySelectorAll(".js-row-check").forEach(function (cb) {
          cb.checked = master.checked;
        });
      });
    });
  }

  // Click a sortable <th> (currently just 사용 기록's 수업일 column) to
  // re-order its table's rows by that column's text, toggling asc/desc on
  // repeat clicks. class_date cells are plain ISO "YYYY-MM-DD" text, which
  // sorts correctly as a string, so no date parsing is needed.
  function hydrateSortableTable(root) {
    root.querySelectorAll(".js-sort-date").forEach(function (th) {
      if (th.dataset.hydrated) return;
      th.dataset.hydrated = "1";
      th.addEventListener("click", function () {
        var table = th.closest("table");
        var tbody = table && table.querySelector("tbody");
        if (!tbody) return;
        var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
        var dir = th.dataset.dir === "asc" ? "desc" : "asc";
        th.dataset.dir = dir;
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        rows.sort(function (a, b) {
          var av = (a.children[idx] && a.children[idx].textContent || "").trim();
          var bv = (b.children[idx] && b.children[idx].textContent || "").trim();
          if (av === bv) return 0;
          var cmp = av < bv ? -1 : 1;
          return dir === "asc" ? cmp : -cmp;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        table.querySelectorAll(".js-sort-date").forEach(function (h) {
          h.classList.remove("sort-asc", "sort-desc");
        });
        th.classList.add(dir === "asc" ? "sort-asc" : "sort-desc");
      });
    });
  }

  function hydrateAll(root) {
    hydratePeriodSync(root);
    hydrateDateSync(root);
    hydrateSubmitGuard(root);
    hydrateAdminGate(root);
    hydrateSelectAll(root);
    hydrateSortableTable(root);
  }

  function showPanel(el) {
    el.hidden = false;
  }

  function setLoading(el) {
    el.hidden = false;
    el.innerHTML = '<p class="loading">불러오는 중...</p>';
  }

  function clearPanel(id) {
    var el = document.getElementById(id);
    if (el) {
      el.hidden = true;
      el.innerHTML = "";
    }
  }

  // Generic "submit this form into that panel" used for both apply-flow
  // steps (student -> review -> confirm): each fetches its target panel
  // with X-Partial so it gets back just the fragment, and clears whatever
  // comes after it in the flow since that content is now stale.
  function wireStepForm(formClass, panelId, clearIds) {
    document.addEventListener("submit", function (e) {
      var form = e.target.closest("form." + formClass);
      if (!form) return;
      var panel = document.getElementById(panelId);
      if (!panel) return; // fallback: normal submit -> full page for this step
      e.preventDefault();
      clearIds.forEach(clearPanel);
      setLoading(panel);
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      fetch(form.action, {
        method: "POST",
        headers: { "X-Partial": "1" },
        body: new FormData(form),
      })
        .then(function (res) { return res.text(); })
        .then(function (html) {
          panel.innerHTML = html;
          showPanel(panel);
          hydrateAll(panel);
        });
    });
  }

  function initSpaNav() {
    document.addEventListener("click", function (e) {
      var link = e.target.closest("a.js-open-student");
      if (!link) return;
      var panel = document.getElementById("student-panel");
      if (!panel) return; // no SPA container on this page -- let it navigate normally
      e.preventDefault();
      clearPanel("review-panel");
      clearPanel("confirm-panel");
      setLoading(panel);
      fetch(link.href, { headers: { "X-Partial": "1" } })
        .then(function (res) { return res.text(); })
        .then(function (html) {
          panel.innerHTML = html;
          showPanel(panel);
          hydrateAll(panel);
          panel.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    wireStepForm("js-review-form", "review-panel", ["confirm-panel"]);
    wireStepForm("js-confirm-form", "confirm-panel", []);

    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-close-panel]");
      if (!btn) return;
      var target = document.getElementById(btn.dataset.closePanel);
      if (target) {
        target.hidden = true;
        target.innerHTML = "";
      }
    });

    // "처음으로" after a final download: back to a blank 학번/이름 검색,
    // same page (no reload) when the home page's own containers are here.
    document.addEventListener("click", function (e) {
      var link = e.target.closest("a.js-reset-flow");
      if (!link) return;
      var searchInput = document.getElementById("search-input");
      var hero = document.getElementById("search-hero");
      var results = document.getElementById("results");
      if (!searchInput || !results) return; // not on the home page -- let it navigate there normally
      e.preventDefault();
      clearPanel("student-panel");
      clearPanel("review-panel");
      clearPanel("confirm-panel");
      searchInput.value = "";
      results.innerHTML = '<p class="empty">학번 또는 이름을 입력해서 학생을 검색하세요.</p>';
      if (hero) hero.classList.remove("compact");
      history.replaceState(null, "", "/");
      window.scrollTo({ top: 0, behavior: "smooth" });
      searchInput.focus();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    hydrateAll(document);
    initSpaNav();
  });

  return { hydrateAll: hydrateAll };
})();
