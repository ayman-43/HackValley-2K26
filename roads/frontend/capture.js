/* Nagrik Roads phone capture: phone-as-dashcam.
 * Ride mode: rear-camera video (MediaRecorder) + GPS track [{t, lat, lon, speed, accuracy}] (t = s since start)
 *            + optional accelerometer bumps [{t, g}] -> POST /api/inspections (file + gps_track=track.json).
 * Photo mode: still frame + current location -> POST /api/inspections (file + lat + lon).
 * Plain JS, no build step. Camera/GPS need a secure context (HTTPS or localhost). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const API = "/api";
  const GPS_MIN_INTERVAL_S = 0.9; // ~1 point per second
  const BUMP_THRESHOLD_G = 0.6;   // deviation from 1 g that counts as a bump
  const BUMP_REFRACTORY_MS = 400;

  const state = {
    mode: "ride",
    stream: null,
    recorder: null,
    chunks: [],
    mime: "",
    recording: false,
    t0: 0,
    timerId: null,
    watchId: null,
    lastFix: null,      // latest GeolocationPosition
    track: [],
    bumps: [],
    lastBumpAt: 0,
    bumpsOn: false,
    photoBlob: null,
    photoFix: null,
    wakeLock: null,
  };

  // ---------- helpers ----------
  function setStatus(html, isError) {
    const el = $("status");
    el.innerHTML = html;
    el.style.color = isError ? "var(--danger)" : "";
  }

  function fmtTime(sec) {
    const s = Math.floor(sec);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  function pickMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    const prefs = [
      "video/mp4;codecs=avc1",
      "video/mp4",
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm",
    ];
    return prefs.find((m) => MediaRecorder.isTypeSupported(m)) || "";
  }

  function extFor(mime) {
    return mime.indexOf("mp4") >= 0 ? "mp4" : "webm";
  }

  function nowS() {
    return (performance.now() - state.t0) / 1000;
  }

  async function keepAwake(on) {
    try {
      if (on && "wakeLock" in navigator) state.wakeLock = await navigator.wakeLock.request("screen");
      else if (!on && state.wakeLock) { await state.wakeLock.release(); state.wakeLock = null; }
    } catch (e) { /* not critical */ }
  }

  // ---------- secure context ----------
  function checkSecure() {
    const ok = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    if (!ok) {
      $("secure-banner").classList.remove("hidden");
      $("origin-hint").textContent = location.origin;
      setStatus("Camera/GPS unavailable on this connection. Use “Upload from the camera app” below.", true);
    }
    return ok;
  }

  // ---------- camera ----------
  async function startCamera() {
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
        audio: false,
      });
      $("preview").srcObject = state.stream;
      $("btn-start").disabled = false;
      $("btn-snap").disabled = false;
      state.mime = pickMime();
      setStatus("Ready. " + (state.mime ? "Recording as " + state.mime.split(";")[0] + "." : "Video recording not supported in this browser; use Photo mode."));
      if (!window.MediaRecorder) $("btn-start").disabled = true;
    } catch (e) {
      setStatus("Camera error: " + (e && e.message ? e.message : e) + ". Allow camera access and reload.", true);
    }
  }

  // ---------- GPS ----------
  function onFix(pos) {
    state.lastFix = pos;
    const c = pos.coords;
    const acc = Math.round(c.accuracy);
    $("acc").textContent = acc;
    $("acc").className = acc <= 10 ? "gps-good" : acc <= 30 ? "gps-ok" : "gps-bad";
    $("gps-pill").textContent = "GPS ±" + acc + " m";
    $("spd").textContent = c.speed != null && !isNaN(c.speed) ? (c.speed * 3.6).toFixed(0) : "–";
    if (state.recording) {
      const t = nowS();
      const last = state.track[state.track.length - 1];
      if (!last || t - last.t >= GPS_MIN_INTERVAL_S) {
        state.track.push({
          t: +t.toFixed(2),
          lat: +c.latitude.toFixed(7),
          lon: +c.longitude.toFixed(7),
          speed: c.speed != null && !isNaN(c.speed) ? +c.speed.toFixed(2) : null,
          accuracy: +c.accuracy.toFixed(1),
        });
        $("pts").textContent = state.track.length;
      }
    }
  }

  function onGpsError(err) {
    $("gps-pill").textContent = "GPS off";
    $("acc").textContent = "–";
    if (err && err.code === 1) setStatus("Location permission denied. Detections will have no position unless you allow it.", true);
  }

  function startGps() {
    if (!("geolocation" in navigator)) { onGpsError(); return; }
    state.watchId = navigator.geolocation.watchPosition(onFix, onGpsError, {
      enableHighAccuracy: true, maximumAge: 1000, timeout: 15000,
    });
  }

  // ---------- bumps (DeviceMotion) ----------
  function onMotion(ev) {
    if (!state.recording) return;
    const a = ev.accelerationIncludingGravity;
    if (!a || a.x == null) return;
    const g = Math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z) / 9.81;
    const dev = Math.abs(g - 1);
    const now = performance.now();
    if (dev >= BUMP_THRESHOLD_G && now - state.lastBumpAt > BUMP_REFRACTORY_MS) {
      state.lastBumpAt = now;
      state.bumps.push({ t: +nowS().toFixed(2), g: +dev.toFixed(2) });
      $("bump-count").textContent = state.bumps.length + " bump(s) logged.";
    }
  }

  async function toggleBumps(on) {
    if (on) {
      try {
        if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
          const r = await DeviceMotionEvent.requestPermission(); // iOS
          if (r !== "granted") throw new Error("permission " + r);
        }
        if (typeof DeviceMotionEvent === "undefined") throw new Error("no accelerometer");
        window.addEventListener("devicemotion", onMotion);
        state.bumpsOn = true;
        $("bump-count").textContent = "Bump logging on.";
      } catch (e) {
        $("bumps").checked = false;
        state.bumpsOn = false;
        $("bump-count").textContent = "Motion sensor unavailable (" + (e.message || e) + ").";
      }
    } else {
      window.removeEventListener("devicemotion", onMotion);
      state.bumpsOn = false;
      $("bump-count").textContent = "";
    }
  }

  // ---------- ride recording ----------
  function startRide() {
    if (!state.stream) return;
    state.chunks = [];
    state.track = [];
    state.bumps = [];
    try {
      state.recorder = state.mime
        ? new MediaRecorder(state.stream, { mimeType: state.mime, videoBitsPerSecond: 2500000 })
        : new MediaRecorder(state.stream);
    } catch (e) {
      setStatus("Could not start recorder: " + e.message, true);
      return;
    }
    state.mime = state.recorder.mimeType || state.mime || "video/webm";
    state.recorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) state.chunks.push(ev.data); };
    state.recorder.onstop = finishRide;
    state.t0 = performance.now();
    state.recording = true;
    state.recorder.start(1000);
    if (state.lastFix) onFix(state.lastFix); // seed point at t≈0
    $("pts").textContent = state.track.length;
    $("rec-dot").classList.remove("hidden");
    $("btn-start").classList.add("hidden");
    $("btn-stop").classList.remove("hidden");
    $("tab-photo").disabled = true;
    state.timerId = setInterval(() => { $("timer").textContent = fmtTime(nowS()); }, 250);
    keepAwake(true);
    setStatus("Recording… drive normally. Press <b>Stop &amp; upload</b> when done.");
  }

  function stopRide() {
    if (!state.recorder || !state.recording) return;
    state.recording = false;
    clearInterval(state.timerId);
    $("rec-dot").classList.add("hidden");
    $("btn-stop").disabled = true;
    state.recorder.stop();
    keepAwake(false);
  }

  function finishRide() {
    const type = (state.mime || "video/webm").split(";")[0];
    const blob = new Blob(state.chunks, { type: type });
    const duration = nowS();
    const trackJson = state.bumpsOn || state.bumps.length
      ? { points: state.track, bumps: state.bumps, duration_s: +duration.toFixed(2), source: "roadsense-capture" }
      : state.track;
    const fd = new FormData();
    fd.append("file", blob, "ride-" + Date.now() + "." + extFor(type));
    fd.append("gps_track", new Blob([JSON.stringify(trackJson)], { type: "application/json" }), "track.json");
    addCommonFields(fd);
    $("btn-stop").classList.add("hidden");
    $("btn-stop").disabled = false;
    $("btn-start").classList.remove("hidden");
    $("tab-photo").disabled = false;
    if (!state.track.length) setStatus("No GPS fix during the ride; uploading video without positions…");
    upload(fd, (blob.size / 1048576).toFixed(1) + " MB video, " + state.track.length + " GPS points");
  }

  // ---------- photo ----------
  function snapPhoto() {
    const v = $("preview");
    if (!v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    state.photoFix = state.lastFix;
    c.toBlob((b) => {
      state.photoBlob = b;
      const img = $("photo-preview");
      if (img.src) URL.revokeObjectURL(img.src);
      img.src = URL.createObjectURL(b);
      img.classList.remove("hidden");
      $("preview").classList.add("hidden");
      $("btn-snap").textContent = "Retake";
      $("btn-send-photo").classList.remove("hidden");
      setStatus(state.photoFix
        ? "Photo ready at ±" + Math.round(state.photoFix.coords.accuracy) + " m. Upload it?"
        : "Photo ready, but no GPS fix yet. You can still upload it.");
    }, "image/jpeg", 0.92);
  }

  function retake() {
    $("photo-preview").classList.add("hidden");
    $("preview").classList.remove("hidden");
    $("btn-snap").textContent = "Take photo";
    $("btn-send-photo").classList.add("hidden");
    state.photoBlob = null;
  }

  function sendPhoto() {
    if (!state.photoBlob) return;
    const fd = new FormData();
    fd.append("file", state.photoBlob, "photo-" + Date.now() + ".jpg");
    const fix = state.photoFix || state.lastFix;
    if (fix) {
      fd.append("lat", fix.coords.latitude.toFixed(7));
      fd.append("lon", fix.coords.longitude.toFixed(7));
    }
    addCommonFields(fd);
    upload(fd, "photo" + (fix ? " with location" : " without location"));
  }

  function sendFile() {
    const f = $("file-input").files[0];
    if (!f) { setStatus("Choose a photo or video first.", true); return; }
    const fd = new FormData();
    fd.append("file", f, f.name);
    if (state.lastFix && f.type.indexOf("image/") === 0) {
      fd.append("lat", state.lastFix.coords.latitude.toFixed(7));
      fd.append("lon", state.lastFix.coords.longitude.toFixed(7));
    }
    addCommonFields(fd);
    upload(fd, f.name);
  }

  function addCommonFields(fd) {
    const rt = $("road-type").value;
    const notes = $("notes").value.trim();
    if (rt) fd.append("road_type", rt);
    if (notes) fd.append("notes", notes);
  }

  // ---------- upload + job polling ----------
  function upload(fd, label) {
    const bar = $("progress");
    bar.classList.remove("hidden");
    bar.value = 0;
    $("result").classList.add("hidden");
    setStatus("Uploading " + label + "…");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API + "/inspections");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        bar.value = e.loaded / e.total;
        setStatus("Uploading " + label + "… " + Math.round((100 * e.loaded) / e.total) + "%");
      }
    };
    xhr.onerror = () => setStatus("Upload failed (network). Check that the phone can reach " + location.host + ".", true);
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
      if (xhr.status < 200 || xhr.status >= 300 || !body) {
        setStatus("Upload failed: HTTP " + xhr.status + " " + ((body && (body.detail || body.error)) || ""), true);
        return;
      }
      bar.value = 0;
      setStatus("Uploaded. Detecting road damage…");
      pollJob(body.job_id, body.inspection_id);
    };
    xhr.send(fd);
  }

  function pollJob(jobId, inspectionId) {
    const bar = $("progress");
    const link = '<a class="btn primary" href="/#/inspection/' + encodeURIComponent(inspectionId) + '">Open inspection #' + inspectionId + "</a>";
    if (jobId == null) { showResult(link, "Uploaded."); return; }
    let failures = 0;
    const tick = async () => {
      try {
        const r = await fetch(API + "/jobs/" + encodeURIComponent(jobId), { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        failures = 0;
        bar.value = Math.max(0, Math.min(1, +j.progress || 0));
        if (j.status === "done") {
          const res = j.result || {};
          const bits = [];
          if (res.unique_defects != null) bits.push(res.unique_defects + " unique defect(s)");
          if (res.raw_detections != null) bits.push("from " + res.raw_detections + " raw detections");
          showResult(link, "Done. " + bits.join(" "));
          return;
        }
        if (j.status === "error") {
          setStatus("Processing failed: " + (j.error || "unknown error"), true);
          return;
        }
        setStatus((j.status === "queued" ? "Queued… " : "Processing… ") + Math.round(100 * bar.value) + "%");
      } catch (e) {
        if (++failures > 5) { showResult(link, "Lost contact with the server; the job keeps running there."); return; }
      }
      setTimeout(tick, 1500);
    };
    tick();
  }

  function showResult(linkHtml, msg) {
    $("progress").classList.add("hidden");
    setStatus(msg);
    const r = $("result");
    r.innerHTML = linkHtml;
    r.classList.remove("hidden");
  }

  // ---------- tabs ----------
  function setMode(mode) {
    state.mode = mode;
    $("tab-ride").setAttribute("aria-selected", String(mode === "ride"));
    $("tab-photo").setAttribute("aria-selected", String(mode === "photo"));
    $("ride-panel").classList.toggle("hidden", mode !== "ride");
    $("photo-panel").classList.toggle("hidden", mode !== "photo");
    if (mode === "ride") retake();
  }

  // ---------- init ----------
  function init() {
    $("tab-ride").addEventListener("click", () => setMode("ride"));
    $("tab-photo").addEventListener("click", () => { if (!state.recording) setMode("photo"); });
    $("btn-start").addEventListener("click", startRide);
    $("btn-stop").addEventListener("click", stopRide);
    $("btn-snap").addEventListener("click", () => (state.photoBlob ? retake() : snapPhoto()));
    $("btn-send-photo").addEventListener("click", sendPhoto);
    $("btn-send-file").addEventListener("click", sendFile);
    $("bumps").addEventListener("change", (e) => toggleBumps(e.target.checked));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.recording) keepAwake(true);
    });
    if (checkSecure()) {
      startCamera();
      startGps();
    } else if ("geolocation" in navigator) {
      startGps(); // fails silently on insecure origins
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
