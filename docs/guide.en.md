# Kataribe guide

How to use what the README does not cover.

## Make it talk

1. Type what you want to hear in the input box and press Enter (for example, "Tell me a story about a little rabbit before bed").
2. It keeps talking until you stop it. Press Esc to stop.
3. Send something like "slower" or "tell a different story" partway through, and it changes from the next line.
4. Text in quotes ("…" or 「」) is read as is, without the LLM.

## Voice

- Write a voice in your request, such as "in a low, calm male voice", and it speaks in that voice.
- Save a voice you like with "Save voice" above the input box, and give it a name.
- Click the voice name to switch to a new voice or a saved one.
- Set the voice to use when a request names none in "Default voice" in the settings.

## History

The button at the top left opens a list of past conversations. Pick one to continue it in the same voice.

## Settings

Open them with the gear at the top right.

- Speech model: choose from the models speech.cpp supports. You can also choose the reading language here.
- LLM: choose Claude Code, Codex, or LM Studio, and check it with "Connection test". If you are not signed in to Claude Code or Codex, a "Sign in" button appears.
- Writing style: edit the instructions given to the LLM. "Reset to default" puts them back.

## Main keys

| Key | Action |
|---|---|
| Enter / Shift + Enter | Send / new line |
| Esc | Pause |
| Ctrl + Space | Pause or resume |
| Ctrl + , | Open settings |

You can change the keys in "Keyboard shortcuts" in the settings.

## Control from the command line

You can send commands to a running Kataribe from a terminal.

```bash
python server.py send "Tell me a story about a little rabbit before bed"
python server.py say "Good work today."
python server.py pause
python server.py status
```

Run `python server.py --help` to see the other commands.

## How it opens

| Option | Opens |
|---|---|
| none | in the Kataribe window |
| `--browser` | in your usual browser |
| `--port N` | on another port (default 7865) |
