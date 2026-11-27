// Operator actions on the ticket screen. All text is inserted with textContent, never innerHTML.
(function () {
  "use strict";
  var root = document.getElementById("ticket-root");
  if (!root) return;

  var ticketId = root.dataset.ticketId;
  var version = parseInt(root.dataset.version, 10);
  var attemptId = root.dataset.attemptId ? parseInt(root.dataset.attemptId, 10) : null;
  var original = root.dataset.originalDraft || "";
  var draft = document.getElementById("draft");
  var btnApprove = document.getElementById("btn-approve");
  var btnEdit = document.getElementById("btn-edit-approve");
  var btnManual = document.getElementById("btn-manual");
  var btnAnalyze = document.getElementById("btn-analyze");
  var notice = document.getElementById("notice");
  var busy = document.getElementById("busy");
  var busyText = document.getElementById("busy-text");
  var buttons = [btnApprove, btnEdit, btnManual, btnAnalyze].filter(Boolean);

  function showNotice(kind, title, message, offerReload) {
    notice.hidden = false;
    notice.className = "notice notice-" + kind;
    notice.textContent = "";
    var strong = document.createElement("strong");
    strong.textContent = title;
    notice.appendChild(strong);
    var p = document.createElement("div");
    p.textContent = message;
    notice.appendChild(p);
    if (offerReload) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.textContent = "Reload ticket";
      b.addEventListener("click", function () { location.reload(); });
      notice.appendChild(b);
    }
  }

  function setBusy(on, text) {
    buttons.forEach(function (b) { b.disabled = on; });
    if (draft) draft.readOnly = on;
    busy.hidden = !on;
    busyText.textContent = text || "Working…";
    if (!on) syncEditButtons();
  }

  function syncEditButtons() {
    if (!draft) return;
    var changed = draft.value.trim() !== original.trim();
    if (btnApprove) btnApprove.disabled = changed;
    if (btnEdit) btnEdit.disabled = btnApprove ? !changed : draft.value.trim() === "";
  }

  function post(path, body, busyLabel) {
    setBusy(true, busyLabel);
    notice.hidden = true;
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.ok) { location.reload(); return; }
        setBusy(false);
        var err = (data && data.error) || {};
        if (res.status === 409) {
          showNotice("conflict", "This ticket changed (" + (err.code || "409") + ")",
            err.message || "Reload the ticket to see the current state.", true);
        } else if (res.status === 422) {
          var msg = err.message || (data.detail && JSON.stringify(data.detail)) || "Invalid input.";
          showNotice("error", "Not saved", msg, false);
        } else {
          showNotice("error", "Request failed (HTTP " + res.status + ")", err.message || "Try again.", true);
        }
      });
    }).catch(function () {
      setBusy(false);
      showNotice("error", "Network error", "The server did not respond. The ticket is unchanged; try again.", true);
    });
  }

  if (draft) draft.addEventListener("input", syncEditButtons);
  syncEditButtons();

  // Another tab or request is analysing this ticket: refresh until it finishes or expires.
  if (root.dataset.status === "ANALYZING") {
    setTimeout(function () { location.reload(); }, 4000);
  }

  if (btnApprove) btnApprove.addEventListener("click", function () {
    post("/api/tickets/" + ticketId + "/decisions",
      { action: "approve", expected_version: version, attempt_id: attemptId }, "Saving decision…");
  });
  if (btnEdit) btnEdit.addEventListener("click", function () {
    post("/api/tickets/" + ticketId + "/decisions",
      { action: "edit_approve", expected_version: version, attempt_id: attemptId, final_text: draft.value },
      "Saving decision…");
  });
  if (btnManual) btnManual.addEventListener("click", function () {
    var noteEl = document.getElementById("manual-note");
    var note = noteEl ? noteEl.value : "";
    post("/api/tickets/" + ticketId + "/decisions",
      { action: "route_manual", expected_version: version, note: note }, "Routing to manual review…");
  });
  if (btnAnalyze) btnAnalyze.addEventListener("click", function () {
    post("/api/tickets/" + ticketId + "/analysis", { expected_version: version },
      "Analysing: searching the knowledge base and waiting for the model…");
  });
})();
