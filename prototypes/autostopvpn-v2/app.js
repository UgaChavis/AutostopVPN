const peers = [
  {
    ip: "10.8.1.50",
    endpoint: "188.235.248.27:39117",
    location: "188.235.248.27",
    handshake: "1м 56с",
    rx: "9.19 KiB/s",
    tx: "4.24 KiB/s",
    flow: "13.42 KiB/s",
    flowValue: 13.42,
    share: "20.39%",
    today: "602.74 MiB",
    total: "82.97 GiB",
    active: true,
    ping: "42 ms",
    loss: "0%",
    hour: "28.4 MiB",
    mtuOk: true,
  },
  {
    ip: "10.8.1.1",
    endpoint: "188.235.248.27:52142",
    location: "188.235.248.27",
    handshake: "13с",
    rx: "4.41 KiB/s",
    tx: "3.42 KiB/s",
    flow: "7.83 KiB/s",
    flowValue: 7.83,
    share: "11.89%",
    today: "548.23 MiB",
    total: "106.26 GiB",
    active: true,
    ping: "36 ms",
    loss: "0%",
    hour: "18.1 MiB",
    mtuOk: true,
  },
  {
    ip: "10.8.1.52",
    endpoint: "176.59.151.181:53817",
    location: "Novosibirsk, RU",
    handshake: "40с",
    rx: "2.29 KiB/s",
    tx: "4.04 KiB/s",
    flow: "6.33 KiB/s",
    flowValue: 6.33,
    share: "9.62%",
    today: "989.02 MiB",
    total: "77.86 GiB",
    active: true,
    ping: "58 ms",
    loss: "0%",
    hour: "12.7 MiB",
    mtuOk: true,
  },
  {
    ip: "10.8.1.35",
    endpoint: "84.42.96.40:63023",
    location: "Saint Petersburg, RU",
    handshake: "0с",
    rx: "3.47 KiB/s",
    tx: "1.98 KiB/s",
    flow: "5.45 KiB/s",
    flowValue: 5.45,
    share: "8.28%",
    today: "401.15 MiB",
    total: "26.81 GiB",
    active: true,
    ping: "51 ms",
    loss: "0%",
    hour: "9.8 MiB",
    mtuOk: true,
  },
  {
    ip: "10.8.1.26",
    endpoint: "185.124.230.236:44713",
    location: "185.124.230.236",
    handshake: "12с",
    rx: "5.12 KiB/s",
    tx: "287 B/s",
    flow: "5.41 KiB/s",
    flowValue: 5.41,
    share: "8.21%",
    today: "4.08 GiB",
    total: "238.90 GiB",
    active: true,
    ping: "44 ms",
    loss: "0%",
    hour: "10.3 MiB",
    mtuOk: false,
  },
  {
    ip: "10.8.1.21",
    endpoint: "176.59.138.125:64769",
    location: "Novosibirsk, RU",
    handshake: "31с",
    rx: "1.80 KiB/s",
    tx: "3.22 KiB/s",
    flow: "5.02 KiB/s",
    flowValue: 5.02,
    share: "7.63%",
    today: "2.01 GiB",
    total: "270.55 GiB",
    active: true,
    ping: "63 ms",
    loss: "0%",
    hour: "8.4 MiB",
    mtuOk: true,
  },
  {
    ip: "10.8.1.24",
    endpoint: "91.78.19.32:12198",
    location: "91.78.19.32",
    handshake: "6д 3ч",
    rx: "0 B/s",
    tx: "0 B/s",
    flow: "0 B/s",
    flowValue: 0,
    share: "0%",
    today: "0 B",
    total: "12.40 GiB",
    active: false,
    ping: "н/д",
    loss: "н/д",
    hour: "0 B",
    mtuOk: true,
  },
  {
    ip: "10.8.1.13",
    endpoint: "188.235.248.27:49368",
    location: "188.235.248.27",
    handshake: "3ч 53м",
    rx: "0 B/s",
    tx: "0 B/s",
    flow: "0 B/s",
    flowValue: 0,
    share: "0%",
    today: "11.20 MiB",
    total: "44.70 GiB",
    active: false,
    ping: "н/д",
    loss: "н/д",
    hour: "0 B",
    mtuOk: true,
  },
];

for (let index = 0; index < 18; index += 1) {
  const template = peers[index % 6];
  const flowValue = Math.max(0.28, 4.82 - index * 0.21);
  peers.push({
    ...template,
    ip: `10.8.1.${40 + index}`,
    endpoint: template.endpoint.replace(/:\d+$/, `:${39000 + index * 131}`),
    flow: `${flowValue.toFixed(2)} KiB/s`,
    flowValue,
    share: `${Math.max(0.31, 7.1 - index * 0.29).toFixed(2)}%`,
    ping: `${Math.round(38 + (index % 5) * 7)} ms`,
    loss: index === 11 ? "2.4%" : "0%",
    hour: `${Math.max(1.2, flowValue * 3.7).toFixed(1)} MiB`,
    mtuOk: index !== 9,
  });
}

