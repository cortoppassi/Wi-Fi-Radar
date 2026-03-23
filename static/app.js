const bodyEl = document.getElementById("devicesBody");
const scanButton = document.getElementById("scanButton");
const searchInput = document.getElementById("searchInput");
const onlineOnly = document.getElementById("onlineOnly");
const toast = document.getElementById("toast");

const totalCount = document.getElementById("totalCount");
const onlineCount = document.getElementById("onlineCount");
const blockedCount = document.getElementById("blockedCount");
const trustedCount = document.getElementById("trustedCount");

let devices = [];
let saveTimeout = null;

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
      const host = escapeHtml(device.hostname || "Hostname não resolvido");
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
              ${device.trusted ? '<span class="pill pill-trusted">Confiável</span>' : ""}
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
              placeholder="Anotação"
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
      `<h3>Varredura concluída</h3>
       <p>${data.scan.online_devices_found} dispositivo(s) online em ${data.scan.subnet}.</p>`
    );
  } finally {
    scanButton.disabled = false;
    scanButton.textContent = "Nova varredura";
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
    const steps = data.router_help.steps.map((s) => `<p>• ${s}</p>`).join("");
    showToast(
      `<h3>${data.router_help.title}</h3>
       ${steps}
       <p><a href="${data.router_help.router_url}" target="_blank" rel="noreferrer">Abrir roteador agora</a></p>`,
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
searchInput.addEventListener("input", renderTable);
onlineOnly.addEventListener("change", renderTable);

refreshDevices()
  .then(() => runScan(false))
  .catch(() => runScan(true));

window.setInterval(() => {
  refreshDevices().catch(() => undefined);
}, 25000);
