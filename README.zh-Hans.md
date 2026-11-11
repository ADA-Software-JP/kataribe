# Kataribe — 实时语音朗读应用

[日本語](README.md) | [English](README.en.md) | 简体中文 | [繁體中文](README.zh-Hant.md)

https://github.com/user-attachments/assets/a69ba1f2-ea7c-40d0-b959-9fef431311ed

https://github.com/user-attachments/assets/91afa4d4-78fe-4e27-a7ec-b2127bd59329

<br>
<br>

新闻解说、睡前故事、工作时的电台闲聊。只要说一句需求，Kataribe 就会一直说下去，直到你停止。文稿由 LLM 撰写，声音在你自己的电脑上生成。

## 主要功能

- **一直说到你叫停**
  想听新闻解说、助眠故事或电台式闲聊，说一句它就会开始。中途发一句“再慢一点”，故事走向和说话方式会立刻改变。
- **用文字描述声音**
  写下“沉稳温柔的女声”“清晰易懂的新闻主播”，它就用那样的声音说话。喜欢的声音可以起名保存，下次直接调用。
- **从上次的地方继续**
  过去的对话会保存在列表里，可以用保存的声音接着说。
- **自选撰写文稿的 AI**
  可选择 LM Studio、Claude Code 或 Codex。使用 LM Studio 时，一切都在你的电脑里完成，无需任何费用。

## 关于语音模型和声音

生成声音的模型可以在设置中切换。
语音由 [audio.cpp](https://github.com/0xShug0/audio.cpp) 生成（已在使用 [speech.cpp](https://github.com/nyosegawa/speech.cpp) 的用户可继续使用）。语音模型可在首屏选择：日语为 [Irodori-TTS](https://huggingface.co/Aratako/Irodori-TTS-500M)，其他语言为 Supertonic 3（31 种语言）和 Qwen3-TTS（中文等 10 种语言）。

- **Irodori-TTS**：日语。默认模型。用文字描述就能创建声音并保存。
- **Qwen3-TTS**：10 种语言（含英语、中文、日语、韩语）。从 aiden、ryan、serena 等 9 种内置声音中选择。“慢一点”“低沉平静”等说话方式的指示会生效。
- **Drama 3**：83 种语言。可以用你自己的录音制作声音并让它说话。不可用于商业用途。模型约 20.8GB。

## 运行环境

- 支持的系统：Windows、macOS（Apple Silicon）
- 需要 Python 3
- 声音由 GPU 生成。在 NVIDIA RTX 3060 上，几秒的语音约 0.5 秒即可生成。

## 使用方法

### 1. 安装与启动

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

### 2. 首次设置

首次启动时会显示设置界面。选择语音模型并执行后，会自动下载语音合成引擎 `audio.cpp`（约 84MB）和所选模型（0.5～2.8GB）。

### 3. 打包成应用（可选）

如果想作为独立应用启动，而不是通过 Python 命令启动，可以自行构建。
执行以下命令后，Windows 会生成 `dist/Kataribe/`，macOS 会生成 `dist/Kataribe.app`。

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean Kataribe.spec
```

详细设置和命令行操作请参阅 [docs/guide.zh-Hans.md](docs/guide.zh-Hans.md)。

## 许可证

[Apache License 2.0](LICENSE)
[audio.cpp](https://github.com/0xShug0/audio.cpp)、[speech.cpp](https://github.com/nyosegawa/speech.cpp) 以及 [Irodori-TTS](https://huggingface.co/Aratako/Irodori-TTS-500M) 等各语音模型遵循各自的许可证。
