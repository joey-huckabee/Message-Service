/* Login page — hand-authored, no external dependencies (L2-DASH-019).
   Posts the entered credentials to the existing JSON POST /login endpoint and,
   on success, redirects to the admin console. The endpoint mints the session +
   CSRF cookies, so no CSRF header is needed for the login request itself. */
(function () {
  "use strict";

  var form = document.getElementById("login-form");
  var errEl = document.getElementById("err");
  var submitBtn = document.getElementById("submit");
  var CONSOLE_PATH = "/admin/console";

  function showError() {
    errEl.classList.remove("hidden");
  }
  function clearError() {
    errEl.classList.add("hidden");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearError();
    var email = document.getElementById("email").value;
    var password = document.getElementById("pw").value;
    submitBtn.disabled = true;

    fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ email: email, password: password })
    })
      .then(function (resp) {
        if (resp.ok) {
          window.location.assign(CONSOLE_PATH);
          return;
        }
        submitBtn.disabled = false;
        showError();
      })
      .catch(function () {
        submitBtn.disabled = false;
        showError();
      });
  });
})();
