() => {
  const workspace = document.getElementById("workspace");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const sessionList = document.getElementById("session-list");
  const voiceCallButton = document.getElementById("voice-call-button");
  const voiceCaption = document.getElementById("voice-caption");
  const voiceStatus = document.getElementById("voice-status");
  if (!workspace || !sidebarToggle || workspace.dataset.roboagentChatReady) return;

  workspace.dataset.roboagentChatReady = "true";
  const compact = window.matchMedia("(max-width: 760px)");
  const controls = {
    microphone: document.getElementById("voice-microphone"),
    captions: document.getElementById("voice-captions"),
    speaker: document.getElementById("voice-speaker"),
    hangup: document.getElementById("voice-hangup"),
  };
  const state = {
    active: false,
    microphoneEnabled: false,
    captionsEnabled: true,
    speakerEnabled: true,
    stream: null,
    audioContext: null,
    gain: null,
    oscillator: null,
    tonePlayed: false,
  };

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
  const syncToggleIcon = () => {
    const collapsed = workspace.classList.contains("sidebar-collapsed");
    const label = collapsed ? "展开侧边栏" : "隐藏侧边栏";
    const button = sidebarToggle.querySelector("button") || sidebarToggle;
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
  };
  const syncSidebarForViewport = () => {
    workspace.classList.toggle("sidebar-collapsed", compact.matches);
    syncToggleIcon();
  };
  const syncVoiceUi = () => {
    workspace.classList.toggle("voice-mode", state.active);
    const showCaption = state.active && state.microphoneEnabled && state.captionsEnabled;
    voiceCaption?.classList.toggle("is-visible", showCaption);
    setControlState("microphone", state.microphoneEnabled);
    setControlState("captions", state.captionsEnabled);
    setControlState("speaker", state.speakerEnabled);
    syncSidebarForViewport();
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
  const releaseMicrophone = () => {
    state.stream?.getTracks().forEach((track) => track.stop());
    state.stream = null;
    state.microphoneEnabled = false;
  };
  const enterVoiceMode = () => {
    state.active = true;
    setStatus("点击麦克风开始");
    syncVoiceUi();
  };
  const exitVoiceMode = () => {
    releaseMicrophone();
    stopAudio();
    state.active = false;
    state.captionsEnabled = true;
    state.speakerEnabled = true;
    setStatus("点击麦克风开始");
    syncVoiceUi();
  };
  const toggleMicrophone = async () => {
    if (!state.active) return;
    if (state.stream) {
      state.microphoneEnabled = !state.microphoneEnabled;
      state.stream.getAudioTracks().forEach((track) => { track.enabled = state.microphoneEnabled; });
      setStatus(state.microphoneEnabled ? "正在聆听…" : "麦克风已关闭");
      syncVoiceUi();
      return;
    }
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.microphoneEnabled = true;
      setStatus("正在聆听…");
    } catch (error) {
      console.warn("RoboAgent microphone access failed", error);
      setStatus("无法访问麦克风，请允许浏览器访问麦克风");
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

  syncSidebarForViewport();
  compact.addEventListener("change", syncSidebarForViewport);
  sidebarToggle.addEventListener("click", () => {
    if (state.active && compact.matches) return;
    workspace.classList.toggle("sidebar-collapsed");
    syncToggleIcon();
  });
  sessionList?.addEventListener("change", () => {
    if (compact.matches) {
      workspace.classList.add("sidebar-collapsed");
      syncToggleIcon();
    }
  });
  voiceCallButton?.addEventListener("click", enterVoiceMode);
  controls.microphone?.addEventListener("click", toggleMicrophone);
  controls.captions?.addEventListener("click", toggleCaptions);
  controls.speaker?.addEventListener("click", toggleSpeaker);
  controls.hangup?.addEventListener("click", exitVoiceMode);
  window.addEventListener("pagehide", () => {
    releaseMicrophone();
    stopAudio();
  });
}
