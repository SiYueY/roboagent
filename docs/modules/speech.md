# RoboAgent 实时语音系统优化与重构设计

## 1. 目标与设计原则

`roboagent/speech` 应定位为 RoboAgent 的统一实时语音运行时，而不是仅服务于当前 Gradio/Browser Chat 示例。

它需要同时支持：

```text
Browser microphone / speaker
USB microphone / speaker
ALSA / PipeWire
Robot SDK audio
Microphone array / vendor DSP
```

核心链路保持统一：

```text
Audio Input
   ↓
Audio Processor
   ↓
VAD / Turn Handling
   ↓
Streaming ASR
   ↓
AgentSession
   ↓
Streaming TTS
   ↓
Audio Output
```

设计原则如下：

* RoboAgent 保持 Python-first；
* `SpeechSession` 继续作为实时语音 orchestration 核心；
* 不引入 LiveKit/Pipecat 作为核心 runtime 依赖；
* `pywebrtc-audio` 作为可选 DSP 实现；
* Browser 和 Robot 只在音频 I/O、Transport、AudioProcessor 上存在差异；
* VAD、Turn、ASR、Agent、TTS 全部共用；
* 不引入复杂 Frame Graph、EventBus、Manager 层级或 arbitrary processor pipeline；
* 优先渐进式重构，避免推倒当前已经可用的实现。

参考项目的定位明确为：

```text
pywebrtc-audio
→ AEC3 / NS / AGC2 / speech probability

LiveKit Agents
→ Turn Detection / Endpointing / Interruption / False interruption

Pipecat
→ Realtime processing / queue / cancellation / metrics
```

---

## 2. 当前实现与主要问题

当前 RoboAgent 已经具备完整的基础 Voice Agent 链路：

```text
Browser Microphone
        ↓
AudioWorklet
16 kHz / mono / PCM16
        ↓
WebSocket
        ↓
SpeechSession
        ↓
AudioFilter
        ↓
VAD
        ↓
TurnDetector
        ↓
Streaming ASR
        ↓
AgentSession
        ↓
Streaming TTS
        ↓
WebSocket
        ↓
Browser Playback
```

已有能力包括：

* Streaming PCM 输入；
* AudioWorklet 重采样与 PCM 分帧；
* RNNoise；
* Silero VAD；
* 300 ms pre-roll；
* Turn Detection；
* Streaming ASR；
* Streaming LLM；
* Streaming TTS；
* bounded input queue；
* drop-oldest 背压；
* barge-in；
* Agent/TTS cancellation；
* SpeechTransport；
* diagnostics；
* Browser playback scheduling。

因此本次不是重新实现 Voice Agent，而是修正几个已经开始限制后续发展的边界。

当前最主要有四个问题。

### 2.1 `AudioFilter` 只能表达单路输入

当前接口本质是：

```text
capture PCM
   ↓
filter
   ↓
processed PCM
```

这适用于：

```text
Passthrough
RNNoise
```

但无法自然表达 AEC：

```text
microphone / near
       +
speaker / far
       ↓
      AEC3
```

因此 `AudioFilter` 已经不足以继续承载机器人本体的全双工音频处理。

### 2.2 Turn 和 Interruption 耦合在 `SpeechSession`

当前 `TurnDetector` 主要通过：

```text
silence_ms
max_duration
idle_timeout
min_speech
```

做 endpointing。

同时 `SpeechSession` 自己又处理：

```text
barge_in_ms
barge_in_confidence
barge_in_min_volume
```

这实际上把两个不同的问题混在了一起：

```text
用户什么时候说完？
AI 正在讲话时，用户是否真的想打断？
```

后者应该成为独立 interruption policy。

### 2.3 当前设计仍偏 Browser

目前实际音频来源是：

```text
Browser
→ WebSocket
→ SpeechSession
```

但后续机器人本体可能是：

```text
ALSA / Robot SDK / microphone array
→ SpeechSession
```

如果不提前定义设备和 transport 边界，后面很容易出现：

```text
BrowserSpeechSession
RobotSpeechSession
```

两套逻辑。

### 2.4 Metrics 不足

当前主要监控：

```text
VAD level
confidence
filter latency
dropped frames
```

但无法回答最重要的问题：

```text
用户说完后多久 AI 开口？
ASR 慢还是 LLM 慢？
TTS 首包多久？
打断后多久真正停止声音？
```

所以需要正式增加 turn-level realtime metrics。

---

## 3. 目标架构

