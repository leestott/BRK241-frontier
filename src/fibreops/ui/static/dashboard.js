(function () {
  let selectedRunId = null;
  let activeOperation = null;

  const operationButtons = () => document.querySelectorAll("[data-operation]");
  const runCards = () => Array.from(document.querySelectorAll("#runs-panel [data-run-id]"));

  function setActivity(message, kind) {
    const status = document.getElementById("activity-status");
    status.textContent = message;
    status.dataset.kind = kind;
  }

  function markSelected() {
    for (const card of runCards()) {
      const selected = card.dataset.runId === selectedRunId;
      card.setAttribute("aria-pressed", String(selected));
      card.classList.toggle("card--selected", selected);
    }
    const chosen = runCards().find((card) => card.dataset.runId === selectedRunId);
    document.getElementById("detail-meta").textContent = chosen ?
      `${chosen.querySelector(".badge").textContent.trim()} incident selected` :
      "select an incident";
  }

  function selectRun(preferCritical) {
    const cards = runCards();
    const previous = selectedRunId;
    let card = !preferCritical && cards.find((item) => item.dataset.runId === previous);
    card ||= cards.find((item) => item.classList.contains("sev-critical")) || cards[0];
    selectedRunId = card?.dataset.runId || null;
    markSelected();

    const detail = document.getElementById("detail-panel");
    if (!card) {
      detail.innerHTML = '<p class="empty-state">[ SELECT INCIDENT · AGENT TIMELINE STANDBY ]</p>';
    } else if (previous !== selectedRunId || !detail.dataset.loadedRunId) {
      htmx.ajax("GET", card.getAttribute("hx-get"), { target: detail, swap: "innerHTML" });
    }
  }

  function updateElapsed() {
    document.querySelectorAll(".incident-elapsed[datetime]").forEach((element) => {
      const started = Date.parse(element.getAttribute("datetime"));
      if (!Number.isFinite(started)) return;
      const minutes = Math.max(0, Math.floor((Date.now() - started) / 60000));
      element.textContent = minutes < 1 ? "Just now" :
        minutes < 60 ? `${minutes}m ago` :
        minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` :
        `${Math.floor(minutes / 1440)}d ago`;
    });
  }

  function finishOperation(element, successful) {
    if (element !== activeOperation) return;
    setActivity(
      successful ? element.dataset.operationDone : `${element.dataset.operation} failed. Try again.`,
      successful ? "ok" : "error"
    );
    activeOperation = null;
    operationButtons().forEach((button) => { button.disabled = false; });
  }

  function showVoiceError(status) {
    const panel = document.getElementById("voice-panel");
    const message = document.createElement("p");
    message.className = "panel-error";
    message.setAttribute("role", "alert");
    message.textContent = status ?
      `Voice updates are unavailable (HTTP ${status}). Check the app logs or retry.` :
      "Voice updates could not be reached. Check your connection or retry.";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn-secondary";
    retry.textContent = "Retry voice updates";
    retry.setAttribute("hx-get", "/partials/voice");
    retry.setAttribute("hx-target", "#voice-panel");
    retry.setAttribute("hx-swap", "innerHTML");
    panel.replaceChildren(message, retry);
    htmx.process(retry);
  }

  document.addEventListener("click", (event) => {
    const card = event.target.closest("#runs-panel [data-run-id]");
    if (!card) return;
    selectedRunId = card.dataset.runId;
    markSelected();
  });

  document.addEventListener("htmx:beforeRequest", (event) => {
    const button = event.detail.elt;
    if (!button?.matches?.("[data-operation]")) return;
    if (activeOperation) {
      event.preventDefault();
      return;
    }
    activeOperation = button;
    operationButtons().forEach((control) => { control.disabled = true; });
    setActivity(`${button.dataset.operation}…`, "pending");
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    finishOperation(event.detail.elt, event.detail.successful);
  });

  document.addEventListener("htmx:responseError", (event) => {
    if (event.detail.target?.id === "voice-panel") showVoiceError(event.detail.xhr?.status);
  });

  document.addEventListener("htmx:sendError", (event) => {
    finishOperation(event.detail.elt, false);
    if (event.detail.target?.id === "voice-panel") showVoiceError(null);
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    if (target?.id === "runs-panel") {
      const injected = event.detail.requestConfig?.verb === "post";
      selectRun(injected);
      updateElapsed();
    } else if (target?.id === "detail-panel") {
      target.dataset.loadedRunId = selectedRunId || "";
    }
  });

  setInterval(updateElapsed, 30000);
})();
