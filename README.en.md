# Kataribe — Real-time read-aloud app

[日本語](README.md) | English | [简体中文](README.zh-Hans.md) | [繁體中文](README.zh-Hant.md)

https://github.com/user-attachments/assets/a69ba1f2-ea7c-40d0-b959-9fef431311ed

https://github.com/user-attachments/assets/91afa4d4-78fe-4e27-a7ec-b2127bd59329

<br>
<br>

News explainers, bedtime stories, background chatter while you work. Ask for it in one line and Kataribe keeps talking until you stop it. An LLM writes the script, and the voice is generated on your own PC.

## Features

- **Keeps talking until you stop it**
  Ask for a news rundown, a story to fall asleep to or some radio-style chatter, and it starts on its own. Send a line like “slower, please” partway through, and the story and the delivery change on the spot.
- **Describe the voice in words**
  Write “a calm, soothing woman” or “a clear newscaster” and it speaks that way. Name a voice you like to save it, and call it up again any time.
- **Pick up where you left off**
  Past conversations stay in a list. Resume any of them with the same saved voice.
- **Choose the AI that writes**
  Pick LM Studio, Claude Code or Codex. With LM Studio, everything runs on your PC, so it costs nothing.

## Voice models and voices

Switch the model that generates the voice in the settings.
Voices are generated with [audio.cpp](https://github.com/0xShug0/audio.cpp) (if you already use [speech.cpp](https://github.com/nyosegawa/speech.cpp), it keeps working). You choose the voice model on the first screen: [Irodori-TTS](https://huggingface.co/Aratako/Irodori-TTS-500M) for Japanese, and Supertonic 3 (31 languages) or Qwen3-TTS (10 languages including Chinese) for other languages.

- **Irodori-TTS**: Japanese. The default model. Create a voice from a written description and save it.
- **Qwen3-TTS**: 10 languages, including English, Chinese, Japanese and Korean. Choose from 9 built-in voices such as aiden, ryan and serena. Directions like “slowly” or “low and calm” are applied.
- **Drama 3**: 83 languages. Create a voice from your own recording and have it speak. Not for commercial use. The model is about 20.8 GB.

## Requirements

- Supported OS: Windows and macOS (Apple Silicon)
- Python 3 is required
- The voice is generated on the GPU. On an NVIDIA RTX 3060, a few seconds of speech takes about 0.5 seconds.

## Usage

### 1. Install and run

**Windows**

```bash
git clone https://github.com/ADA-Software-JP/kataribe
cd kataribe
python -m pip install -r requirements.txt
python server.py
```

**macOS**

```bash
git clone https://github.com/ADA-Software-JP/kataribe
cd kataribe
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

### 2. First-time setup

The setup screen appears on first launch. Choose a voice model and run it, and the `audio.cpp` speech engine (about 84 MB) and the chosen model (0.5 to 2.8 GB) are downloaded automatically.

### 3. Build an app (optional)

To launch it as a standalone app instead of from a Python command, you can build it yourself.
Run the following to create `dist/Kataribe/` on Windows or `dist/Kataribe.app` on macOS.

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean Kataribe.spec
```

See [docs/guide.en.md](docs/guide.en.md) for detailed settings and command-line use.

## License

[Apache License 2.0](LICENSE)
[audio.cpp](https://github.com/0xShug0/audio.cpp), [speech.cpp](https://github.com/nyosegawa/speech.cpp), and each voice model such as [Irodori-TTS](https://huggingface.co/Aratako/Irodori-TTS-500M) follow their own licenses.
