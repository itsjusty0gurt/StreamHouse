(() => {
  "use strict";

  const localToken = new URLSearchParams(location.search).get("token") || "";
  const relayBase = String(
    window.STREAMHOUSE_RELAY_BASE || window.SALLY_RELAY_BASE || ""
  ).replace(/\/$/, "");
  const state = {
    authToken: "",
    channelId: "",
    config: {pages: []},
    activePageId: "",
  };
  const grid = document.querySelector("#grid");
  const pages = document.querySelector("#pages");
  const toast = document.querySelector("#toast");

  function dimensions(count) {
    if (count <= 1) return [1, 1];
    if (count === 2) return [2, 1];
    if (count <= 4) return [2, 2];
    if (count <= 6) return [3, 2];
    return [3, 3];
  }

  function notify(message, kind = "error") {
    toast.textContent = message;
    toast.classList.toggle("success", kind === "success");
    toast.classList.add("show");
    setTimeout(() => {
      toast.classList.remove("show");
      toast.classList.remove("success");
    }, 1800);
  }

  function requestHeaders() {
    const headers = {"Content-Type": "application/json"};
    if (state.authToken) headers.Authorization = `Bearer ${state.authToken}`;
    return headers;
  }

  function endpoint(path) {
    return `${relayBase}${path}`;
  }

  async function trigger(button, element) {
    element.disabled = true;
    try {
      const response = await fetch(endpoint("/api/trigger"), {
        method: "POST",
        headers: requestHeaders(),
        body: JSON.stringify({
          token: localToken,
          button_id: button.id,
          channel_id: state.channelId,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Sound was rejected.");
      notify("Sent to Streamhouse Hub", "success");
    } catch (error) {
      notify(error.message);
    } finally {
      setTimeout(() => { element.disabled = false; }, 300);
    }
  }

  function render() {
    const page = state.config.pages.find(item => item.id === state.activePageId)
      || state.config.pages[0]
      || {buttons: []};
    state.activePageId = page.id || "";
    const count = page.buttons.length;
    const [columns, rows] = dimensions(count);
    grid.innerHTML = "";
    grid.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
    grid.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
    if (!count) {
      grid.innerHTML = '<div id="empty">No sounds are configured on this page.</div>';
    } else {
      for (const button of page.buttons) {
        const element = document.createElement("button");
        element.className = "sound";
        element.textContent = button.label;
        element.addEventListener("click", () => trigger(button, element));
        grid.appendChild(element);
      }
    }
    pages.innerHTML = "";
    pages.classList.toggle("visible", state.config.pages.length > 1);
    for (const item of state.config.pages) {
      const element = document.createElement("button");
      element.className = "page" + (item.id === state.activePageId ? " active" : "");
      element.textContent = item.name;
      element.addEventListener("click", () => {
        state.activePageId = item.id;
        render();
      });
      pages.appendChild(element);
    }
  }

  async function loadConfig() {
    const query = localToken ? `?token=${encodeURIComponent(localToken)}` : "";
    try {
      const response = await fetch(endpoint(`/api/config${query}`), {
        headers: requestHeaders(),
      });
      if (!response.ok) throw new Error("Soundboard access was denied.");
      state.config = await response.json();
      render();
    } catch (error) {
      grid.innerHTML = `<div id="empty">${error.message}</div>`;
    }
  }

  if (window.Twitch && window.Twitch.ext) {
    window.Twitch.ext.onAuthorized(auth => {
      state.authToken = auth.token;
      state.channelId = auth.channelId;
      loadConfig();
    });
  } else {
    loadConfig();
  }
})();
