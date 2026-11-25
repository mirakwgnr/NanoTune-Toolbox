# NanoTune Toolbox

NanoTune Toolbox is a lightweight, 100% offline Windows companion utility developed by **mirakwgnr** (github.com/mirakwgnr) for **NanoTune**. It provides audio transcoding, lyrics conversion, timestamp shifting, and cover art extraction in an adaptive interface.

## Key Features

- **Audio Transcoding:** Batch-convert tracks across FLAC, MP3, M4A, WAV, OGG Vorbis, OPUS, and AIFF while preserving metadata, lyrics, and artwork.
- **Lyrics Toolkit:** Extract embedded lyrics, convert `.ELRC` to `.LRC` (and vice versa), strip or shift timestamps, and embed or remove lyric tags.
- **Cover Art & Metadata:** Extract embedded album artwork as PNG/JPG or export clean, metadata-free audio.
- **NanoTune Integration:** Automatically links with `%APPDATA%\NanoTune` to share configurations, renderers, and send files via local IPC.
- **Batch Processing:** Support for drag-and-drop file queues, Solitaire-style cover stacks, and a built-in preview bar.

## System Requirements

- **OS:** Windows 7 SP1 / 8 / 8.1 (64-bit) (minimum) / Windows 10 (1809+) or 11 (64-bit) (recommended)
- **Processor:** Intel Core i5-650 / AMD Phenom II X4 955 (minimum) / Intel Core i5-6500 or AMD Ryzen 3 1200 (recommended)
- **Memory:** 2 GB RAM (minimum) / 4 GB RAM or higher (recommended)
- **Storage:** 150 MB available disk space (minimum) / 250 MB+ SSD (recommended)

## Quick Start

1. Launch `NanoTuneToolbox.exe` (or run the installer).
2. Drag and drop audio or `.LRC`/`.ELRC` files into the window or select them from the NanoTune Library.
3. Choose your desired output preset under Audio Codecs, Lyrics, or Cover & Tags, then click **Run Task**.
4. Click **Import to NanoTune** on the bottom bar to send processed files directly to your desktop player.

## License & Related Projects

- **Main Desktop Player:** [NanoTune for PC](https://github.com/mirakwgnr/nanotune)
- **Android Port:** [NanoTune-Mobile](https://github.com/mirakwgnr/nanotune-mobile)

Licensed under the **GNU General Public License v2.0 (GPL-2.0)**. Operates strictly offline with no telemetry or external network calls.
