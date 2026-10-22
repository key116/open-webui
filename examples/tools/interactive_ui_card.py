"""
title: Interactive UI Card
author: OpenAI
author_url: https://github.com/key116/open-webui
funding_url: https://github.com/key116/open-webui
version: 0.1.0
description: Render a reusable interactive card with buttons inside an Open WebUI chat. Clicking a button sends the selected choice back to the conversation.
required_open_webui_version: 0.6.0
"""

from html import escape
from fastapi.responses import HTMLResponse


class Tools:
    def render_interactive_card(
        self,
        title: str,
        description: str,
        options: list[str],
    ) -> HTMLResponse:
        """
        Display a reusable interactive card in the chat.

        Use this when the user should make a choice in a game, simulation,
        branching story, quiz, or interactive test. Pass 2-8 concise options.
        Each option is shown as a button. When clicked, the choice is sent
        back to the current conversation for the assistant to continue.

        Args:
            title: Short title shown at the top of the card.
            description: The situation, question, or instruction shown above the buttons.
            options: A list of 2-8 button labels. Keep each label concise.
        """
        safe_title = escape((title or "Interactive choice").strip()[:120])
        safe_description = escape((description or "").strip()[:1200]).replace("\n", "<br>")
        clean_options = [
            str(option).strip()[:120]
            for option in (options or [])
            if str(option).strip()
        ][:8]

        if len(clean_options) < 2:
            clean_options = ["Continue", "Go back"]

        buttons = []
        for option in clean_options:
            prompt = (
                f'玩家在互动卡《{(title or "Interactive choice").strip()[:120]}》中选择了：'
                f'{option}\n'
                '请根据这个选择继续当前游戏、故事或测试。不要重复询问已经做出的选择。'
            )
            buttons.append(
                '<button type="button" class="choice" data-prompt="'
                + escape(prompt, quote=True)
                + '">'
                + escape(option)
                + '</button>'
            )

        html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 12px; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.card { width: 100%; max-width: 720px; margin: 0 auto; border: 1px solid rgba(128,128,128,.32); border-radius: 16px; padding: 16px; background: rgba(128,128,128,.06); }
h2 { margin: 0 0 8px; font-size: 17px; line-height: 1.4; }
.description { font-size: 14px; line-height: 1.65; overflow-wrap: anywhere; margin-bottom: 14px; }
.choices { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 8px; }
.choice { appearance: none; width: 100%; min-height: 44px; padding: 10px 12px; border: 1px solid rgba(128,128,128,.42); border-radius: 11px; background: transparent; color: inherit; text-align: left; font: inherit; cursor: pointer; overflow-wrap: anywhere; }
.choice:hover { background: rgba(128,128,128,.14); }
.choice:focus-visible { outline: 2px solid #6b9cff; outline-offset: 2px; }
.choice:disabled { opacity: .6; cursor: default; }
.status { min-height: 18px; margin-top: 10px; font-size: 12px; opacity: .75; }
</style>
</head>
<body>
<section class="card" aria-labelledby="card-title">
  <h2 id="card-title">__TITLE__</h2>
  <div class="description">__DESCRIPTION__</div>
  <div class="choices">__BUTTONS__</div>
  <div class="status" aria-live="polite" id="status">选择一个选项继续。</div>
</section>
<script>
(() => {
  const buttons = Array.from(document.querySelectorAll('button[data-prompt]'));
  const status = document.getElementById('status');
  let submitted = false;

  const resize = () => {
    parent.postMessage({ type: 'iframe:height', height: document.documentElement.scrollHeight }, '*');
  };

  buttons.forEach((button) => {
    button.addEventListener('click', () => {
      if (submitted) return;
      submitted = true;
      buttons.forEach((item) => { item.disabled = true; });
      status.textContent = '已提交选择，等待聊天继续……';
      parent.postMessage({ type: 'input:prompt:submit', text: button.dataset.prompt }, '*');
      resize();
    });
  });

  window.addEventListener('load', resize);
  if ('ResizeObserver' in window) {
    new ResizeObserver(resize).observe(document.body);
  } else {
    setTimeout(resize, 50);
  }
})();
</script>
</body>
</html>"""
        html = (
            html.replace("__TITLE__", safe_title)
            .replace("__DESCRIPTION__", safe_description)
            .replace("__BUTTONS__", "".join(buttons))
        )

        return HTMLResponse(
            content=html,
            headers={"Content-Disposition": 'inline; filename="interactive-card.html"'},
        )
