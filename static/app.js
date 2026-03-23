const bodyEl = document.getElementById("devicesBody");
const scanButton = document.getElementById("scanButton");
const routerButton = document.getElementById("routerButton");
const searchInput = document.getElementById("searchInput");
const onlineOnly = document.getElementById("onlineOnly");
const toast = document.getElementById("toast");

const totalCount = document.getElementById("totalCount");
const onlineCount = document.getElementById("onlineCount");
const blockedCount = document.getElementById("blockedCount");
const trustedCount = document.getElementById("trustedCount");

const routerUrlInput = document.getElementById("routerUrlInput");
const routerUserInput = document.getElementById("routerUserInput");
const routerPasswordInput = document.getElementById("routerPasswordInput");
const saveRouterSettingsButton = document.getElementById("saveRouterSettingsButton");
const copyRouterUserButton = document.getElementById("copyRouterUserButton");
const copyRouterPasswordButton = document.getElementById("copyRouterPasswordButton");
const routerHint = document.getElementById("routerHint");

let devices = [];
let saveTimeout = null;
let routerSettings = {
  router_url: "http://192.168.1.254",
  username: "",
  password: "",
  open_url: "http://192.168.1.254",
  uses_embedded_auth_url: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function showToast(html, ms = 7000) {
  toast.innerHTML = html;
  toast.classList.add("visible");
  window.clearTimeout(saveTimeout);
  saveTimeout = window.setTimeout(() => toast.classList.remove("visible"), ms);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    let message = `Erro ${response.status}`;
    try {
      const payload = await response.json();
      if (payload.error) message = payload.error;
    } catch (_) {
      // Ignore JSON parse errors for non-JSON responses.
    }
    throw new Error(message);
  }

  return response.json();
}

function formatDate(iso) {
  if (!iso) return "-";
  const date = new Date(iso);
  return `${date.toLocaleDateString("pt-BR")} ${date.toLocaleTimeString("pt-BR")}`;
}

function deviceLabel(device) {
  if (device.nickname) return device.nickname;
  if (device.hostname) return device.hostname;
  return "Sem nome";
}

function applyFilters(list) {
  const term = searchInput.value.trim().toLowerCase();
  return list.filter((d) => {
    if (onlineOnly.checked && !d.online) return false;
    if (!term) return true;
    return [d.nickname, d.hostname, d.ip, d.mac, d.note].join(" ").toLowerCase().includes(term);
  });
}

function renderStats() {
  totalCount.textContent = String(devices.length);
  onlineCount.textContent = String(devices.filter((d) => d.online).length);
  blockedCount.textContent = String(devices.filter((d) => d.blocked).length);
  trustedCount.textContent = String(devices.filter((d) => d.trusted).length);
}

function getRouterTargetUrl() {
  return (routerSettings.open_url || routerSettings.router_url || "").trim();
}

function renderRouterHint() {
  if (routerSettings.username && routerSettings.password) {
    routerHint.textContent =
      "Ao abrir o roteador, o app tenta login automatico (POST de formulario).";
    return;
  }
  routerHint.textContent = "Preencha usuario e senha para tentar abertura com login automatico quando suportado.";
}

function fillRouterSettingsForm(settings) {
  routerUrlInput.value = settings.router_url || "";
  routerUserInput.value = settings.username || "";
  routerPasswordInput.value = settings.password || "";
  renderRouterHint();
}

