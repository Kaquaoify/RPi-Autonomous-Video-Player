// --- Helpers HTTP ---
async function jget(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error("GET " + url + " -> " + r.status);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!r.ok) throw new Error("POST " + url + " -> " + r.status);
  return r.json();
}

// --- DOM refs ---
const elVideoList   = () => document.getElementById("video-list");
const elBtnPlay     = () => document.getElementById("btn-playpause");
const elPrevToggle  = () => document.getElementById("preview-toggle");
const elPrevWrap    = () => document.getElementById("preview-wrap");
const elPrevVideo   = () => document.getElementById("preview-video");
const elPrevVol     = () => document.getElementById("preview-vol");
const elTitle       = () => document.getElementById("s-title");
const elStateBadge  = () => document.getElementById("s-state-badge");
const elStateIcon   = () => document.getElementById("s-state-icon");
const elStateText   = () => document.getElementById("s-state-text");
const elVolIcon     = () => document.getElementById("s-vol-icon");
const elVolText     = () => document.getElementById("s-vol");

// --- State ---
let LAST_STATE = "idle";
let POLL_TIMER = null;
let HLS_OBJ = null;

// --- Status helpers ---
function setStateUI(state) {
  LAST_STATE = state;
  elStateText().textContent = state;
  // badge color/icon
  let icon = "fa-circle-play";
  let badgeBg = "#222";
  let badgeBorder = "#333";
  if (state === "playing") { icon = "fa-circle-play"; badgeBg = "#143b14"; }
  else if (state === "paused") { icon = "fa-circle-pause"; badgeBg = "#2a2a0f"; }
  else if (state === "stopped") { icon = "fa-stop"; }
  else if (state === "buffering" || state === "opening") { icon = "fa-spinner"; }
  else if (state === "ended") { icon = "fa-circle-check"; }
  else if (state === "error") { icon = "fa-triangle-exclamation"; badgeBg = "#3b1414"; }

  elStateIcon().className = "fa-solid " + icon;
  elStateBadge().style.background = badgeBg;
  elStateBadge().style.borderColor = badgeBorder;

  // Play/pause toggle
  if (state === "playing") {
    elBtnPlay().dataset.action = "pause";
    elBtnPlay().title = "Pause";
    elBtnPlay().innerHTML = '<i class="fa-solid fa-pause"></i>';
  } else {
    elBtnPlay().dataset.action = "play";
    elBtnPlay().title = "Lecture";
    elBtnPlay().innerHTML = '<i class="fa-solid fa-play"></i>';
  }
}

function setTitleUI(name) {
  elTitle().textContent = name || "—";
  elTitle().title = name || "—";
}

function setVolumeUI(volume, muted) {
  elVolText().textContent = muted ? "muet" : (volume ?? "—");
  elVolIcon().className = "fa-solid " + (muted ? "fa-volume-xmark" :
    (volume >= 66 ? "fa-volume-high" : volume >= 33 ? "fa-volume-low" : "fa-volume-off"));
}

// --- Poll /status ---
async function pollStatus() {
  try {
    const s = await jget("/status");
    setStateUI(s.state || "idle");
    setTitleUI(s.current_name || s.loaded_name || "—");
    setVolumeUI(s.volume || 0, !!s.muted);
  } catch (e) {
    setStateUI("error");
  }
}

function startPolling() {
  clearInterval(POLL_TIMER);
  POLL_TIMER = setInterval(pollStatus, 1500);
  pollStatus();
}

