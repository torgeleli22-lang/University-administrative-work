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
    root.querySelectorAll(".class-date-select").forEach(function (sel) {
      if (sel.dataset.hydrated) return;
      sel.dataset.hydrated = "1";
      var cid = sel.dataset.course;
      var startSel = root.querySelector("#class_period_start_" + cid);
      var endSel = root.querySelector("#class_period_end_" + cid);
      if (!startSel || !endSel) return;
      function sync() {
        var opt = sel.options[sel.selectedIndex];
        startSel.value = opt.dataset.start;
        endSel.value = opt.dataset.end;
      }
      sel.addEventListener("change", sync);
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

  // Delete buttons stay disabled until an admin token is typed in, so
  // deleting always proceeds in the order "enter token, then delete" --
  // .js-admin-token is the token input, .js-admin-gated the buttons it
  // gates (row/bulk/per-student delete), both scoped to the same form.
  function applyAdminGate(scopeEl) {
    var doc = scopeEl.ownerDocument || document;
    var tokenInput = doc.querySelector(".js-admin-token");
    if (!tokenInput) return;
    var hasToken = tokenInput.value.trim().length > 0;
    scopeEl.querySelectorAll(".js-admin-gated").forEach(function (btn) {
      btn.disabled = !hasToken;
    });
  }

  function hydrateAdminGate(root) {
    root.querySelectorAll(".js-admin-token").forEach(function (input) {
      if (input.dataset.hydrated) return;
      input.dataset.hydrated = "1";
      input.addEventListener("input", function () { applyAdminGate(document); });
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

  function hydrateAll(root) {
    hydratePeriodSync(root);
    hydrateDateSync(root);
    hydrateSubmitGuard(root);
    hydrateAdminGate(root);
    hydrateSelectAll(root);
  }

  function showPanel(el) {
    el.hidden = false;
  }

  function setLoading(el) {
    el.hidden = false;
    el.innerHTML = '<p class="loading">불러오는 중...</p>';
  }

  function initSpaNav() {
    document.addEventListener("click", function (e) {
      var link = e.target.closest("a.js-open-student");
      if (!link) return;
      var panel = document.getElementById("student-panel");
      if (!panel) return; // no SPA container on this page -- let it navigate normally
      e.preventDefault();
      var reviewPanel = document.getElementById("review-panel");
      if (reviewPanel) {
        reviewPanel.hidden = true;
        reviewPanel.innerHTML = "";
      }
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

    document.addEventListener("submit", function (e) {
      var form = e.target.closest("form.js-review-form");
      if (!form) return;
      var reviewPanel = document.getElementById("review-panel");
      if (!reviewPanel) return; // fallback: normal submit -> full review page
      e.preventDefault();
      setLoading(reviewPanel);
      reviewPanel.scrollIntoView({ behavior: "smooth", block: "start" });
      fetch(form.action, {
        method: "POST",
        headers: { "X-Partial": "1" },
        body: new FormData(form),
      })
        .then(function (res) { return res.text(); })
        .then(function (html) {
          reviewPanel.innerHTML = html;
          showPanel(reviewPanel);
          hydrateAll(reviewPanel);
        });
    });

    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-close-panel]");
      if (!btn) return;
      var target = document.getElementById(btn.dataset.closePanel);
      if (target) {
        target.hidden = true;
        target.innerHTML = "";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    hydrateAll(document);
    initSpaNav();
  });

  return { hydrateAll: hydrateAll };
})();
