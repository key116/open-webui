# Interactive UI Card for Open WebUI

This is a small proof of concept for a reusable, model-generated choice card. It is a **Tool extension**, not a game and not a frontend patch.

## What it does

- Renders a responsive HTML card directly in the chat.
- Accepts a title, description, and 2-8 choice labels.
- Sends the clicked choice back to the conversation through Open WebUI's supported embed message flow.
- Leaves the story/game/test rules to the model, so the same UI can be reused across different activities.
- Uses escaped text for user/model-supplied labels and descriptions.

## Import

1. Open your Open WebUI instance.
2. Go to **Workspace → Tools** and create/import a Tool.
3. Use the contents of `interactive_ui_card.py` as the Tool code and save it.
4. Enable the Tool for the model you want to use. Make sure tool calling is enabled.
5. In a normal chat, ask the model to use `render_interactive_card` to show a choice card.

If your Open WebUI build does not offer file import, create a Tool and paste the file contents into its code editor.

## Example prompt

> Use the Interactive UI Card tool to show a card titled "The Locked Station". Describe that I found two exits and can hear a noise behind the door. Offer these choices: "Open the door", "Take the stairs", and "Inspect the ticket machine". After I choose, continue the scene based on my choice.

## Current limitations

- This first version is intentionally button-only. It does not store game state on its own.
- Clicking a button uses Open WebUI's `input:prompt:submit` embed message and may require the host's confirmation dialog before the choice is submitted. This follows the host's cross-origin embed safety behavior.
- The card is rendered in an iframe, so it does not gain direct access to the host application's internal state.
- This has been checked against the repository source flow, but not yet executed in a live Open WebUI instance. A live smoke test is still required.

## Implementation notes

The Tool returns a FastAPI `HTMLResponse` with an inline content disposition. Open WebUI's `process_tool_result` converts that response into an embedded UI. The buttons post `input:prompt:submit` to the parent frame, which the chat component handles as a prompt submission request.