重构后的统一架构：

```text
                         AgentSession
                              ▲
                              │
                        SpeechSession
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
     Transport           Turn Handling           Metrics
        │                     │
        ▼               ┌─────┴─────┐
 AudioProcessor          │           │
        │                ▼           ▼
        │               VAD     Interruption
        ▼                │
    Clean PCM            ▼
        │           TurnDetector
        │                │
        └──────────────► ASR
                          │
                          ▼
                     AgentSession
                          │
                          ▼
                         TTS
                          │
                          ▼
                   AudioProcessor
                   render processing
                          │
                          ▼
                       Transport
```

其中四个边界必须保持清晰。

### 3.1 `SpeechSession`

负责：

```text
lifecycle
async task coordination
queue/backpressure
ASR lifecycle
Agent lifecycle
TTS lifecycle
event propagation
cancellation orchestration
metrics timing
```

不负责：

```text
AEC 算法
Interruption 算法
ALSA
WebSocket
设备管理
```

### 3.2 `SpeechTransport`

继续表示：

> 音频和事件如何进入/离开当前 SpeechSession。

接口保持：

```python
class SpeechTransport(Protocol):
    async def receive_audio(self): ...
    async def send_audio(self, audio): ...
    async def send_event(self, event): ...
    async def clear_output(self): ...
    async def close(self): ...
```

不同场景：

```text
Browser
→ WebSocketSpeechTransport

Robot
→ LocalSpeechTransport

Future
→ LiveKitSpeechTransport / WebRTCSpeechTransport
```

### 3.3 `AudioDevice`

仅用于本地/native 音频场景。

新增：

```text
speech/device/
├── base.py
└── alsa.py
```

定义：

```python
class AudioInput(Protocol):
    async def start(self): ...
    async def read(self) -> AudioChunk: ...
    async def close(self): ...


class AudioOutput(Protocol):
    async def start(self): ...
    async def write(self, audio: AudioChunk): ...
    async def clear(self): ...
    async def close(self): ...
```

它只负责：

```text
PCM 从哪里来
PCM 往哪里去
```

不负责 DSP、VAD、ASR。

Browser 不需要 `AudioDevice`，因为 Browser microphone 是远端媒体源。

### 3.4 `AudioProcessor`

当前：

```text
AudioFilter
```

改为：

```text
AudioProcessor
```

接口：

```python
class AudioProcessor(Protocol):

    speech_probability: float | None

    async def start(
        self,
        capture_format: AudioFormat,
        render_format: AudioFormat | None = None,
    ) -> None:
        ...

    async def process_capture(
        self,
        audio: AudioChunk,
    ) -> Sequence[AudioChunk]:
        ...

    async def process_render(
        self,
        audio: AudioChunk,
    ) -> Sequence[AudioChunk]:
        ...

    async def flush_capture(self) -> Sequence[AudioChunk]:
        ...

    async def close(self) -> None:
        ...
```

其中：

```text
process_capture()
→ microphone / near-end

process_render()
→ speaker / far-end
```

这个接口从一开始就兼容 AEC。

---

## 4. 核心重构方案

### 4.1 AudioProcessor

目录调整为：

```text
roboagent/speech/audio/
├── processor.py
├── passthrough.py
├── rnnoise.py
├── webrtc.py
├── buffer.py
└── vad.py
```

现有：

```text
PassthroughAudioFilter
RNNoiseFilter
```

改为：

```text
PassthroughAudioProcessor
RNNoiseProcessor
```

RNNoise 当前已有：

```text
pyrnnoise
soxr
48 kHz internal processing
speech probability
optional fallback
```

这些逻辑全部保留，仅适配新接口。

#### WebRTCAudioProcessor

新增：

```text
audio/webrtc.py
```

内部使用：

```text
pywebrtc-audio
```

支持：

```text
AEC3
NS
AGC2
high-pass filter
speech probability
```

对外暴露：

```text
speech_probability
gain_db
```

Browser 模式默认：

```text
AEC = false
```

因为浏览器已经：

```javascript
echoCancellation: true
noiseSuppression: true
```

Robot native 模式才默认考虑：

```text
AEC = true
NS = true
AGC = true
```

输出链必须支持 far-end reference：

```text
TTS PCM
   ↓
process_render()
   ↓
final render PCM
   ├── AEC far-end reference
   └── speaker
```

输入：

```text
microphone
   ↓
process_capture()
   ↓
AEC / NS / AGC
   ↓
clean PCM
```