// --- Populate video list ---
async function loadVideoList() {
  try {
    const j = await jget("/control/videos");
    if (!j.ok) return;
    const items = j.items || [];
    const list = elVideoList();
    list.innerHTML = "";
    for (const it of items) {
      const div = document.createElement("div");
      div.className = "video-item";
      div.dataset.name = it.name;

      const thumb = document.createElement("div");
      thumb.className = "thumbnail";
      const img = document.createElement("img");
      img.className = "video-thumb";
      img.alt = it.name;
      img.loading = "lazy";
      img.src = (window.thumbUrl ? window.thumbUrl(it.name) : ("/thumbnails/" + it.name + ".jpg"));
      thumb.appendChild(img);

      const title = document.createElement("div");
      title.className = "video-title";
      const scroll = document.createElement("div");
      scroll.className = "scrolling-text";
      scroll.textContent = it.name;
      title.appendChild(scroll);

      div.appendChild(thumb);
      div.appendChild(title);
      div.addEventListener("click", () => playVideoByName(it.name));
      list.appendChild(div);
    }
  } catch (e) {
    console.warn("loadVideoList failed:", e);
  }
}

// --- Controls ---
async function playVideo(videoName) {
  log("[play-video] ->", videoName);
  try {
    const res = await fetchWithTimeout("/control/play-video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: videoName }), // <-- clé attendue par la nouvelle API
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

async function handleToolbarClick(e) {
  const btn = e.target.closest(".vlc-btn");
  if (!btn) return;
  const action = btn.dataset.action;
  try {
    if (action === "play") await jpost("/control/play");
    else if (action === "pause") await jpost("/control/pause");
    else if (action === "next") await jpost("/control/next");
    else if (action === "prev") await jpost("/control/prev");
    else if (action === "volup") await jpost("/control/vol_up");
    else if (action === "voldown") await jpost("/control/vol_down");
    startPolling();
  } catch (err) {
    alert("Erreur contrôle: " + err.message);
  }
}

function bindToolbar() {
  document.body.addEventListener("click", handleToolbarClick);
}

// --- Preview (HLS) ---
function attachHlsToVideo(url) {
  const video = elPrevVideo();
  if (Hls && Hls.isSupported()) {
    if (HLS_OBJ) { HLS_OBJ.destroy(); HLS_OBJ = null; }
    HLS_OBJ = new Hls({ lowLatencyMode: true, liveDurationInfinity: true });
    HLS_OBJ.loadSource(url);
    HLS_OBJ.attachMedia(video);
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
  } else {
    console.warn("HLS non supporté par ce navigateur.");
  }
}

async function loadPreviewStatusAndWire() {
  // Si la page index a un toggle preview
  if (!elPrevToggle()) return;
  try {
    const rep = await jget("/api/preview/status");
    const enabled = !!(rep.preview && rep.preview.enabled);
    elPrevToggle().checked = enabled;
    elPrevWrap().style.display = enabled ? "block" : "none";
    if (enabled && rep.preview.index_url) {
      attachHlsToVideo(rep.preview.index_url);
      elPrevVideo().play().catch(()=>{});
    }
  } catch (e) {
    console.warn("preview status error:", e);
  }

  elPrevToggle().addEventListener("change", async () => {
    try {
      const enabled = elPrevToggle().checked;
      const url = enabled ? "/api/preview/enable" : "/api/preview/disable";
      const rep = await jpost(url);
      if (enabled) {
        elPrevWrap().style.display = "block";
        if (rep.preview && rep.preview.index_url) {
          attachHlsToVideo(rep.preview.index_url);
          elPrevVideo().play().catch(()=>{});
        }
      } else {
        elPrevWrap().style.display = "none";
        if (HLS_OBJ) { HLS_OBJ.destroy(); HLS_OBJ = null; }
        elPrevVideo().pause();
        elPrevVideo().removeAttribute("src");
        elPrevVideo().load();
      }
    } catch (e) {
      // revert
      elPrevToggle().checked = !elPrevToggle().checked;
      alert("Erreur aperçu: " + e.message);
    }
  });

  // Volume overlay (local au navigateur)
  elPrevVol() && elPrevVol().addEventListener("input", () => {
    elPrevVideo().volume = Math.max(0, Math.min(1, (parseInt(elPrevVol().value, 10) || 0) / 100));
  });
}

// --- Boot ---
(async function boot() {
  try {
    await loadVideoList();
    bindToolbar();
    startPolling();
    await loadPreviewStatusAndWire();
  } catch (e) {
    console.warn("boot error:", e);
  }
})();
