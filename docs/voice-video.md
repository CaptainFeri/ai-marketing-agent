# The `voice` mode video pipeline

Handoff section 8, phase 1 weeks 5-6: "ویدیوی حالت `none` و `voice` (Piper،
Chatterbox، Whisper، FFmpeg)." `none` needed nothing more than what the image
queue already builds. This is `voice`: one narration track, its captions, and
a muxed video — end to end, real, no model weights required.

```
gate 1 approved
  └─ start_media
       └─ GpuJob(image_flux) ×4 per distinct visual brief   (docs/image-queue.md)
       └─ GpuJob(tts) ×1, for `voice` and `face` packages
            └─ gpu.dispatch → execute_tts_job
                 ├─ tts_backend.synthesize()   one real audio segment per section
                 ├─ subtitle_render.to_srt()   timed from those segments directly
                 └─ MediaAsset(audio) + MediaAsset(subtitle), stored
       └─ once nothing is left pending → media_finished → gate 2
gate 2 approved (an image selected, video_mode != none)
  └─ media_cpu.mux_voice_video
       ├─ the selected image + the narration + the captions
       └─ video_compose.mux_voice_video()   real ffmpeg → MediaAsset(video)
```

## No Whisper step

The handoff's own plan runs Whisper over the finished narration to time
captions. That makes sense when the voice came from a model whose output you
have not seen before. Ours does not: `tts_backend` synthesizes one section at
a time and already knows both the exact text and the exact duration of each
piece of audio it produced — strictly better ground truth than
re-transcribing our own speech would give back. `GpuJobKind.TRANSCRIBE_WHISPER`
stays in the schema for whatever eventually needs to time a *human-recorded*
track (the `face` mode speaker profile, phase 3); `execute_tts_job` never
queues it. See `app.services.subtitle_render`.

## Why espeak-ng, not Piper, is the default

The handoff's own model table (section 5) specifies Piper for Persian and
Chatterbox for English/Arabic. Both need a downloaded voice model before
they can run at all, and neither runs in every environment this platform
runs in — the model host is not always reachable, the same class of gap
phase 0 exists to close for FLUX and Wan. Unlike those, though, nothing about
TTS needs the GPU: `espeak-ng` ships Persian and Arabic voices in the OS
package itself, no download, no phase 0 dependency. It is a formant
synthesizer, not a neural one, so the voice is audibly robotic — but it is
genuine, correctly-pronounced speech, not a placeholder tone, which is what
lets the whole pipeline below it (subtitles, muxing, gate 2, publishing) run
for real today instead of waiting on phase 0.

`TTS_BACKEND=espeak` (the default) uses it. `TTS_BACKEND=production` routes
Persian to Piper and everything else to Chatterbox, both of which raise
`NotImplementedError` with the same "phase 0 must first deliver this" message
`ComfyUIImageBackend` and `UnavailableGpuRuntime` use — flipping the setting
is meant to be the only change needed once phase 0 ships real voice models.

## What's real today, and what phase 0 still has to supply

| Piece | Status |
|---|---|
| Queueing, leasing, quota accounting for the TTS job | real (same machinery as text and images) |
| **Narration** (`tts_backend.py`, `espeak-ng`) | **real** — genuine synthesized speech, correctly pronounced, no model download |
| **Subtitle timing** (`subtitle_render.py`) | **real** — derived directly from each section's own audio duration, not transcribed |
| **Muxing** (`video_compose.py`, `ffmpeg`) | **real** — an actual still-image-plus-narration MP4 with burned-in captions |
| Production-quality voice (Piper for fa, Chatterbox for en/ar) | **simulated stand-in only** — `TTS_BACKEND=production` fails loudly until phase 0 delivers both voice models |
| `face` mode (a real or AI-generated moving face, lip-synced) | out of scope — phase 3 |

Every AI-synthesized audio and video asset is stored with
`is_ai_labelled=True` (decision D7) — narration is never a real person's
voice, so the label is unconditional rather than something a speaker profile
opts into.

## Trying it

```
espeak-ng -v fa -s 165 -w out.wav "یک جمله آزمایشی"   # confirms the voice is installed
ffmpeg -filters | grep subtitles                       # confirms libass support
```

Both ship in the Docker image (`Dockerfile`); on a bare host, `apt-get
install espeak-ng ffmpeg`. `tests/test_tts_backend.py`,
`tests/test_video_compose.py` and `tests/test_voice_video_e2e.py` skip
themselves when either binary is missing rather than failing the suite.
