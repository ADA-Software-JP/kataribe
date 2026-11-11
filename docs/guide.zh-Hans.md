# Kataribe 指南

这里介绍 README 中没有写到的用法。

## 让它说话

1. 在输入框中写下想听的内容，按 Enter（例如「睡前给我讲一个小兔子的故事」）。
2. 它会一直说下去，直到你停止。按 Esc 停止。
3. 中途发送「慢一点」「换个故事」等，从下一行开始改变。
4. 用「」括起来的文字不经过 LLM，直接朗读。

## 声音

- 在请求中写「用低沉平静的男声」等，它就用那个声音说话。
- 喜欢的声音可以用输入框上方的「保存声音」命名保存。
- 点击声音名称，可以切换到新的声音或已保存的声音。
- 请求中没有指定声音时使用的声音，可以在设置的「默认声音」中决定。

## 历史

左上角的按钮会打开以往对话的列表。选择一个对话，就会用同一个声音接着说下去。

## 设置

从右上角的齿轮打开。

- 语音合成模型：从 speech.cpp 支持的模型中选择。朗读的语言也可以在这里选择。
- 语音引擎：除了 speech.cpp，还可以选择 [audio.cpp](https://github.com/0xShug0/audio.cpp)。使用 audio.cpp 时，请填写 `audiocpp_server` 可执行文件和 Irodori-TTS 的 GGUF（如 Anime 版）的位置。点击首屏的“安装”，会自动安装 audio.cpp 和语音模型（约 1.1GB）。
- LLM：从 Claude Code、Codex、LM Studio 中选择，并用「连接测试」确认。未登录 Claude Code 或 Codex 时，界面上会出现登录按钮。
- 写作风格：可以修改交给 LLM 的指示。用「重置为默认」恢复原样。

## 主要按键

| 按键 | 操作 |
|---|---|
| Enter / Shift + Enter | 发送 / 换行 |
| Esc | 暂停 |
| Ctrl + Space | 暂停与继续切换 |
| Ctrl + , | 打开设置 |

按键可以在设置的「键盘快捷键」中更改。

## 用命令操作

可以从终端向正在运行的 Kataribe 发送指令。

```bash
python server.py send "睡前给我讲一个小兔子的故事"
python server.py say "今天也辛苦了。"
python server.py pause
python server.py status
```

其他操作可以用 `python server.py --help` 查看。

## 打开方式

| 参数 | 打开方式 |
|---|---|
| 无 | 在 Kataribe 的窗口中打开 |
| `--browser` | 在平时使用的浏览器中打开 |
| `--port N` | 更改端口（默认 7865） |