function renderTable() {
  renderStats();
  const filtered = applyFilters(devices);
  if (filtered.length === 0) {
    bodyEl.innerHTML =
      '<tr><td colspan="8">Nenhum dispositivo encontrado com os filtros atuais.</td></tr>';
    return;
  }

  bodyEl.innerHTML = filtered
    .map((device) => {
      const safeMac = device.mac;
      const label = escapeHtml(deviceLabel(device));
      const host = escapeHtml(device.hostname || "Hostname nao resolvido");
      const note = escapeHtml(device.note || "");
      const nickname = escapeHtml(device.nickname || "");
      const ip = escapeHtml(device.ip || "-");
      const mac = escapeHtml(device.mac);
      return `
        <tr>
          <td>
            <span class="status-dot ${device.online ? "status-online" : "status-offline"}"></span>
          </td>
          <td>
            <div class="device-main">
              <strong>${label}</strong>
              <small>${host}</small>
              ${device.blocked ? '<span class="pill pill-blocked">Bloqueado</span>' : ""}
              ${device.trusted ? '<span class="pill pill-trusted">Confiavel</span>' : ""}
            </div>
          </td>
          <td class="mono">${ip}</td>
          <td class="mono">${mac}</td>
          <td>
            <input data-field="trusted" data-mac="${safeMac}" type="checkbox" ${device.trusted ? "checked" : ""} />
          </td>
          <td>
            <input data-field="blocked" data-mac="${safeMac}" type="checkbox" ${device.blocked ? "checked" : ""} />
          </td>
          <td>
            <input
              class="input-inline"
              data-field="note"
              data-mac="${safeMac}"
              type="text"
              placeholder="Anotacao"
              value="${note}"
            />
          </td>
          <td>${formatDate(device.last_seen)}</td>
        </tr>
        <tr>
          <td></td>
          <td colspan="7">
            <input
              class="input-inline"
              data-field="nickname"
              data-mac="${safeMac}"
              type="text"
              placeholder="Definir apelido (ex: TV Sala)"
              value="${nickname}"
            />
          </td>
        </tr>
      `;
    })
    .join("");
}

async function refreshDevices() {
  const data = await fetchJson("/api/devices");
  devices = data.devices;
  renderTable();
}

async function loadRouterSettings() {
  const data = await fetchJson("/api/router-settings");
  routerSettings = data;
  fillRouterSettingsForm(data);
}

async function saveRouterSettings() {
  const payload = {
    router_url: routerUrlInput.value.trim(),
    username: routerUserInput.value.trim(),
    password: routerPasswordInput.value,
  };
  const data = await fetchJson("/api/router-settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  routerSettings = data;
  fillRouterSettingsForm(data);
  showToast("<h3>Acesso salvo</h3><p>Dados do roteador atualizados com sucesso.</p>");
}

async function runScan(aggressive = true) {
  scanButton.disabled = true;
  scanButton.textContent = "Varrendo...";
  try {
    const data = await fetchJson("/api/scan", {
      method: "POST",
      body: JSON.stringify({ aggressive }),
    });
    devices = data.devices;
    renderTable();
    showToast(
      `<h3>Varredura concluida</h3>
       <p>${data.scan.online_devices_found} dispositivo(s) online em ${data.scan.subnet}.</p>`
    );
  } finally {
    scanButton.disabled = false;
    scanButton.textContent = "Nova varredura";
  }
}

async function copyCredential(value, label) {
  if (!value) {
    showToast(`<h3>Nada para copiar</h3><p>Preencha o campo de ${label} primeiro.</p>`);
    return;
  }
  await navigator.clipboard.writeText(value);
  showToast(`<h3>${label} copiado</h3><p>Valor enviado para a area de transferencia.</p>`);
}

function submitRouterLoginPost(loginPayload, targetWindowName) {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = loginPayload.login_url;
  form.target = targetWindowName;
  form.style.display = "none";

  Object.entries(loginPayload.fields || {}).forEach(([key, value]) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = key;
    input.value = String(value ?? "");
    form.appendChild(input);
  });

  document.body.appendChild(form);
  form.submit();
  form.remove();
}

