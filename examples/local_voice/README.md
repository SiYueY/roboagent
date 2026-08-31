# Local voice example

Install `roboagent[speech,speech-webrtc,speech-alsa]`, configure `speech.device`, then run:

```bash
uv run python examples/local_voice/app.py
```

The example uses ALSA devices such as `default` or `hw:1,0`. It expects a configured model and DashScope speech credentials.
