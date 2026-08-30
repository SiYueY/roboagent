() => {
  const workspace = document.getElementById("workspace");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const mobileSidebarClose = document.getElementById("mobile-sidebar-close");
  const sessionList = document.getElementById("session-list");
  const voiceCallButton = document.getElementById("voice-call-button");
  const voiceCaption = document.getElementById("voice-caption");
  const voiceStatus = document.getElementById("voice-status");
  const mainArea = document.getElementById("main-area");
  const cameraSwitchButton = document.getElementById("camera-switch-button");
  if (!workspace || !sidebarToggle || workspace.dataset.roboagentChatReady) return;

  workspace.dataset.roboagentChatReady = "true";
  const compact = window.matchMedia("(max-width: 760px)");
  const controls = {
    microphone: document.getElementById("voice-microphone"),
    video: document.getElementById("voice-video"),
    captions: document.getElementById("voice-captions"),
    speaker: document.getElementById("voice-speaker"),
    hangup: document.getElementById("voice-hangup"),
  };
  const toggleableControls = ["microphone", "video", "captions", "speaker"];
  const state = {
    active: false,
    microphoneEnabled: false,
    videoEnabled: false,
    captionsEnabled: true,
    speakerEnabled: true,
    microphoneStream: null,
    cameraStream: null,
    cameraFacingMode: "user",
    audioContext: null,
    gain: null,
    oscillator: null,
    tonePlayed: false,
  };

  const createCameraLayer = () => {
    if (!mainArea) return {};
    let layer = document.getElementById("camera-layer");
    let video = document.getElementById("camera-video");
    let overlay = document.getElementById("camera-overlay");
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "camera-layer";
      mainArea.prepend(layer);
    }
    if (!video) {
      video = document.createElement("video");
      video.id = "camera-video";
      video.autoplay = true;
      video.muted = true;
      video.playsInline = true;
      layer.append(video);
    }
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "camera-overlay";
      layer.append(overlay);
    }
    return { layer, video, overlay };
  };
  const { video: cameraVideo } = createCameraLayer();

  const setStatus = (message) => {
    const text = voiceStatus?.querySelector("p") || voiceStatus;
    if (text) text.textContent = message;
  };
  const setControlState = (name, active) => {
    const control = controls[name];
    if (!control) return;
    control.classList.toggle("is-active", active);
    const button = control.querySelector("button") || control;
    button.setAttribute("aria-pressed", String(active));
  };
  const syncSidebarLabel = () => {
    const collapsed = workspace.classList.contains("sidebar-collapsed");
    const label = collapsed ? "展开侧边栏" : "隐藏侧边栏";
    const button = sidebarToggle.querySelector("button") || sidebarToggle;
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    const closeButton = mobileSidebarClose?.querySelector("button") || mobileSidebarClose;
    closeButton?.setAttribute("aria-label", "隐藏侧边栏");
    closeButton?.setAttribute("title", "隐藏侧边栏");
  };
  const collapseSidebarForCompactViewport = () => {
    if (compact.matches) workspace.classList.add("sidebar-collapsed");
    syncSidebarLabel();
  };
  const syncVoiceUi = () => {
    workspace.classList.toggle("voice-mode", state.active);
    const showCaption = state.active && state.microphoneEnabled && state.captionsEnabled;
    voiceCaption?.classList.toggle("is-visible", showCaption);
    for (const name of toggleableControls) {
      setControlState(name, state[`${name}Enabled`]);
    }
    workspace.classList.toggle("camera-enabled", state.active && state.videoEnabled);
    cameraVideo?.classList.toggle("is-mirrored", state.cameraFacingMode === "user");
    collapseSidebarForCompactViewport();
  };

  const stopTone = () => {
    state.oscillator?.stop();
    state.oscillator = null;
  };
  const stopAudio = () => {
    stopTone();
    state.audioContext?.close();
    state.audioContext = null;
    state.gain = null;
    state.tonePlayed = false;
  };
  const playTone = async () => {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    if (!state.audioContext || state.audioContext.state === "closed") {
      state.audioContext = new AudioContext();
      state.gain = state.audioContext.createGain();
      state.gain.connect(state.audioContext.destination);
    }
    await state.audioContext.resume();
    stopTone();
    const now = state.audioContext.currentTime;
    const oscillator = state.audioContext.createOscillator();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(660, now);
    state.gain.gain.setValueAtTime(state.speakerEnabled ? 0.07 : 0, now);
    state.gain.gain.exponentialRampToValueAtTime(0.001, now + 0.24);
    oscillator.connect(state.gain);
    oscillator.start(now);
    oscillator.stop(now + 0.25);
    oscillator.addEventListener("ended", () => {
      if (state.oscillator === oscillator) state.oscillator = null;
    });
    state.oscillator = oscillator;
    state.tonePlayed = true;
  };
  const releaseStream = (name) => {
    state[name]?.getTracks().forEach((track) => track.stop());
    state[name] = null;
  };
  const releaseMicrophone = () => {
    releaseStream("microphoneStream");
    state.microphoneEnabled = false;
  };
  const releaseCamera = () => {
    releaseStream("cameraStream");
    state.videoEnabled = false;
    if (cameraVideo) cameraVideo.srcObject = null;
  };
  const startCamera = async (facingMode, { reportError = true } = {}) => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: facingMode },
          width: { ideal: 1280 },
          height: { ideal: 720 },
          frameRate: { ideal: 24, max: 30 },
        },
      });
      state.cameraStream = stream;
      state.cameraFacingMode = facingMode;
      state.videoEnabled = true;
      if (cameraVideo) {
        cameraVideo.srcObject = stream;
        await cameraVideo.play();
      }
      setStatus("正在预览视频…");
      return true;
    } catch (error) {
      console.warn("RoboAgent camera access failed", error);
      releaseCamera();
      if (reportError) setStatus("无法访问摄像头，请允许浏览器访问摄像头");
      return false;
    }
  };
  const enterVoiceMode = () => {
    state.active = true;
    setStatus("点击麦克风开始");
    syncVoiceUi();
  };
  const exitVoiceMode = () => {
    releaseMicrophone();
    releaseCamera();
    stopAudio();
    Object.assign(state, {
      active: false,
      captionsEnabled: true,
      speakerEnabled: true,
      cameraFacingMode: "user",
    });
    setStatus("点击麦克风开始");
    syncVoiceUi();
  };
  const toggleMicrophone = async () => {
    if (!state.active) return;
    if (state.microphoneStream) {
      state.microphoneEnabled = !state.microphoneEnabled;
      state.microphoneStream.getAudioTracks().forEach((track) => { track.enabled = state.microphoneEnabled; });
      setStatus(state.microphoneEnabled ? "正在聆听…" : "麦克风已关闭");
      syncVoiceUi();
      return;
    }
    try {
      state.microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.microphoneEnabled = true;
      setStatus("正在聆听…");
    } catch (error) {
      console.warn("RoboAgent microphone access failed", error);
      setStatus("无法访问麦克风，请允许浏览器访问麦克风");
    }
    syncVoiceUi();
  };
  const toggleCamera = async () => {
    if (!state.active) return;
    if (state.videoEnabled) {
      releaseCamera();
      setStatus("视频已关闭");
    } else {
      await startCamera(state.cameraFacingMode);
    }
    syncVoiceUi();
  };
  const switchCamera = async () => {
    if (!state.active || !state.videoEnabled) return;
    const previousFacingMode = state.cameraFacingMode;
    const nextFacingMode = previousFacingMode === "user" ? "environment" : "user";
    releaseCamera();
    const switched = await startCamera(nextFacingMode, { reportError: false });
    if (!switched) {
      const restored = await startCamera(previousFacingMode, { reportError: false });
      if (!restored) setStatus("无法切换摄像头，请检查浏览器权限");
    }
    syncVoiceUi();
  };
  const toggleCaptions = () => {
    state.captionsEnabled = !state.captionsEnabled;
    syncVoiceUi();
  };
  const toggleSpeaker = async () => {
    if (!state.tonePlayed && state.speakerEnabled) {
      setStatus("正在播放…");
      await playTone();
      syncVoiceUi();
      return;
    }
    state.speakerEnabled = !state.speakerEnabled;
    if (state.gain && state.audioContext) {
      state.gain.gain.setValueAtTime(state.speakerEnabled ? 0.07 : 0, state.audioContext.currentTime);
    }
    if (state.speakerEnabled) {
      setStatus("正在播放…");
      await playTone();
    } else {
      setStatus("扬声器已关闭");
      stopTone();
    }
    syncVoiceUi();
  };

  collapseSidebarForCompactViewport();
  compact.addEventListener("change", collapseSidebarForCompactViewport);
  sidebarToggle.addEventListener("click", () => {
    if (state.active && compact.matches) return;
    workspace.classList.toggle("sidebar-collapsed");
    syncSidebarLabel();
  });
  mobileSidebarClose?.addEventListener("click", () => {
    workspace.classList.add("sidebar-collapsed");
    syncSidebarLabel();
  });
  sessionList?.addEventListener("change", collapseSidebarForCompactViewport);
  voiceCallButton?.addEventListener("click", enterVoiceMode);
  Object.entries({
    microphone: toggleMicrophone,
    video: toggleCamera,
    captions: toggleCaptions,
    speaker: toggleSpeaker,
  }).forEach(([name, handler]) => controls[name]?.addEventListener("click", handler));
  controls.hangup?.addEventListener("click", exitVoiceMode);
  cameraSwitchButton?.addEventListener("click", switchCamera);
  window.addEventListener("pagehide", () => {
    releaseMicrophone();
    releaseCamera();
    stopAudio();
  });
}
