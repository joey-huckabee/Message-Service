/* Admin notification console — hand-authored, no external dependencies (L2-DASH-020).
   Renders the recipient roster from GET /admin/users and drives create / update /
   reset-password through the existing admin account routes, echoing the msp_csrf
   cookie as the X-CSRF-Token header. A 401 from any call redirects to /login. */
(function () {
  "use strict";

  var USERS_URL = "/admin/users";
  var LOGIN_PATH = "/login";
  var CSRF_COOKIE = "msp_csrf";

  var rowsEl = document.getElementById("rows");
  var panelEl = document.getElementById("panel");
  var msgEl = document.getElementById("msg");
  var searchEl = document.getElementById("search");
  var allUsers = [];

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
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

  // Every request carries credentials; state-changing methods carry the CSRF
  // header (double-submit). A 401 means the session lapsed → back to login.
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

  function loadRoster() {
    api("GET", USERS_URL)
      .then(function (resp) {
        if (!resp.ok) throw new Error("failed to load recipients");
        return resp.json();
      })
      .then(function (users) {
        allUsers = users;
        renderRows();
      })
      .catch(function (err) {
        if (err.message !== "unauthenticated") showMsg(err.message, "err");
      });
  }

  function renderRows() {
    var q = (searchEl.value || "").toLowerCase();
    var list = allUsers.filter(function (u) {
      return !q || u.email.toLowerCase().indexOf(q) !== -1 ||
        (u.display_name || "").toLowerCase().indexOf(q) !== -1;
    });
    if (!list.length) {
      rowsEl.innerHTML = '<tr><td colspan="6" class="empty">No recipients match.</td></tr>';
      return;
    }
    rowsEl.innerHTML = list.map(function (u) {
      var em = esc(u.email), at = em.indexOf("@");
      var email = '<span class="email">' + (at > -1 ? em.slice(0, at) + '<span class="mono">' + em.slice(at) + "</span>" : em) + "</span>";
      var role = u.is_admin ? '<span class="pill admin">Admin</span>' : '<span class="pill user">User</span>';
      var status = u.disabled ? '<span class="pill disabled">Disabled</span>' : '<span class="pill active">Active</span>';
      return "<tr>" +
        "<td>" + email + "</td>" +
        "<td>" + esc(u.display_name) + "</td>" +
        "<td>" + role + "</td>" +
        "<td>" + status + "</td>" +
        '<td class="muted">' + esc((u.created_at || "").slice(0, 10)) + "</td>" +
        '<td><div class="row-actions">' +
        '<button class="btn ghost small" data-edit="' + u.user_id + '">Edit</button>' +
        '<button class="btn ghost small" data-reset="' + u.user_id + '">Reset password</button>' +
        "</div></td></tr>";
    }).join("");
    Array.prototype.forEach.call(rowsEl.querySelectorAll("[data-edit]"), function (b) {
      b.onclick = function () { openEdit(byId(b.getAttribute("data-edit"))); };
    });
    Array.prototype.forEach.call(rowsEl.querySelectorAll("[data-reset]"), function (b) {
      b.onclick = function () { openReset(byId(b.getAttribute("data-reset"))); };
    });
  }

  function byId(id) {
    id = Number(id);
    for (var i = 0; i < allUsers.length; i++) if (allUsers[i].user_id === id) return allUsers[i];
    return null;
  }
  function closePanel() { panelEl.className = "panel hidden"; panelEl.innerHTML = ""; }
  function field(label, id, type, value, disabled) {
    return '<div class="field"><label>' + label + "</label>" +
      '<input id="' + id + '" type="' + type + '" value="' + esc(value || "") + '"' +
      (disabled ? " disabled" : "") + "></div>";
  }
  function checks(admin, disabled) {
    return '<div class="checks">' +
      '<label class="check"><input type="checkbox" id="f-admin"' + (admin ? " checked" : "") + "> Administrator</label>" +
      '<label class="check"><input type="checkbox" id="f-disabled"' + (disabled ? " checked" : "") + "> Disabled</label></div>";
  }
  function actions(primaryLabel) {
    return '<div class="panel-actions"><button class="btn ghost" id="p-cancel">Cancel</button>' +
      '<button class="btn primary" id="p-save">' + primaryLabel + "</button></div>";
  }
  function wirePanel(onSave) {
    document.getElementById("p-cancel").onclick = closePanel;
    document.getElementById("p-save").onclick = onSave;
    panelEl.className = "panel";
  }

  function openCreate() {
    clearMsg();
    panelEl.innerHTML = "<h2>New recipient</h2><div class=\"grid\">" +
      field("Email", "f-email", "email", "") + field("Display name", "f-name", "text", "") +
      field("Password", "f-pw", "password", "") + field("Confirm password", "f-pw2", "password", "") +
      "</div>" + checks(false, false) + actions("Create recipient");
    wirePanel(function () {
      var pw = val("f-pw");
      if (pw !== val("f-pw2")) { showMsg("Passwords do not match.", "err"); return; }
      submit("POST", USERS_URL, {
        email: val("f-email"), display_name: val("f-name"), password: pw,
        is_admin: checked("f-admin"), disabled: checked("f-disabled")
      }, "Recipient created.");
    });
  }
  function openEdit(u) {
    if (!u) return;
    clearMsg();
    panelEl.innerHTML = "<h2>Edit recipient</h2><div class=\"grid\">" +
      field("Email", "f-email", "email", u.email, true) + field("Display name", "f-name", "text", u.display_name) +
      "</div>" + checks(u.is_admin, u.disabled) + actions("Save changes");
    wirePanel(function () {
      submit("PATCH", USERS_URL + "/" + u.user_id, {
        display_name: val("f-name"), is_admin: checked("f-admin"), disabled: checked("f-disabled")
      }, "Recipient updated.");
    });
  }
  function openReset(u) {
    if (!u) return;
    clearMsg();
    panelEl.innerHTML = "<h2>Reset password · " + esc(u.email) + "</h2><div class=\"grid\">" +
      field("New password", "f-pw", "password", "") + field("Confirm password", "f-pw2", "password", "") +
      "</div>" + actions("Set password");
    wirePanel(function () {
      var pw = val("f-pw");
      if (pw !== val("f-pw2")) { showMsg("Passwords do not match.", "err"); return; }
      submit("POST", USERS_URL + "/" + u.user_id + "/password", { password: pw }, "Password reset.");
    });
  }

  function val(id) { return document.getElementById(id).value; }
  function checked(id) { return document.getElementById(id).checked; }

  function submit(method, path, body, okMsg) {
    document.getElementById("p-save").disabled = true;
    api(method, path, body)
      .then(function (resp) {
        if (resp.ok) { closePanel(); showMsg(okMsg, "ok"); loadRoster(); return; }
        return resp.json().then(function (d) {
          document.getElementById("p-save").disabled = false;
          showMsg((d && d.detail) ? d.detail : "Request failed (" + resp.status + ").", "err");
        });
      })
      .catch(function (err) {
        if (err.message !== "unauthenticated") {
          var btn = document.getElementById("p-save");
          if (btn) btn.disabled = false;
          showMsg(err.message, "err");
        }
      });
  }

  document.getElementById("new-btn").onclick = openCreate;
  searchEl.oninput = renderRows;
  document.getElementById("signout").onclick = function () {
    api("POST", "/logout").then(function () { window.location.assign(LOGIN_PATH); })
      .catch(function () { window.location.assign(LOGIN_PATH); });
  };
  loadRoster();
})();
