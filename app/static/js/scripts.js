// app/static/js/scripts.js

// ==============================
// Constantes
// ==============================

// Délai max pour fetch (évite les requêtes bloquées)
const FETCH_TIMEOUT_MS = 8000;

// 1x1 PNG transparent (placeholder local, pas de fichier statique requis)
const PLACEHOLDER_THUMB =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

// ==============================
// Utilitaires
// ==============================

function log(...args) { console.log("[RPi-AVP]", ...args); }

// fetch avec timeout (AbortController)
async function fetchWithTimeout(url, options = {}, timeoutMs = FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(id);
    return res;
  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}

// parse JSON sans casser le flux si la réponse n'est pas JSON
async function parseJsonSafe(res) {
  try { return await res.json(); } catch { return null; }
}

// ==============================
// API: commandes VLC & lecture
// ==============================

async function sendAction(action) {
  try {
    const res = await fetchWithTimeout(`/control/${encodeURIComponent(action)}`, { method: "POST" });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      console.error("[control] HTTP", res.status, body);
      return null;
    }
    const data = await parseJsonSafe(res);
    log("[control]", action, data);
    return data;
  } catch (err) {
    console.error("[control][error]", action, err);
    return null;
  }
}

async function playVideo(videoName) {
  log("[play-video] ->", videoName);
  try {
    const res = await fetchWithTimeout("/play-video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video: videoName }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      console.error("[play-video] HTTP", res.status, body);
      return;
    }
    const data = await parseJsonSafe(res);
    log("[play-video][ok]", data);
  } catch (err) {
    console.error("[play-video][error]", err);
  }
}

// ==============================
// Rattachement des handlers DOM
// ==============================

function attachClickHandlers() {
  // Boutons de contrôle VLC (sauf Play/Pause intelligent)
  document.querySelectorAll(".vlc-btn").forEach((btn) => {
    if (btn.id === "btn-playpause") return; // géré séparément
    const action = btn.dataset.action;
    if (!action) return;
    btn.addEventListener("click", () => sendAction(action), { passive: true });
  });

  // Clic sur une carte vidéo → lecture
  document.querySelectorAll(".video-item").forEach((item) => {
    item.addEventListener(
      "click",
      () => {
        const name = item.dataset.name || item.getAttribute("data-name");
        if (name) playVideo(name);
      },
      { passive: true }
    );
  });

  // Fallback pour miniatures cassées → 1x1 transparent
  document.querySelectorAll(".video-thumb").forEach((img) => {
    img.addEventListener("error", () => {
      if (img.dataset.fallbackApplied) return;
      img.dataset.fallbackApplied = "1";
      img.src = PLACEHOLDER_THUMB;
    });
  });

  // Bouton d'accès à la page paramètres
  const btnSettings = document.getElementById("btn-settings");
  if (btnSettings) {
    btnSettings.addEventListener("click", () => { window.location.href = "/settings"; }, { passive: true });
  }

  // Bouton Play/Pause intelligent
  const btnPP = document.getElementById("btn-playpause");
  if (btnPP) {
    btnPP.addEventListener("click", togglePlayPause, { passive: true });
  }
}

// -------- Aperçu HLS (toggle + volume local) --------
let hlsInstance = null;

function showPreviewUI(show) {
  const wrap = document.getElementById("preview-wrap");
  const ph = document.getElementById("vlc-output");
  if (!wrap || !ph) return;
  wrap.style.display = show ? "block" : "none";
  ph.style.display = show ? "none" : "flex";
}

function applyVideoNoControls(video) {
  video.controls = false;
  video.setAttribute("disablepictureinpicture", "");
  video.setAttribute("disableremoteplayback", "");
  video.setAttribute("controlslist", "nodownload noplaybackrate nofullscreen");
  video.addEventListener("contextmenu", (e) => e.preventDefault());
}