const trafficSeries = [
  8, 12, 13, 14, 18, 26, 19, 31, 18, 23, 22, 35, 14, 13, 21, 12, 13, 23, 11, 12, 15, 18, 18, 18, 20, 13, 20, 11, 10, 13,
  17, 16, 13, 14, 12, 21, 16, 13, 15, 14, 10,
];

let selectedIp = "10.8.1.50";
let lastUpdated = new Date("2026-06-08T16:45:21+07:00");
let toastTimer = null;
let quickFilter = "all";
let chartScale = "detail";

const $ = (id) => document.getElementById(id);

function formatSyncLine() {
  const stamp = lastUpdated.toISOString().replace(".000Z", "+07:00");
  $("syncLine").textContent = `ssh tunnel // live vpn telemetry // ops cockpit • SYNC ${stamp} | AGE 2с | STEP 1s`;
}

function locationBucket(value) {
  if (!value) return "Нет данных";
  if (/Novosibirsk/i.test(value)) return "Novosibirsk";
  if (/Saint Petersburg/i.test(value)) return "Saint Petersburg";
  return value.split(",")[0];
}

function ageMinutes(peer) {
  const value = String(peer.handshake || "");
  if (value.includes("д")) return 24 * 60;
  if (value.includes("ч")) return Number.parseInt(value, 10) * 60 || 60;
  if (value.includes("м")) return Number.parseInt(value, 10) || 1;
  return 0;
}

function peerDiagnosis(peer) {
  if (!peer.active) return { key: "offline", label: "Нет связи", level: "danger" };
  if (!peer.mtuOk) return { key: "mtu", label: "MTU issue", level: "danger" };
  if (ageMinutes(peer) > 10) return { key: "stale", label: "Тихий >10м", level: "warn" };
  if (peer.flowValue >= 8) return { key: "top", label: "Топ трафика", level: "ok" };
  return { key: "ok", label: "Норма", level: "ok" };
}

function quickFilterMatches(peer) {
  const diagnosis = peerDiagnosis(peer);
  if (quickFilter === "all") return true;
  if (quickFilter === "active") return peer.active && peer.flowValue > 0;
  return diagnosis.key === quickFilter;
}

function visiblePeers() {
  const query = $("searchInput").value.trim().toLowerCase();
  const status = $("statusFilter").value;
  const location = $("locationFilter").value;
  return peers.filter((peer) => {
    if (!quickFilterMatches(peer)) return false;
    if (status === "Онлайн" && !peer.active) return false;
    if (status === "Нет связи" && peer.active) return false;
    if (location !== "Все" && locationBucket(peer.location) !== location) return false;
    if (!query) return true;
    return [peer.ip, peer.endpoint, peer.location, peer.handshake, peer.flow, peer.share].some((value) =>
      String(value).toLowerCase().includes(query),
    );
  });
}

function renderLocationOptions() {
  const select = $("locationFilter");
  const current = select.value || "Все";
  const locations = [...new Set(peers.map((peer) => locationBucket(peer.location)))].sort();
  select.innerHTML = `<option>Все</option>${locations.map((item) => `<option>${item}</option>`).join("")}`;
  select.value = locations.includes(current) ? current : "Все";
}

function renderTable() {
  const tbody = $("peerRows");
  const rows = visiblePeers();
  if (!rows.some((peer) => peer.ip === selectedIp)) {
    selectedIp = rows[0]?.ip || "";
  }

  tbody.innerHTML = rows
    .map(
      (peer) => {
        const diagnosis = peerDiagnosis(peer);
        return `
        <tr class="${peer.active ? "active" : ""} ${peer.ip === selectedIp ? "selected" : ""} issue-${diagnosis.key}" data-ip="${peer.ip}">
          <td><span class="status-dot" title="${peer.active ? "Онлайн" : "Нет связи"}"></span></td>
          <td>${peer.ip}</td>
          <td><span class="diagnosis ${diagnosis.level}">${diagnosis.label}</span></td>
          <td>${peer.location}</td>
          <td>${peer.handshake}</td>
          <td class="rx">${peer.rx}</td>
          <td class="tx">${peer.tx}</td>
          <td>${peer.flow}</td>
          <td>${peer.share}</td>
          <td>${peer.today}</td>
          <td>${peer.total}</td>
        </tr>`;
      },
    )
    .join("");

  tbody.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => {
      selectedIp = row.dataset.ip;
      renderTable();
      renderDetail();
    });
  });

  const activeCount = peers.filter((peer) => peer.active).length;
  const offlineCount = peers.filter((peer) => !peer.active).length;
  const issueCount = peers.filter((peer) => ["offline", "stale", "mtu"].includes(peerDiagnosis(peer).key)).length;
  $("visibleCount").textContent = `Показано ${rows.length}/${peers.length} | активных ${activeCount}`;
  $("tableCount").textContent = `${rows.length} peers`;
  $("opsSummary").textContent = `${peers.length} пиров • ${activeCount} активны • ${offlineCount} без связи • ${issueCount} требуют внимания • данные 2с назад`;
  $("kpiPeersNote").textContent = `онлайн ${activeCount} • без связи ${offlineCount} • требуют внимания ${issueCount}`;
}

