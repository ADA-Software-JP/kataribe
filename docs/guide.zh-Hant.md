# Kataribe 指南

這裡介紹 README 中沒有寫到的用法。

## 讓它說話

1. 在輸入框中寫下想聽的內容，按 Enter（例如「睡前給我講一個小兔子的故事」）。
2. 它會一直說下去，直到你停止。按 Esc 停止。
3. 中途傳送「慢一點」「換個故事」等，從下一行開始改變。
4. 用「」括起來的文字不經過 LLM，直接朗讀。

## 聲音

- 在請求中寫「用低沉平靜的男聲」等，它就用那個聲音說話。
- 喜歡的聲音可以用輸入框上方的「儲存聲音」命名儲存。
- 點選聲音名稱，可以切換到新的聲音或已儲存的聲音。
- 請求中沒有指定聲音時使用的聲音，可以在設定的「預設聲音」中決定。

## 紀錄

左上角的按鈕會開啟以往對話的清單。選擇一個對話，就會用同一個聲音接著說下去。

## 設定

從右上角的齒輪開啟。

- 語音合成模型：從 speech.cpp 支援的模型中選擇。朗讀的語言也可以在這裡選擇。
- 語音引擎：除了 speech.cpp，也可以選擇 [audio.cpp](https://github.com/0xShug0/audio.cpp)。使用 audio.cpp 時，請填寫 `audiocpp_server` 執行檔和 Irodori-TTS 的 GGUF（如 Anime 版）的位置。
- LLM：從 Claude Code、Codex、LM Studio 中選擇，並用「連線測試」確認。未登入 Claude Code 或 Codex 時，畫面上會出現登入按鈕。
- 撰寫風格：可以修改交給 LLM 的指示。用「重設為預設值」恢復原樣。

## 主要按鍵

| 按鍵 | 操作 |
|---|---|
| Enter / Shift + Enter | 傳送 / 換行 |
| Esc | 暫停 |
| Ctrl + Space | 暫停與繼續切換 |
| Ctrl + , | 開啟設定 |

按鍵可以在設定的「鍵盤快速鍵」中變更。

## 用指令操作

可以從終端機向正在執行的 Kataribe 傳送指令。

```bash
python server.py send "睡前給我講一個小兔子的故事"
python server.py say "今天也辛苦了。"
python server.py pause
python server.py status
```

其他操作可以用 `python server.py --help` 查看。

## 開啟方式

| 參數 | 開啟方式 |
|---|---|
| 無 | 在 Kataribe 的視窗中開啟 |
| `--browser` | 在平常使用的瀏覽器中開啟 |
| `--port N` | 變更連接埠（預設 7865） |