function initLocalVolumeUI(video) {
  const slider = document.getElementById("preview-vol");
  if (!slider || !video) return;

  const saved = Number(localStorage.getItem("previewVolume") || "0");
  const vol0to1 = Math.min(1, Math.max(0, saved / 100));
  video.volume = vol0to1;
  video.muted = vol0to1 === 0;
  slider.value = String(Math.round(vol0to1 * 100));

  slider.addEventListener("input", () => {
    const v = Number(slider.value || "0");
    const f = Math.min(1, Math.max(0, v / 100));
    video.volume = f;
    video.muted = v === 0;
    localStorage.setItem("previewVolume", String(v));
  });
}

function startHlsPlayback(url) {
  const video = document.getElementById("preview-video");
  if (!video) return;

  showPreviewUI(true);
  applyVideoNoControls(video);
  initLocalVolumeUI(video);

  if (window.Hls && Hls.isSupported()) {
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
    hlsInstance = new Hls({ liveSyncDuration: 4, maxLiveSyncPlaybackRate: 1.0 });
    hlsInstance.loadSource(url);
    hlsInstance.attachMedia(video);
    hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    video.play().catch(() => {});
  } else {
    console.warn("HLS non supporté par ce navigateur");
  }
}

function stopHlsPlayback() {
  const video = document.getElementById("preview-video");
  if (video) {
    try { video.pause(); } catch {}
    video.removeAttribute("src");
  }
  if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
  showPreviewUI(false);
}

async function refreshPreviewToggleUI() {
  try {
    const r = await fetch("/api/preview/status");
    if (!r.ok) return;
    const s = await r.json();
    const cb = document.getElementById("preview-toggle");
    if (cb) cb.checked = !!s.enabled;
    if (s.enabled) startHlsPlayback(s.url || "/hls/index.m3u8");
    else stopHlsPlayback();
  } catch {}
}

function wirePreviewToggle() {
  const cb = document.getElementById("preview-toggle");
  if (!cb) return;
  cb.addEventListener("change", async () => {
    try {
      if (cb.checked) {
        const r = await fetch("/api/preview/enable", { method: "POST" });
        const s = await r.json().catch(() => ({}));
        startHlsPlayback((s && s.url) || "/hls/index.m3u8");
      } else {
        await fetch("/api/preview/disable", { method: "POST" });
        stopHlsPlayback();
      }
    } catch (e) {
      console.error("preview toggle error:", e);
    }
  });
}

// ===== Bouton Play/Pause intelligent =====

function updatePlayPauseUI(isPlaying) {
  const btn = document.getElementById("btn-playpause");
  if (!btn) return;
  const icon = btn.querySelector("i");
  btn.dataset.action = isPlaying ? "pause" : "play";
  icon.className = `fa-solid fa-${isPlaying ? "pause" : "play"}`;
  btn.title = isPlaying ? "Pause" : "Lecture";
  btn.classList.toggle("active", isPlaying);
}

async function getIsPlaying() {
  try {
    const r = await fetchWithTimeout("/status", { method: "GET" });
    if (!r.ok) return false;
    const s = await r.json();
    return s.state === "playing";
  } catch { return false; }
}

async function togglePlayPause() {
  const btn = document.getElementById("btn-playpause");
  if (!btn) return;

  // 1) Lis l'état réel
  const isPlayingNow = await getIsPlaying();
  const action = isPlayingNow ? "pause" : "play";

  // 2) Envoie la commande correspondante
  await sendAction(action);

  // 3) Petite anim' puis resync avec l'état réel (évite tout décalage)
  btn.classList.add("switching");
  setTimeout(async () => {
    const isPlayingAfter = await getIsPlaying();
    updatePlayPauseUI(isPlayingAfter);
    btn.classList.remove("switching");
  }, 200);
}

async function syncPlayButton() {
  try {
    const r = await fetchWithTimeout('/status', { method: 'GET' });
    if (!r.ok) return;
    const s = await r.json();
    updatePlayPauseUI(s.state === 'playing');
    updateStatusPanelPayload(s);
  } catch {}
}

