"""Quick (intuition) and Thinker (CoT) shogi players using Gemma + JSON schema."""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from typing import Callable
import httpx
from shogi_game import board_to_ascii
import shogi


QUICK_SYSTEM = """あなたは将棋の対局者です。スタイルは「直感型・即答」。
盤面と合法手リスト（USI 表記）を見て、深く読まずに直感で着手を決めます。

USI 表記:
- 通常の手: 例 "7g7f"（7七から7六へ）。成る場合は末尾に + を付ける（例 "8h2b+"）
- 駒打ち: 例 "P*5e"（持ち駒の歩を5五へ打つ）。駒種は P=歩, L=香, N=桂, S=銀, G=金, B=角, R=飛
- 必ず提示された合法手リストの中から1つを選ぶこと

JSON で次を返してください:
- intent: この手で何を狙っているかの宣言文（例 "中央を厚くして相手の角の働きを止める狙い"）
- move: USI 表記の手"""

THINKER_SYSTEM = """あなたは将棋の対局者です。スタイルは「熟考型・じっくり読む」。
盤面と合法手リスト（USI 表記）を見て、最低3手の候補を比較検討します。

検討の観点:
- 王の安全度（自玉/相手玉の囲い、王手・寄せの可能性）
- 駒の働き（飛・角の利きを通すか塞ぐか、桂・香の活用）
- 持ち駒の活用（打ち場所の脅威）
- 駒得・駒損の見通し
- 相手の最善応手を想定する

USI 表記:
- 通常の手: 例 "7g7f"。成る場合は末尾に + を付ける（例 "8h2b+"）
- 駒打ち: 例 "P*5e"（持ち駒を5五へ打つ）

JSON で次を返してください:
- thinking: 候補手3つ以上を比較した詳細な検討プロセス
- summary: 最終決定の理由を一行で端的に
- move: USI 表記の手（必ず合法手リストから選ぶこと）"""


# USI move regex: normal moves OR drops
USI_PATTERN = r"^([1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])$"

QUICK_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "minLength": 5},
        "move": {"type": "string", "pattern": USI_PATTERN},
    },
    "required": ["intent", "move"],
    "additionalProperties": False,
}

THINKER_SCHEMA = {
    "type": "object",
    "properties": {
        "thinking": {"type": "string", "minLength": 30},
        "summary": {"type": "string", "minLength": 5},
        "move": {"type": "string", "pattern": USI_PATTERN},
    },
    "required": ["thinking", "summary", "move"],
    "additionalProperties": False,
}


@dataclass
class AgentResponse:
    move: str | None  # USI string, or None if all retries failed
    raw_text: str
    reasoning: str
    comment: str
    retries: int = 0
    forced_fallback: bool = False


class GemmaPlayer:
    def __init__(self, name: str, style: str, model: str, base_url: str, api_key: str):
        assert style in ("quick", "thinker")
        self.name = name
        self.style = style
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.system_prompt = QUICK_SYSTEM if style == "quick" else THINKER_SYSTEM
        self.schema = QUICK_SCHEMA if style == "quick" else THINKER_SCHEMA
        self.schema_name = "quick_move" if style == "quick" else "thinker_move"
        self.max_tokens = 500 if style == "quick" else 2500
        self.temperature = 0.7 if style == "quick" else 0.4

    def _build_user_msg(self, board: shogi.Board, legal_usi: list[str]) -> str:
        color = "先手(▲)" if board.turn == shogi.BLACK else "後手(△)"
        # Truncate display of legal moves if extremely long
        legal_str = ", ".join(legal_usi)
        return (
            f"あなたは {color} の手番です。\n\n"
            f"現在の盤面:\n{board_to_ascii(board)}\n\n"
            f"合法手 ({len(legal_usi)}手): {legal_str}\n\n"
            f"この中から1つ選んで JSON で答えてください。"
        )

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": self.schema_name, "schema": self.schema},
            },
        }

    def _stream_one_attempt(self, messages, on_chunk_with_delta):
        text = ""
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=self._payload(messages, stream=True),
            timeout=300,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}).get("content")
                if delta:
                    text += delta
                    if on_chunk_with_delta:
                        on_chunk_with_delta(delta)
        return text

    def choose_move_streaming(
        self,
        board: shogi.Board,
        legal_usi: list[str],
        on_chunk: Callable[[str, str], None] | None = None,
        max_retries: int = 2,
    ) -> AgentResponse:
        user_msg = self._build_user_msg(board, legal_usi)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_msg},
        ]
        legal_set = set(legal_usi)
        overall_display = ""
        last_text = ""

        for attempt in range(max_retries + 1):
            if attempt > 0:
                marker = f"\n\n--- 🔁 再試行 {attempt}/{max_retries}（反則手のため）---\n\n"
                overall_display += marker
                if on_chunk:
                    on_chunk(marker, overall_display)

            def _per_chunk(delta: str):
                nonlocal overall_display
                overall_display += delta
                if on_chunk:
                    on_chunk(delta, overall_display)

            text = self._stream_one_attempt(messages, _per_chunk)
            last_text = text

            move_str, intent, thinking, summary = self._parse_json(text)
            if move_str and move_str in legal_set:
                return AgentResponse(
                    move=move_str, raw_text=overall_display,
                    reasoning=thinking, comment=(intent if self.style == "quick" else summary),
                    retries=attempt, forced_fallback=False,
                )

            attempted = move_str or "(parse-failed)"
            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": (
                    f"あなたが返した {attempted!r} は合法手ではありません。"
                    f"合法手は次のいずれかだけです（USI表記）: {', '.join(legal_usi)}。"
                    f"必ずこの中から1つだけ選び、同じ JSON フォーマットで再回答してください。"
                ),
            })

        # All retries failed → fallback to first legal
        move_str, intent, thinking, summary = self._parse_json(last_text)
        fallback = legal_usi[0]
        return AgentResponse(
            move=fallback, raw_text=overall_display,
            reasoning=thinking, comment=(intent if self.style == "quick" else summary) or "(fallback)",
            retries=max_retries, forced_fallback=True,
        )

    @staticmethod
    def _parse_json(text: str) -> tuple[str, str, str, str]:
        try:
            obj = json.loads(text)
            return (
                obj.get("move", "") or "",
                obj.get("intent", "") or "",
                obj.get("thinking", "") or "",
                obj.get("summary", "") or "",
            )
        except json.JSONDecodeError:
            m = re.search(USI_PATTERN.replace("$", "").replace("^", ""), text)
            return (m.group(0) if m else "", "", "", "")


def make_players() -> tuple[GemmaPlayer, GemmaPlayer]:
    base_url = os.environ.get("GEMMA_BASE_URL", "")
    api_key = os.environ.get("GEMMA_API_KEY", "")
    model = os.environ.get("GEMMA_MODEL", "")
    missing = [k for k, v in {
        "GEMMA_API_KEY": api_key, "GEMMA_BASE_URL": base_url, "GEMMA_MODEL": model,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Required env vars not set: {', '.join(missing)} (see .env.example)")
    quick = GemmaPlayer("Gemma Quick", "quick", model, base_url, api_key)
    thinker = GemmaPlayer("Gemma Thinker", "thinker", model, base_url, api_key)
    return quick, thinker
