# NanoTune Toolbox

NanoTune Toolbox is a lightweight, 100% offline Windows companion utility developed by **mirakwgnr** (`@mirakwgnr` on Instagram) for **NanoTune (Desktop)**. Built for fast batch audio transcoding, synced lyrics conversion, and cover art extraction across modern and legacy PC hardware, it pairs adaptive cover-art theming with seamless local IPC integration into your main NanoTune player.

## Features

* **Batch Audio Transcoding:** Converts local `FLAC` (16/24-bit), `MP3`, `M4A` (AAC & ALAC), `WAV` (16/24/32-bit PCM & Mono), `OGG Vorbis`, `OPUS`, and `AIFF` files with selectable bitrates and bit-depths while preserving metadata tags, lyrics, and embedded artwork.
* **Lyrics Toolkit:** Extracts embedded lyrics to `.LRC`/`.ELRC`, smartly converts word-by-word `.ELRC` to line-synced `.LRC`, generates makeshift word-synced `.ELRC` from standard `.LRC`, strips timestamps to plain `.TXT`, shifts line and word timestamps by `±ms`, and embeds or removes lyric tags (`FLAC`, `MP3`, `M4A`).
* **Cover Art & Clean Copy:** Extracts full-resolution embedded album artwork as `.PNG` or `.JPG`, or exports metadata-free clean audio copies into the source directory.
* **Adaptive Cover Theming:** Extracts theme accents dynamically from album artwork—shifting the UI, interactive dropdown pills, progress bar, and Star + Hammer & Wrench icon to match each track, with a Solitaire-style cover stack for multi-file batches.
* **NanoTune Ecosystem Sync:** Automatically indexes your configured **NanoTune** music folders and library cache, shares your selected graphics renderer (`DirectX 10.1`, `OpenGL 2.1`, or `Software`), and pushes converted files straight into a running NanoTune instance via local IPC.
* **Built-In Preview Player:** Includes a lightweight bottom playback bar with `Space` to play/pause and a clickable seek slider to verify converted tracks immediately.

## System Requirements

| Component | Minimum | Recommended |
| :--- | :--- | :--- |
| **OS** | Windows 7 SP1 / 8 / 8.1 (64-bit)* | Windows 10 (1809+) / 11 (64-bit) |
| **Processor** | Intel Core i5-650 (3.2 GHz) or AMD Phenom II X4 955 / Athlon II X2 260 (or equivalent x86-64 CPU) | Intel Core i5-6500 (3.2 GHz) or AMD Ryzen 3 1200 (3.1 GHz) |
| **Memory** | 2 GB RAM (512 MB available) | 4 GB RAM or higher (for batch lossless transcoding) |
| **Graphics** | DirectX 10.0 or OpenGL 2.0 compatible GPU (Fallback supported) or CPU Rasterizer | DirectX 10.1 or OpenGL 2.1 compatible GPU (e.g., GTX 750 Ti / Radeon R7 260X) with 60Hz+ output |
| **Storage** | 150 MB available disk space | 250 MB+ SSD (for batch audio output) |
| **Audio** | DirectSound / WASAPI compatible sound device | WASAPI Exclusive-capable DAC or onboard audio chipset |

*\*Legacy Windows 7/8/8.1 environments use compatible runtime builds and default to native CPU software rendering.*

## Installation & Quick Start

1. Download and run the **NanoTune Toolbox** installer (close any running instances first).
2. Drag and drop one or more audio or `.LRC`/`.ELRC` files into the window, click **Open File(s)...**, or pick tracks directly from your synced **NanoTune Library**.
3. Filter by **Audio Codecs**, **Lyrics**, or **Cover & Tags**, choose a bitrate/bit-depth preset if applicable, and click **Run Task** (all outputs save safely in each source file's folder without overwriting).
4. Click **Import to NanoTune** on the bottom bar to send processed audio or lyric files straight into your running **NanoTune** desktop player.

## User Data & Related Projects

* **Shared Config & Cache:** `%APPDATA%\NanoTune\config.json` and `%APPDATA%\NanoTune\library_cache.json`
* **Uninstall:** Remove anytime via Windows **Installed Apps**.
* **Main Desktop Player:** [NanoTune for PC](https://github.com)
* **Android Port:** [NanoTune-Mobile](https://github.com-Mobile)

## Privacy & License

NanoTune Toolbox operates strictly offline as a local desktop audio and lyrics utility with no telemetry, analytics, or external network calls.

Licensed under the **GNU General Public License v2.0 (GPL-2.0)**. See `LICENCE.txt` or the [full GPL-2.0 license text](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt) for details.
