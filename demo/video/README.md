# Scope Guard demo video

[`scopeguard-demo-youtube.mp4`](scopeguard-demo-youtube.mp4) is the upload-ready 1:38 walkthrough at 3840×2160 and 60 fps, with the original narration and captions preserved. It adds a short, genuine Switch Town Square exchange between the solo project owner and Codex. That capture is 1080p footage upscaled in the 4K output. The visible request identifies the sender as the project owner; it is not a teammate conversation and does not satisfy the Tracks 1 & 2 teammate-chat requirement. `scopeguard-demo-final.mp4` remains the narrated product walkthrough without this chat insert; `scopeguard-demo-4k60-silent.mp4` is its silent visual master.

The dashboard, engagement, and signed change order are direct 4K captures of the running app using seeded deterministic demo data. The opening and close use the same lime crosshair mark as the app. The workflow diagram is labelled **Deterministic replay**. The Switch segment is a read-only Codex review requested by the project owner; it contains no teammate approvals or signatures.

## Add ElevenLabs narration

Generate the eight clips from [`scene-narration-cues.md`](scene-narration-cues.md), using the matching files in [`elevenlabs-scenes`](elevenlabs-scenes). Place each clip at its listed time in Studio, export one combined MP3 or WAV, then run from the repository root:

```powershell
.\scripts\mux_demo_audio.ps1 -Audio .\path\to\elevenlabs-voiceover.mp3
```

The script creates `scopeguard-demo-final.mp4` with the 4K60 video, AAC narration, and selectable subtitles. To rebuild the silent master after changing the script, run:

```powershell
py -3.13 .\scripts\render_demo_video.py
```