---

### 4.2 统一内部音频格式

Speech Runtime 内部继续使用：

```text
PCM16
mono
16 kHz
20 ms/frame
```

即：

```text
320 samples
640 bytes
```

机器人硬件可以是：

```text
48 kHz
stereo
multi-channel
```

但进入：

```text
VAD
ASR
```

之前必须转换为 canonical speech format。

不要要求硬件本身输出 16 kHz。

---

### 4.3 Robot Native Audio

新增：

```text
speech/transport/local.py
```

组合：

```python
LocalSpeechTransport(
    audio_input=...,
    audio_output=...,
)
```

数据流：

```text
AudioInput
   ↓
LocalSpeechTransport
   ↓
SpeechSession
```

输出：

```text
SpeechSession
   ↓
LocalSpeechTransport
   ↓
AudioOutput
```

ALSA 只是第一种实现：

```text
AudioInput
├── AlsaAudioInput
└── future RobotSdkAudioInput

AudioOutput
├── AlsaAudioOutput
└── future RobotSdkAudioOutput
```

不要把 RoboAgent core 直接绑定 ALSA。

本地播放需要独立 playback queue：

```text
TTS
 ↓
bounded playback queue
 ↓
playback worker
 ↓
ALSA / Robot SDK
```

`clear_output()` 必须能够：

```text
clear pending PCM
+
stop current playback
```

否则 barge-in 会产生明显尾音。

---

### 4.4 Turn Handling

继续保留：

```text
TurnDetector
```

只负责：

```text
speech start
speech complete
silence endpoint
max duration
idle timeout
min speech duration
```

新增：

```text
turn/interruption.py
```

定义：

```python
class InterruptionDetector:

    def update(
        self,
        *,
        speaking: bool,
        confidence: float,
        level: float,
        output_active: bool,
        duration_ms: float,
    ) -> InterruptionDecision:
        ...

    def reset(self) -> None:
        ...
```

把当前 `SpeechSession._confirm_barge_in()` 移入这里。

初始策略仍使用：

```text
minimum duration
minimum confidence
minimum volume
```

例如：

```yaml
interruption:
  min_duration_ms: 400
  min_confidence: 0.60
  min_volume: 0.004
```

先保持简单可靠。

#### False interruption

参考 LiveKit，引入概念：

```text
candidate interruption
confirmed interruption
false interruption
```

V0.1 可以识别 false interruption，但不强制实现“恢复播放”。

原因是当前：

```text
Browser AudioContext
Robot playback queue
TTS provider
```

都还没有统一 resumable playback cursor。

先支持：

```text
false_interruption event
false_interruption_rate metric
```

后续再增加 resume。

---

### 4.5 Browser Playback

当前 `playback.clear` 不应该只重置：

```text
playbackTime
```

浏览器还应维护：

```javascript
playbackSources
```

收到 interruption：

```text
stop all scheduled AudioBufferSourceNode
clear playbackSources
reset playbackTime
```

这样才能真正降低：

```text
barge-in → audible stop
```

的延迟。

---

### 4.6 Metrics

新增：

```text
speech/metrics.py
```

至少记录：

```text
input_frames
dropped_frames
audio_process_ms

speech_start
speech_stop

ASR first partial
ASR final

Agent first token

TTS first audio

E2E turn latency

interruption latency
```

核心指标：

```text
ASR First Partial
ASR Final Latency
Agent TTFT
TTS TTFA
E2E Turn Latency
Barge-in Latency
Dropped Audio Frames
Playback Queue Latency
```

推荐定义：

```text
E2E Turn Latency
=
用户停止讲话
→
首个有效 TTS PCM 开始播放
```

每一轮结束后发一次：

```text
speech.metrics
```

不要每帧发 metrics。

---

## 5. Browser 与 Robot 两种运行模式

重构后的目标不是两套 Speech Runtime，而是两种 composition。

### Browser 模式

```text
Browser Microphone
   ↓
Browser AEC / NS
   ↓
AudioWorklet
   ↓
WebSocketSpeechTransport
   ↓
SpeechSession
   ↓
RNNoise / Passthrough
   ↓
VAD / ASR / Agent / TTS
   ↓
WebSocket
   ↓
Browser Playback
```

推荐：

```text
Browser AEC = ON
Server AEC = OFF
```

避免双重 AEC。

### Robot 模式

