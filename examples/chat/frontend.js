() => {
  const COMPACT_VIEWPORT = "(max-width: 760px)";
  const CONTROL_NAMES = ["microphone", "video", "captions", "speaker"];
  const INITIALIZATION_TIMEOUT_MS = 10_000;
  // DashScope's PCM keeps considerable headroom even at provider volume 100.
  // This gain only applies to synthesized audio, never microphone capture.
  const TTS_OUTPUT_GAIN = 1.8;
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
  // Keep microphone capture and the chat UI in one versioned browser source.
  // AudioWorklet requires a module URL, so create one locally instead of
  // exposing a second static JavaScript endpoint.
  const AUDIO_WORKLET_SOURCE = String.raw`
class RoboAgentPCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSamples = 320;
    this.sourcePerOutput = sampleRate / 16000;
    this.nextOutputAt = 0;
    this.inputIndex = 0;
    this.taps = 63;
    this.history = new Float32Array(this.taps);
    this.historyIndex = 0;
    this.pcm = new Int16Array(this.targetSamples);
    this.pcmIndex = 0;
    this.levelSquared = 0;
    this.levelSamples = 0;
    this.coefficients = this.makeLowPass(this.taps, Math.min(0.45, 7200 / sampleRate));
  }

  makeLowPass(length, cutoff) {
    const values = new Float32Array(length);
    const midpoint = (length - 1) / 2;
    let sum = 0;
    for (let index = 0; index < length; index += 1) {
      const distance = index - midpoint;
      const sinc = distance === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * distance) / (Math.PI * distance);
      const window = 0.54 - 0.46 * Math.cos((2 * Math.PI * index) / (length - 1));
      values[index] = sinc * window;
      sum += values[index];
    }
    for (let index = 0; index < length; index += 1) values[index] /= sum;
    return values;
  }

  pushSample(sample) {
    this.history[this.historyIndex] = sample;
    this.historyIndex = (this.historyIndex + 1) % this.taps;
    this.levelSquared += sample * sample;
    this.levelSamples += 1;
    if (this.levelSamples >= sampleRate / 5) {
      this.port.postMessage({ type: "level", value: Math.sqrt(this.levelSquared / this.levelSamples) });
      this.levelSquared = 0;
      this.levelSamples = 0;
    }
  }

  filteredSample() {
    let output = 0;
    let index = (this.historyIndex - 1 + this.taps) % this.taps;
    for (let tap = 0; tap < this.taps; tap += 1) {
      output += this.history[index] * this.coefficients[tap];
      index = (index - 1 + this.taps) % this.taps;
    }
    return Math.max(-1, Math.min(1, output));
  }

  emitSample(sample) {
    this.pcm[this.pcmIndex++] = sample < 0 ? Math.max(-32768, sample * 32768) : Math.min(32767, sample * 32767);
    if (this.pcmIndex === this.targetSamples) {
      const frame = this.pcm.buffer.slice(0);
      this.port.postMessage(frame, [frame]);
      this.pcmIndex = 0;
    }
  }

  process(inputs) {
    const source = inputs[0]?.[0];
    if (!source) return true;
    for (let index = 0; index < source.length; index += 1, this.inputIndex += 1) {
      this.pushSample(source[index]);
      while (this.nextOutputAt <= this.inputIndex) {
        this.emitSample(this.filteredSample());
        this.nextOutputAt += this.sourcePerOutput;
      }
    }
    return true;
  }
}

registerProcessor("roboagent-pcm", RoboAgentPCMProcessor);
`;
  let audioWorkletUrl = null;

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
        speechSocket: null,
        workletNode: null,
        workletContext: null,
        microphoneSink: null,
        pendingAudio: [],
        playbackTime: 0,
        responseCaption: "",
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
      let cameraVideo = null;

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
        cameraVideo?.classList.toggle("is-mirrored", state.cameraFacingMode === "user");
        syncCompactSidebar();
      };

      // Media lifecycle ----------------------------------------------------
      const releaseStream = (key) => {
        state[key]?.getTracks().forEach((track) => track.stop());
        state[key] = null;
      };
      const stopAudio = () => {
        state.audioContext?.close();
        state.audioContext = null;
        state.gain = null;
        state.workletContext = null;
      };
      const ensureAudioOutput = async () => {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) throw new Error("此浏览器不支持 Web Audio");
        if (!state.audioContext || state.audioContext.state === "closed") {
          state.audioContext = new AudioContext();
        }
        if (!state.gain) {
          state.gain = state.audioContext.createGain();
          state.gain.gain.value = state.speakerEnabled ? TTS_OUTPUT_GAIN : 0;
          state.gain.connect(state.audioContext.destination);
        }
        await state.audioContext.resume();
        return state.audioContext;
      };
      const loadAudioWorklet = async (audioContext) => {
        if (state.workletContext === audioContext) return;
        audioWorkletUrl ??= URL.createObjectURL(
          new Blob([AUDIO_WORKLET_SOURCE], { type: "application/javascript" }),
        );
        await audioContext.audioWorklet.addModule(audioWorkletUrl);
        state.workletContext = audioContext;
      };
      const releaseMicrophone = () => {
        releaseStream("microphoneStream");
        state.workletNode?.disconnect();
        state.microphoneSink?.disconnect();
        state.workletNode = null;
        state.microphoneSink = null;
        state.pendingAudio = [];
        state.microphoneEnabled = false;
      };
      const releaseCamera = () => {
        releaseStream("cameraStream");
        state.videoEnabled = false;
        if (cameraVideo) {
          cameraVideo.pause();
          cameraVideo.srcObject = null;
        }
      };
      const waitForCameraRelease = () => new Promise((resolve) => {
        window.setTimeout(resolve, CAMERA_RELEASE_DELAY_MS);
      });
      const waitForCameraMetadata = () => new Promise((resolve, reject) => {
        if (!cameraVideo) { reject(new Error("camera layer is unavailable")); return; }
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
      const playPcm = async (buffer) => {
        if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 2 || buffer.byteLength % 2) {
          throw new Error("服务端返回了无效的 PCM 音频帧");
        }
        const context = await ensureAudioOutput();
        const samples = new Int16Array(buffer);
        const audio = context.createBuffer(1, samples.length, 24000);
        const channel = audio.getChannelData(0);
        for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768;
        const source = context.createBufferSource();
        source.buffer = audio; source.connect(state.gain || state.audioContext.destination);
        const start = Math.max(context.currentTime, state.playbackTime);
        source.start(start); state.playbackTime = start + audio.duration;
      };
      const startCamera = async (facingMode, { reportError = true } = {}) => {
        try {
          cameraVideo ??= createCameraLayer();
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
        const token = byId("session-list")?.querySelector("input:checked")?.value
          || byId("voice-token")?.querySelector("input, textarea")?.value;
        if (token) {
          const protocol = location.protocol === "https:" ? "wss" : "ws";
          state.speechSocket = new WebSocket(`${protocol}://${location.host}/speech?token=${encodeURIComponent(token)}`);
          state.speechSocket.binaryType = "arraybuffer";
          state.speechSocket.onopen = () => {
            state.pendingAudio.splice(0).forEach((audio) => state.speechSocket.send(audio));
            setStatus("语音服务已连接，点击麦克风开始");
          };
          state.speechSocket.onerror = () => setStatus("无法连接语音服务，请检查 HTTPS 和服务日志");
          state.speechSocket.onclose = (event) => {
            if (state.active && event.code !== 1000) setStatus(`语音服务已断开（${event.code}）`);
          };
          state.speechSocket.onmessage = (event) => {
            if (typeof event.data === "string") {
              const payload = JSON.parse(event.data);
              if (payload.type === "session.ready") setStatus("语音服务已就绪，点击麦克风开始");
              if (payload.type === "speech.started") {
                state.responseCaption = "";
                byId("voice-caption").textContent = "";
                setStatus("正在识别…");
              }
              if (payload.type === "speech.stopped") setStatus("正在处理语音…");
              if (payload.type === "response.started") {
                state.responseCaption = "";
                setStatus("正在思考…");
              }
              if (payload.type === "response.completed" && !state.playbackTime) {
                setStatus(state.microphoneEnabled ? "正在聆听…" : "回答完成");
              }
              if (payload.type === "audio.started") setStatus("正在播放…");
              if (payload.type === "audio.completed") {
                setStatus(state.microphoneEnabled ? "正在聆听…" : "语音播放完成");
              }
              if (payload.type === "transcript.partial" || payload.type === "transcript.final") byId("voice-caption").textContent = payload.text;
              if (payload.type === "response.delta") {
                state.responseCaption += payload.delta || "";
                byId("voice-caption").textContent = state.responseCaption;
              }
              if (payload.type === "playback.clear") state.playbackTime = 0;
              if (payload.type === "speech.diagnostics") {
                const status = byId("voice-status");
                if (status?.dataset.debug === "true") {
                  status.textContent = `输入 ${Math.round(payload.level * 100)}% · ${payload.vad_state} · 置信度 ${Math.round((payload.confidence || 0) * 100)}% · 丢帧 ${payload.dropped_frames}`;
                }
              }
              if (payload.type === "error") setStatus(payload.error);
              return;
            }
            playPcm(event.data).catch((error) => {
              console.warn("RoboAgent PCM playback failed", error);
              setStatus(`音频播放失败：${error.message}`);
            });
          };
          setStatus("正在连接语音服务…");
        } else {
          setStatus("未找到当前对话的语音会话，请刷新页面");
        }
        syncVoiceUi();
      };
      const exitVoiceMode = () => {
        releaseMicrophone();
        releaseCamera();
        stopAudio();
        state.speechSocket?.close(); state.speechSocket = null;
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
            state.microphoneStream = await navigator.mediaDevices.getUserMedia({
              audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
            });
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!state.audioContext || state.audioContext.state === "closed") state.audioContext = new AudioContext();
            // Resume from this button's user gesture. Some browsers reject a
            // later resume() initiated by a WebSocket audio callback.
            await ensureAudioOutput();
            await loadAudioWorklet(state.audioContext);
            state.workletNode = new AudioWorkletNode(state.audioContext, "roboagent-pcm");
            state.workletNode.port.onmessage = ({ data }) => {
              if (!(data instanceof ArrayBuffer)) {
                const status = byId("voice-status");
                if (data?.type === "level" && state.microphoneEnabled && status?.textContent.startsWith("正在聆听")) {
                  status.textContent = `正在聆听… 输入 ${Math.round(data.value * 100)}%`;
                }
                return;
              }
              if (!state.microphoneEnabled) return;
              if (state.speechSocket?.readyState === WebSocket.OPEN) state.speechSocket.send(data);
              else if (state.pendingAudio.length < 50) state.pendingAudio.push(data);
            };
            state.microphoneEnabled = true;
            state.microphoneSink = state.audioContext.createGain();
            state.microphoneSink.gain.value = 0;
            state.audioContext.createMediaStreamSource(state.microphoneStream)
              .connect(state.workletNode).connect(state.microphoneSink).connect(state.audioContext.destination);
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
      const toggleSpeaker = () => {
        state.speakerEnabled = !state.speakerEnabled;
        if (state.gain && state.audioContext) {
          state.gain.gain.setValueAtTime(
            state.speakerEnabled ? TTS_OUTPUT_GAIN : 0,
            state.audioContext.currentTime,
          );
        }
        setStatus(state.speakerEnabled ? "扬声器已开启" : "扬声器已关闭");
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
        state.speechSocket?.close();
        state.speechSocket = null;
        if (audioWorkletUrl) {
          URL.revokeObjectURL(audioWorkletUrl);
          audioWorkletUrl = null;
        }
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