function updateStatusPanelPayload(s){
  // ---- Titre
  const titleEl = document.getElementById('s-title');
  if (titleEl) {
    const title = (s && s.current) ? s.current : '—';
    titleEl.textContent = title;
    titleEl.setAttribute('title', title);   // tooltip plein titre
  }

  // ---- État: badge + icône + texte
  const badge = document.getElementById('s-state-badge');
  const stateIcon = document.getElementById('s-state-icon');
  const stateText = document.getElementById('s-state-text');

  const raw = (s && s.state) ? String(s.state).toLowerCase() : 'idle';

  // mapping état → icône FA + classe couleur + libellé
  const map = {
    playing:  { icon: 'fa-circle-play',   cls: 'is-playing',  label: 'Lecture' },
    paused:   { icon: 'fa-circle-pause',  cls: 'is-paused',   label: 'Pause' },
    stopped:  { icon: 'fa-circle-stop',   cls: 'is-stopped',  label: 'Arrêt' },
    opening:  { icon: 'fa-compact-disc',  cls: 'is-opening',  label: 'Ouverture' },
    buffering:{ icon: 'fa-spinner',       cls: 'is-buffering',label: 'Buffering' },
    ended:    { icon: 'fa-flag-checkered',cls: 'is-ended',    label: 'Terminé' },
    error:    { icon: 'fa-triangle-exclamation', cls: 'is-error', label: 'Erreur' },
    idle:     { icon: 'fa-circle',        cls: 'is-stopped',  label: 'Inactif' }
  };
  const m = map[raw] || map.idle;

  if (stateIcon) {
    stateIcon.className = `fa-solid ${m.icon}` + (raw === 'buffering' ? ' fa-spin' : '');
  }
  if (stateText) {
    stateText.textContent = m.label;
  }
  if (badge) {
    // nettoie les classes précédentes
    badge.className = 'status-badge';
    badge.classList.add(m.cls);
  }

  // ---- Volume (% + icône adaptée)
  const volText = document.getElementById('s-vol');
  const volIcon = document.getElementById('s-vol-icon');
  let vol = (s && typeof s.volume === 'number') ? Math.max(0, Math.min(100, s.volume)) : null;

  if (volText) volText.textContent = (vol === null ? '—' : `${vol}%`);

  if (volIcon) {
    let vIcon = 'fa-volume-off';
    if (vol !== null) {
      if (vol === 0) vIcon = 'fa-volume-xmark';
      else if (vol <= 30) vIcon = 'fa-volume-low';
      else if (vol <= 70) vIcon = 'fa-volume';
      else vIcon = 'fa-volume-high';
    }
    volIcon.className = `fa-solid ${vIcon}`;
  }
    requestAnimationFrame(updateTitleOverflow);
}

function updateTitleOverflow() {
  const row = document.querySelector('.status-title');
  const mask = document.querySelector('.status-title-mask');
  const txt  = document.getElementById('s-title');
  if (!row || !mask || !txt) return;

  // mesure le débordement horizontal
  const needsScroll = txt.scrollWidth > mask.clientWidth + 2; // marge anti-flap
  row.classList.toggle('is-overflowing', needsScroll);

  if (needsScroll) {
    const overflowPx = Math.max(0, txt.scrollWidth - mask.clientWidth);
    // durée proportionnelle (8–20 s)
    const dur = Math.max(8, Math.min(20, overflowPx / 30));
    txt.style.setProperty('--scroll-px', overflowPx + 'px');
    txt.style.setProperty('--scroll-duration', dur + 's');
    // ellipsis OFF pour le scroll (le mask fait le boulot)
    txt.style.textOverflow = 'clip';
  } else {
    txt.style.removeProperty('--scroll-px');
    txt.style.removeProperty('--scroll-duration');
    txt.style.textOverflow = 'ellipsis';
  }
}

// ==============================
// Initialisation
// ==============================

document.addEventListener("DOMContentLoaded", () => {
  // UI Aperçu HLS
  wirePreviewToggle();
  refreshPreviewToggleUI();

  // Contrôles & handlers
  attachClickHandlers();

  // Sync initiale du bouton Play/Pause + polling périodique
  syncPlayButton();
  setInterval(syncPlayButton, 3000);
});

window.addEventListener('resize', () => {
  // léger debounce
  clearTimeout(window.__st_overflow_t);
  window.__st_overflow_t = setTimeout(updateTitleOverflow, 100);
});