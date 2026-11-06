# Scope Guard demo video

[`scopeguard-demo-final.mp4`](scopeguard-demo-final.mp4) is the finished 1:38 narrated walkthrough at 3840×2160 and 60 fps, with captions. `scopeguard-demo-4k60-silent.mp4` is its silent visual master. The dashboard, engagement room, and signed change order are direct captures of the running app using seeded deterministic demo data; the surrounding explanation and SG focus-frame mark are rendered at 4K by `scripts/render_demo_video.py`.

## Add ElevenLabs narration

Generate the eight clips from [`scene-narration-cues.md`](scene-narration-cues.md), using the matching files in [`elevenlabs-scenes`](elevenlabs-scenes). Place each clip at its listed time in Studio, export one combined MP3 or WAV, then run from the repository root:

```powershell
.\scripts\mux_demo_audio.ps1 -Audio .\path\to\elevenlabs-voiceover.mp3
```

The script creates `scopeguard-demo-final.mp4` with the 4K60 video, AAC narration, and selectable subtitles. To rebuild the silent master after changing the script, run:

```powershell
py -3.13 .\scripts\render_demo_video.py
```
