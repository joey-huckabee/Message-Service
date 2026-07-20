/* Admin subscriptions console — hand-authored, no external dependencies (L2-DASH-023).
   Reads the embedded vocabulary (#vocab-data) for the Type->target dropdowns,
   fetches the recipient list and a recipient's subscriptions from the admin APIs,
   and drives create/delete through /admin/users/{id}/subscriptions, echoing the
   msp_csrf cookie as X-CSRF-Token. A 401 redirects to /login. */
(function () {
  "use strict";

  var USERS_URL = "/admin/users";
  var LOGIN_PATH = "/login";
  var CSRF_COOKIE = "msp_csrf";

  var vocabEl = document.getElementById("vocab-data");
  var VOCAB = vocabEl ? JSON.parse(vocabEl.textContent) : { pipelines: [], tags: [] };

  var recipientSel = document.getElementById("recipient");
  var granSel = document.getElementById("gran");
  var targetWrap = document.getElementById("target-wrap");
  var targetSel = document.getElementById("target");
  var targetLbl = document.getElementById("target-lbl");
  var rowsEl = document.getElementById("rows");
  var subsTitle = document.getElementById("subs-title");
  var addBtn = document.getElementById("add-btn");
  var msgEl = document.getElementById("msg");
  var recipients = [];

  function esc(s) {
    return String(s).replace(/[&<>]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
    });
  }
  function getCookie(name) {
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].split("=");
      if (kv[0] === name) return decodeURIComponent(kv.slice(1).join("="));
    }
    return "";
  }
  function showMsg(text, kind) {
    msgEl.textContent = text;
    msgEl.className = "msg " + (kind === "ok" ? "ok" : "err");
  }
  function clearMsg() {
    msgEl.className = "msg hidden";
  }

  function api(method, path, body) {
    var opts = { method: method, headers: { "Accept": "application/json" } };
    if (method !== "GET") {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = getCookie(CSRF_COOKIE);
      if (body !== undefined) opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (resp) {
      if (resp.status === 401) {
        window.location.assign(LOGIN_PATH);
        throw new Error("unauthenticated");
      }
      return resp;
    });
  }

  function selectedRecipientId() {
    return recipientSel.value;
  }
  function subsBase() {
    return USERS_URL + "/" + encodeURIComponent(selectedRecipientId()) + "/subscriptions";
  }

  function syncTarget() {
    var g = granSel.value;
    if (g === "GLOBAL") {
      targetWrap.classList.add("hidden");
      return;
    }
    targetWrap.classList.remove("hidden");
    targetLbl.textContent = g === "PIPELINE" ? "Pipeline" : "Tag";
    var opts = g === "PIPELINE" ? VOCAB.pipelines : VOCAB.tags;
    targetSel.innerHTML = opts
      .map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + "</option>"; })
      .join("");
  }

  function loadRecipients() {
    api("GET", USERS_URL)
      .then(function (resp) {
        if (!resp.ok) throw new Error("failed to load recipients");
        return resp.json();
      })
      .then(function (users) {
        recipients = users;
        recipientSel.innerHTML = users
          .map(function (u) { return '<option value="' + u.user_id + '">' + esc(u.email) + "</option>"; })
          .join("");
        if (users.length) loadSubs();
      })
      .catch(function (err) {
        if (err.message !== "unauthenticated") showMsg(err.message, "err");
      });
  }

  function loadSubs() {
    if (!selectedRecipientId()) return;
    api("GET", subsBase())
      .then(function (resp) {
        if (!resp.ok) throw new Error("failed to load subscriptions");
        return resp.json();
      })
      .then(renderRows)
      .catch(function (err) {
        if (err.message !== "unauthenticated") showMsg(err.message, "err");
      });
  }

  function renderRows(subs) {
    var email = "";
    for (var i = 0; i < recipients.length; i++) {
      if (String(recipients[i].user_id) === String(selectedRecipientId())) email = recipients[i].email;
    }
    subsTitle.textContent = "Subscriptions · " + email;
    if (!subs.length) {
      rowsEl.innerHTML = '<tr><td colspan="3" class="empty">This recipient has no subscriptions.</td></tr>';
      return;
    }
    rowsEl.innerHTML = subs.map(function (s) {
      var label = s.granularity === "GLOBAL" ? "Global" : (s.granularity === "PIPELINE" ? "Pipeline" : "Tag");
      var target = s.target_value
        ? '<span class="target">' + esc(s.target_value) + "</span>"
        : '<span class="muted">— every run —</span>';
      return "<tr>" +
        '<td><span class="pill ' + s.granularity + '">' + label + "</span></td>" +
        "<td>" + target + "</td>" +
        '<td class="row-actions"><button class="btn ghost small" data-rm="' + s.subscription_id +
        '">Remove</button></td>' +
        "</tr>";
    }).join("");
    Array.prototype.forEach.call(rowsEl.querySelectorAll("[data-rm]"), function (b) {
      b.onclick = function () { removeSub(b.getAttribute("data-rm")); };
    });
  }

  function addSub() {
    clearMsg();
    var g = granSel.value;
    var target = g === "GLOBAL" ? null : targetSel.value;
    addBtn.disabled = true;
    api("POST", subsBase(), { granularity: g, target_value: target })
      .then(function (resp) {
        addBtn.disabled = false;
        if (resp.ok) { showMsg("Subscription added.", "ok"); loadSubs(); return; }
        return resp.json().then(function (d) {
          showMsg(d && d.detail ? d.detail : "Could not add subscription (" + resp.status + ").", "err");
        });
      })
      .catch(function (err) {
        addBtn.disabled = false;
        if (err.message !== "unauthenticated") showMsg(err.message, "err");
      });
  }

  function removeSub(subId) {
    clearMsg();
    api("DELETE", subsBase() + "/" + encodeURIComponent(subId))
      .then(function (resp) {
        if (resp.ok) { showMsg("Subscription removed.", "ok"); loadSubs(); return; }
        showMsg("Could not remove subscription (" + resp.status + ").", "err");
      })
      .catch(function (err) {
        if (err.message !== "unauthenticated") showMsg(err.message, "err");
      });
  }

  recipientSel.onchange = function () { clearMsg(); loadSubs(); };
  granSel.onchange = syncTarget;
  addBtn.onclick = addSub;
  document.getElementById("signout").onclick = function () {
    api("POST", "/logout").then(function () { window.location.assign(LOGIN_PATH); })
      .catch(function () { window.location.assign(LOGIN_PATH); });
  };

  syncTarget();
  loadRecipients();
})();