function renderDetail() {
  const peer = peers.find((item) => item.ip === selectedIp) || visiblePeers()[0];
  if (!peer) {
    $("detailIp").textContent = "—";
    $("detailSubtitle").textContent = "Выберите строку в таблице";
    return;
  }

  $("detailIp").textContent = peer.ip;
  $("detailSubtitle").textContent = `${peer.location} | ${peer.endpoint}`;
  $("detailEndpoint").textContent = "46.8.254.243:443";
  $("detailStatus").textContent = peer.active ? "активен" : "нет связи";
  $("detailStatus").className = peer.active ? "good" : "";
  $("detailHandshake").textContent = peer.handshake;
  $("detailPing").textContent = peer.ping;
  $("detailLoss").textContent = peer.loss;
  $("detailLoss").className = peer.loss === "0%" ? "good" : "warn";
  $("detailFlow").textContent = peer.flow;
  $("detailShare").textContent = peer.share;
  $("detailToday").textContent = peer.today;
  $("detailTotal").textContent = peer.total;
  $("detailHour").textContent = peer.hour;
  $("detailMtu").textContent = peer.mtuOk ? "1280 OK" : "проверить 1420→1280";
  $("detailMtu").className = peer.mtuOk ? "good" : "warn";

  const note = $("detailNote");
  const diagnosis = peerDiagnosis(peer);
  note.textContent = diagnosis.key === "ok" || diagnosis.key === "top"
    ? "Активный пир, трафик идет сейчас"
    : diagnosis.key === "mtu"
      ? "Пир активен, но профиль требует сверки MTU target 1280"
      : "Нет свежего handshake, текущего трафика нет";
  note.classList.toggle("idle", diagnosis.level !== "ok");

  $("eventList").innerHTML = `
    <li>${peer.handshake}: ${peer.active ? "соединение активно" : "нет свежего handshake"}</li>
    <li>Трафик: ${peer.flow} / доля ${peer.share}</li>
    <li>Ping ${peer.ping}, потери ${peer.loss}</li>
    <li>${peer.mtuOk ? "MTU target 1280 подтвержден" : "MTU profile drift: требуется 1280"}</li>
  `;

  $("kpiLeader").textContent = peers[0].ip;
  $("kpiLeaderNote").textContent = `${peers[0].flow} • ${peers[0].share}`;
}

