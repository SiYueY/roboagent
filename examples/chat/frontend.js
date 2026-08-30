() => {
  const COMPACT_VIEWPORT = "(max-width: 760px)";
  const CONTROL_NAMES = ["microphone", "video", "captions", "speaker"];
  const INITIALIZATION_TIMEOUT_MS = 10_000;
  const CAMERA_RELEASE_DELAY_MS = 150;
  const CAMERA_METADATA_TIMEOUT_MS = 3_000;
  const CAMERA_PROFILES = {
    user: {
      width: { ideal: 1280 },
      height: { ideal: 720 },
      frameRate: { ideal: 24, max: 30 },
    },
    environment: {
      width: { ideal: 960 },
      height: { ideal: 540 },
      frameRate: { ideal: 20, max: 24 },
    },
  };

  const initialize = () => {
    const root = document.querySelector("gradio-app")?.shadowRoot ?? document;
    const byId = (id) => root.querySelector(`#${id}`);
    const workspace = byId("workspace");
    const mainArea = byId("main-area");
    if (!workspace || !mainArea || !byId("sidebar-toggle")) return false;
    if (workspace.dataset.roboagentChatReady && window.roboagentChat) return true;

    try {
      const compactViewport = window.matchMedia(COMPACT_VIEWPORT);
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

      // DOM utilities ------------------------------------------------------
      const buttonFor = (element) => element?.querySelector("button") ?? element;
      const setAccessibleLabel = (element, label) => {
        const button = buttonFor(element);
        button?.setAttribute("aria-label", label);
        button?.setAttribute("title", label);
      };
      const setStatus = (message) => {
        const status = byId("voice-status");
        const text = status?.querySelector("p") ?? status;
        if (text) text.textContent = message;
      };
      const setControlState = (name, active) => {
        const control = byId(`voice-${name}`);
        if (!control) return;
        control.classList.toggle("is-active", active);
        buttonFor(control)?.setAttribute("aria-pressed", String(active));
      };
      const createCameraLayer = () => {
        let layer = byId("camera-layer");
        let video = byId("camera-video");
        let overlay = byId("camera-overlay");

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
        return video;
      };
      const cameraVideo = createCameraLayer();

      // UI synchronization -------------------------------------------------
      const syncSidebarLabel = () => {
        const collapsed = workspace.classList.contains("sidebar-collapsed");
        setAccessibleLabel(byId("sidebar-toggle"), collapsed ? "展开侧边栏" : "隐藏侧边栏");
        setAccessibleLabel(byId("mobile-sidebar-close"), "隐藏侧边栏");
      };
      const syncCompactSidebar = () => {
        if (compactViewport.matches) workspace.classList.add("sidebar-collapsed");
        syncSidebarLabel();
      };
      const syncVoiceUi = () => {
        workspace.classList.toggle("voice-mode", state.active);
        workspace.classList.toggle("camera-enabled", state.active && state.videoEnabled);
        byId("voice-caption")?.classList.toggle(
          "is-visible",
          state.active && state.microphoneEnabled && state.captionsEnabled,
        );
        CONTROL_NAMES.forEach((name) => setControlState(name, state[`${name}Enabled`]));
        cameraVideo.classList.toggle("is-mirrored", state.cameraFacingMode === "user");
        syncCompactSidebar();
      };

      // Media lifecycle ----------------------------------------------------
      const releaseStream = (key) => {
        state[key]?.getTracks().forEach((track) => track.stop());
        state[key] = null;
      };
      const stopTone = () => {
        if (state.oscillator) {
          try {
            state.oscillator.stop();
          } catch {
            // A naturally ended oscillator cannot be stopped again.
          }
        }
        state.oscillator = null;
      };
      const stopAudio = () => {
        stopTone();
        state.audioContext?.close();
        state.audioContext = null;
        state.gain = null;
        state.tonePlayed = false;
      };
      const releaseMicrophone = () => {
        releaseStream("microphoneStream");
        state.microphoneEnabled = false;
      };
      const releaseCamera = () => {
        releaseStream("cameraStream");
        state.videoEnabled = false;
        cameraVideo.pause();
        cameraVideo.srcObject = null;
      };
      const waitForCameraRelease = () => new Promise((resolve) => {
        window.setTimeout(resolve, CAMERA_RELEASE_DELAY_MS);
      });
      const waitForCameraMetadata = () => new Promise((resolve, reject) => {
        if (cameraVideo.readyState >= HTMLMediaElement.HAVE_METADATA) {
          resolve();
          return;
        }
        let timeout;
        const complete = (callback) => () => {
          window.clearTimeout(timeout);
          cameraVideo.removeEventListener("loadedmetadata", onLoadedMetadata);
          cameraVideo.removeEventListener("error", onError);
          callback();
        };
        const onLoadedMetadata = complete(resolve);
        const onError = complete(() => reject(new Error("camera metadata failed to load")));
        cameraVideo.addEventListener("loadedmetadata", onLoadedMetadata, { once: true });
        cameraVideo.addEventListener("error", onError, { once: true });
        timeout = window.setTimeout(
          complete(() => reject(new Error("camera metadata timed out"))),
          CAMERA_METADATA_TIMEOUT_MS,
        );
      });
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
      const startCamera = async (facingMode, { reportError = true } = {}) => {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: {
              facingMode: { ideal: facingMode },
              ...CAMERA_PROFILES[facingMode],
            },
          });
          state.cameraStream = stream;
          state.cameraFacingMode = facingMode;
          state.videoEnabled = true;
          cameraVideo.srcObject = stream;
          await waitForCameraMetadata();
          await cameraVideo.play();
          setStatus("正在预览视频…");
          return true;
        } catch (error) {
          console.warn("RoboAgent camera access failed", error);
          releaseCamera();
          if (reportError) setStatus("无法访问摄像头，请允许浏览器访问摄像头");
          return false;
        }
      };

      // User actions -------------------------------------------------------
      const toggleSidebar = () => {
        if (state.active && compactViewport.matches) return;
        workspace.classList.toggle("sidebar-collapsed");
        syncSidebarLabel();
      };
      const closeMobileSidebar = () => {
        workspace.classList.add("sidebar-collapsed");
        syncSidebarLabel();
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
          state.microphoneStream.getAudioTracks().forEach((track) => {
            track.enabled = state.microphoneEnabled;
          });
          setStatus(state.microphoneEnabled ? "正在聆听…" : "麦克风已关闭");
        } else {
          try {
            state.microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            state.microphoneEnabled = true;
            setStatus("正在聆听…");
          } catch (error) {
            console.warn("RoboAgent microphone access failed", error);
            setStatus("无法访问麦克风，请允许浏览器访问麦克风");
          }
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
        setStatus("正在切换摄像头…");
        releaseCamera();
        await waitForCameraRelease();
        const switched = await startCamera(nextFacingMode, { reportError: false });
        if (!switched) {
          await waitForCameraRelease();
          if (!(await startCamera(previousFacingMode, { reportError: false }))) {
            setStatus("无法切换摄像头，请检查浏览器权限");
          }
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
        } else {
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
        }
        syncVoiceUi();
      };

      // Public API and cleanup --------------------------------------------
      const api = {
        toggleSidebar,
        closeMobileSidebar,
        enterVoiceMode,
        exitVoiceMode,
        toggleMicrophone,
        toggleCamera,
        toggleCaptions,
        toggleSpeaker,
        switchCamera,
      };
      const cleanup = () => {
        compactViewport.removeEventListener("change", syncCompactSidebar);
        releaseMicrophone();
        releaseCamera();
        stopAudio();
        if (window.roboagentChat === api) delete window.roboagentChat;
      };

      compactViewport.addEventListener("change", syncCompactSidebar);
      window.addEventListener("pagehide", cleanup, { once: true });
      window.roboagentChat = api;
      workspace.dataset.roboagentChatReady = "true";
      delete window.roboagentChatInitError;
      syncCompactSidebar();
      return true;
    } catch (error) {
      delete workspace.dataset.roboagentChatReady;
      window.roboagentChatInitError = error instanceof Error ? error.stack : String(error);
      console.error("RoboAgent chat frontend initialization failed", error);
      return false;
    }
  };

  if (initialize()) return;
  const observer = new MutationObserver(() => {
    if (initialize()) observer.disconnect();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.setTimeout(() => observer.disconnect(), INITIALIZATION_TIMEOUT_MS);
}