async function openRouter() {
  if (!routerSettings.router_url) {
    await loadRouterSettings();
  }

  const fallbackUrl = getRouterTargetUrl();
  if (!fallbackUrl) {
    showToast("<h3>URL nao configurada</h3><p>Informe a URL do roteador e salve.</p>");
    return;
  }

  const targetWindowName = "routerAutoLoginWindow";
  const popup = window.open("about:blank", targetWindowName);
  if (!popup) {
    showToast("<h3>Popup bloqueado</h3><p>Permita popups para abrir e logar no roteador.</p>");
    return;
  }

  try {
    const loginPayload = await fetchJson("/api/router-auto-login", { method: "POST" });
    if (loginPayload.mode === "form_post" && loginPayload.login_url && loginPayload.fields) {
      submitRouterLoginPost(loginPayload, targetWindowName);
      showToast("<h3>Abrindo roteador</h3><p>Tentando login automatico agora.</p>");
      return;
    }

    popup.location.href = loginPayload.open_url || fallbackUrl;
    if (routerSettings.username && routerSettings.password) {
      showToast(
        "<h3>Abrindo roteador</h3><p>Este modelo pode exigir formulario proprio; a aba foi aberta com fallback.</p>",
        11000
      );
    }
  } catch (error) {
    popup.location.href = fallbackUrl;
    showToast(
      `<h3>Abrindo roteador</h3><p>Nao foi possivel preparar login automatico (${escapeHtml(error.message)}). Abrindo em modo normal.</p>`,
      12000
    );
  }
}

async function updateDevice(mac, field, value) {
  const payload = { [field]: value };
  const data = await fetchJson(`/api/devices/${encodeURIComponent(mac)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

  const index = devices.findIndex((d) => d.mac === mac);
  if (index >= 0) devices[index] = data.device;
  renderTable();

  if (data.router_help) {
    const steps = data.router_help.steps.map((s) => `<p>- ${escapeHtml(s)}</p>`).join("");
    const openUrl = escapeHtml(data.router_help.open_url || data.router_help.router_url);
    showToast(
      `<h3>${escapeHtml(data.router_help.title)}</h3>
       ${steps}
       <p><a href="${openUrl}" target="_blank" rel="noreferrer">Abrir roteador agora</a></p>`,
      12000
    );
  }
}

bodyEl.addEventListener("change", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  const mac = target.dataset.mac;
  const field = target.dataset.field;
  if (!mac || !field) return;

  try {
    if (field === "trusted" || field === "blocked") {
      await updateDevice(mac, field, target.checked);
    } else {
      await updateDevice(mac, field, target.value);
    }
  } catch (error) {
    showToast(`<h3>Falha ao atualizar</h3><p>${error.message}</p>`);
  }
});

let debounceTimer = null;
bodyEl.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  if (!target.dataset.mac || !target.dataset.field) return;
  if (target.dataset.field === "trusted" || target.dataset.field === "blocked") return;

  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    try {
      await updateDevice(target.dataset.mac, target.dataset.field, target.value);
    } catch (error) {
      showToast(`<h3>Falha ao atualizar</h3><p>${error.message}</p>`);
    }
  }, 500);
});

scanButton.addEventListener("click", () => runScan(true));
routerButton.addEventListener("click", () => {
  openRouter().catch((error) => showToast(`<h3>Falha ao abrir</h3><p>${error.message}</p>`));
});
saveRouterSettingsButton.addEventListener("click", () => {
  saveRouterSettings().catch((error) => showToast(`<h3>Falha ao salvar</h3><p>${error.message}</p>`));
});
copyRouterUserButton.addEventListener("click", () => {
  copyCredential(routerUserInput.value.trim(), "Usuario").catch((error) =>
    showToast(`<h3>Falha ao copiar</h3><p>${error.message}</p>`)
  );
});
copyRouterPasswordButton.addEventListener("click", () => {
  copyCredential(routerPasswordInput.value, "Senha").catch((error) =>
    showToast(`<h3>Falha ao copiar</h3><p>${error.message}</p>`)
  );
});

searchInput.addEventListener("input", renderTable);
onlineOnly.addEventListener("change", renderTable);

loadRouterSettings().catch(() => undefined);
refreshDevices()
  .then(() => runScan(false))
  .catch(() => runScan(true));

window.setInterval(() => {
  refreshDevices().catch(() => undefined);
}, 25000);