```text
Robot Microphone
   ↓
AudioInput
   ↓
LocalSpeechTransport
   ↓
WebRTCAudioProcessor
   ├── AEC3
   ├── NS
   ├── AGC2
   └── speech probability
   ↓
VAD / ASR / Agent / TTS
   ↓
WebRTCAudioProcessor.render
   ↓
AudioOutput
   ↓
Robot Speaker
```

Robot 模式中 AEC far-end 应尽量使用：

```text
最终实际送到扬声器的 PCM
```

而不是 TTS provider 原始 PCM。

---

## 6. 目录、配置与开发计划

推荐最终目录：

```text
roboagent/
└── speech/
    ├── __init__.py
    ├── config.py
    ├── errors.py
    ├── event.py
    ├── factory.py
    ├── metrics.py
    ├── session.py
    ├── types.py
    │
    ├── audio/
    │   ├── processor.py
    │   ├── passthrough.py
    │   ├── rnnoise.py
    │   ├── webrtc.py
    │   ├── buffer.py
    │   └── vad.py
    │
    ├── device/
    │   ├── base.py
    │   └── alsa.py
    │
    ├── turn/
    │   ├── detector.py
    │   └── interruption.py
    │
    ├── asr/
    ├── tts/
    ├── text/
    └── transport/
        ├── base.py
        └── local.py
```

Browser WebSocket transport 暂时继续放：

```text
examples/chat/speech_server.py
```

因为它依赖 FastAPI。

只有未来 WebSocket 成为 framework 正式 transport 后，再移入：

```text
speech/transport/websocket.py
```

### 推荐配置

Browser：

```yaml
speech:
  audio:
    processor: rnnoise

  vad:
    provider: silero

  turn:
    # Balanced endpointing: local VAD stop (280 ms) + this silence window.
    silence_ms: 400
    max_duration_ms: 20000
    min_speech_ms: 300

    interruption:
      enabled: true
      # A candidate is cancelled only after ASR returns meaningful text.
      min_duration_ms: 300
      min_confidence: 0.55
      min_volume: 0.003
```

Robot：

```yaml
speech:
  device:
    provider: alsa
    input_device: "hw:1,0"
    output_device: "hw:1,0"

    capture_sample_rate: 48000
    playback_sample_rate: 48000

    period_ms: 20
    buffer_ms: 80

  audio:
    processor: webrtc

    webrtc:
      echo_cancellation: true
      noise_suppression: true
      auto_gain_control: true
      stream_delay_ms: 60
```

### 开发阶段

第一阶段：修正核心抽象

```text
AudioFilter → AudioProcessor
新增 capture/render
迁移 Passthrough/RNNoise
SpeechSession 适配
```

第二阶段：完善 interruption

```text
新增 InterruptionDetector
移除 SpeechSession 内 barge-in policy
增加 false interruption 状态
```

第三阶段：接入 pywebrtc-audio

```text
WebRTCAudioProcessor
AEC3
NS
AGC2
speech probability
```

第四阶段：支持本地机器人音频

```text
AudioInput / AudioOutput
ALSA backend
LocalSpeechTransport
capture/playback queue
```

第五阶段：完善 playback cancellation

```text
Browser scheduled source stop
Robot playback queue clear
ALSA playback abort
```

第六阶段：增加 metrics

```text
ASR / Agent / TTS / E2E / interruption
```

第七阶段：增加本地语音示例

```text
examples/local_voice/
```

先使用：

```text
Ubuntu PC
+ USB microphone
+ speaker
```

验证完整链路，再接真实机器人硬件。

---

## 7. 最终约束

重构完成后必须满足：

```text
Browser microphone
USB microphone
Robot SDK microphone
```

互相替换时：

```text
SpeechSession
VAD
TurnDetector
InterruptionDetector
ASR
Agent
TTS
```

均无需修改。

同样：

```text
Browser speaker
ALSA speaker
Robot speaker
```

替换时，只修改：

```text
Transport / AudioDevice
```

核心职责最终冻结为：

```text
AudioDevice
→ 声音从哪里来、往哪里去

SpeechTransport
→ 媒体如何进入和离开 SpeechSession

AudioProcessor
→ PCM 如何完成 AEC / NS / AGC / format processing

VAD / Turn
→ 用户什么时候在说话、什么时候说完、是否真的要打断

ASR
→ 语音转文字

AgentSession
→ 理解、推理与工具执行

TTS
→ 文字转语音

SpeechSession
→ 将所有组件组织成一个实时语音会话
```

这应作为 RoboAgent 后续所有语音功能演进的核心架构约束。