function svgPath(points) {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${point[0].toFixed(2)} ${point[1].toFixed(2)}`).join(" ");
}

function renderChart() {
  const svg = $("trafficChart");
  const width = 860;
  const height = 180;
  const pad = { x: 34, top: 24, bottom: 28 };
  const plotW = width - pad.x * 2;
  const plotH = height - pad.top - pad.bottom;
  const rx = trafficSeries.map((value) => value * 0.62);
  const tx = trafficSeries.map((value) => value * 0.38);
  const capacityKib = 122070;
  const maxLocal = chartScale === "real" ? capacityKib : Math.max(...trafficSeries, 130);

  function points(series) {
    return series.map((value, index) => {
      const x = pad.x + (plotW * index) / (series.length - 1);
      const y = height - pad.bottom - (plotH * value) / maxLocal;
      return [x, y];
    });
  }

  const totalPoints = points(trafficSeries);
  const area = `${svgPath(totalPoints)} L ${width - pad.x} ${height - pad.bottom} L ${pad.x} ${height - pad.bottom} Z`;
  const gridX = Array.from({ length: 8 }, (_, index) => pad.x + (plotW * (index + 1)) / 9);
  const gridY = Array.from({ length: 4 }, (_, index) => pad.top + (plotH * (index + 1)) / 5);

  svg.innerHTML = `
    <defs>
      <linearGradient id="chartFill" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="rgba(32, 223, 143, 0.18)" />
        <stop offset="1" stop-color="rgba(32, 223, 143, 0.02)" />
      </linearGradient>
    </defs>
    ${gridX.map((x) => `<line x1="${x}" x2="${x}" y1="${pad.top}" y2="${height - pad.bottom}" stroke="var(--grid)" />`).join("")}
    ${gridY.map((y) => `<line x1="${pad.x}" x2="${width - pad.x}" y1="${y}" y2="${y}" stroke="var(--grid)" />`).join("")}
    <line x1="${pad.x}" x2="${width - pad.x}" y1="${pad.top}" y2="${pad.top}" stroke="var(--faint)" />
    <text x="${pad.x}" y="17" fill="var(--muted)" font-size="11" font-family="var(--mono)">${chartScale === "real" ? "0..1 Gbps real scale" : "detail scale: visible low traffic"}</text>
    <text x="${width - 36}" y="17" text-anchor="end" fill="var(--muted)" font-size="11" font-family="var(--mono)">RX blue / TX green</text>
    <text x="${pad.x}" y="${height - 8}" fill="var(--muted)" font-size="10" font-family="var(--mono)">-90с</text>
    <text x="${width - pad.x}" y="${height - 8}" text-anchor="end" fill="var(--muted)" font-size="10" font-family="var(--mono)">сейчас</text>
    <line x1="${pad.x + plotW * 0.72}" x2="${pad.x + plotW * 0.72}" y1="${pad.top}" y2="${height - pad.bottom}" stroke="var(--warn)" stroke-dasharray="4 5" />
    <text x="${pad.x + plotW * 0.72 + 6}" y="${pad.top + 12}" fill="var(--warn)" font-size="10" font-family="var(--mono)">пик</text>
    <path d="${area}" fill="url(#chartFill)"></path>
    <path d="${svgPath(points(rx))}" fill="none" stroke="var(--blue)" stroke-width="3"></path>
    <path d="${svgPath(points(tx))}" fill="none" stroke="var(--good)" stroke-width="3"></path>
  `;
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_error) {
    const input = document.createElement("textarea");
    input.value = text;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.focus();
    input.select();
    document.execCommand("copy");
    input.remove();
  }
}

function handleRefresh() {
  const button = $("refreshButton");
  button.disabled = true;
  button.textContent = "Обновляется...";
  $("agePill").textContent = "SYNC";
  $("agePill").classList.add("warn");
  lastUpdated = new Date(lastUpdated.getTime() + 1000);
  const next = Math.max(8, Math.round((trafficSeries.at(-1) + 8 + Math.random() * 14) * 10) / 10);
  trafficSeries.push(next);
  trafficSeries.shift();
  setTimeout(() => {
    formatSyncLine();
    renderChart();
    $("agePill").textContent = "AGE 2с";
    $("agePill").classList.remove("warn");
    $("trustState").textContent = "Источник: dashboard.json • обновлено 2с назад • данные свежие • сервер доступен";
    button.disabled = false;
    button.textContent = "Обновить";
    showToast("Snapshot обновлен. Серверные настройки не менялись.");
  }, 520);
}

function bindControls() {
  ["searchInput", "statusFilter", "periodFilter", "locationFilter"].forEach((id) => {
    $(id).addEventListener("input", () => {
      renderTable();
      renderDetail();
    });
  });

  $("resetFilters").addEventListener("click", () => {
    $("searchInput").value = "";
    $("statusFilter").value = "Все";
    $("periodFilter").value = "Сегодня";
    $("locationFilter").value = "Все";
    selectedIp = "10.8.1.50";
    renderTable();
    renderDetail();
  });

  $("refreshButton").addEventListener("click", handleRefresh);
  $("scaleToggle").addEventListener("click", () => {
    chartScale = chartScale === "detail" ? "real" : "detail";
    $("scaleToggle").textContent = chartScale === "detail" ? "Масштаб: детальный" : "Масштаб: реальный";
    renderChart();
  });
  $("closeButton").addEventListener("click", () => {
    $("linkState").textContent = "LINK CLOSED (prototype)";
    showToast("В прототипе закрытие только помечает состояние. В приложении VPN не выключается.");
  });

  document.querySelectorAll(".quick-filter").forEach((button) => {
    button.addEventListener("click", () => {
      quickFilter = button.dataset.filter;
      if (quickFilter !== "all") {
        $("statusFilter").value = "Все";
      }
      document.querySelectorAll(".quick-filter").forEach((item) => item.classList.toggle("active", item === button));
      renderTable();
      renderDetail();
    });
  });

  $("copyEndpoint").addEventListener("click", async () => {
    const peer = peers.find((item) => item.ip === selectedIp) || peers[0];
    await copyText(peer.endpoint);
    showToast(`Endpoint скопирован: ${peer.endpoint}`);
  });

  $("copyProfile").addEventListener("click", async () => {
    await copyText("Endpoint = 46.8.254.243:443\nMTU = 1280\nPersistentKeepalive = 25");
    showToast("Профиль телефона скопирован.");
  });
}

renderLocationOptions();
formatSyncLine();
renderChart();
renderTable();
renderDetail();
bindControls();
