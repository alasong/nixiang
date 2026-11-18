"""文本看板：TASKS.md 的读写与状态机。

一个任务一行，固定 7 列，状态只取 todo / doing / done / blocked。
这个模块负责：解析、渲染、推进状态、跑验收命令并写证据。

它不是实验台的一部分，是管理实验台的工具：**清单里没有验收命令的活不许开工**。

用法：
    python3 -m mvp.board                  # 看板视图（按状态分列）
    python3 -m mvp.board start T2         # todo → doing
    python3 -m mvp.board verify T2        # 跑验收命令，过则 done
    python3 -m mvp.board add --title ... --capability ... --acceptance ... --expect ...
    python3 -m mvp.board done T2 --rc 0 --note "手动验收说明"
    python3 -m mvp.board block T2 --note "卡在哪"
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "TASKS.md"
STATUSES = ("todo", "doing", "done", "blocked")
OPEN_STATUSES = ("todo", "doing")
MAX_OPEN = 5
COLUMNS = 7
ROW_RE = re.compile(r"^\|\s*(T\d+)\s*\|")


class BoardError(RuntimeError):
    """看板操作被拒：状态非法、超限、验收不通过。"""


@dataclass
class Card:
    id: str
    title: str
    capability: str
    command: str
    expect: str
    status: str
    evidence: str = "—"


@dataclass
class Board:
    path: Path
    cards: list[Card]
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)


def parse(text: str, path: Path = DEFAULT_PATH) -> Board:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines)
                     if line.strip().startswith("| id |"))
    except StopIteration:
        raise BoardError("找不到表头「| id | ...」") from None
    end = start + 2                       # 表头 + 分隔线
    while end < len(lines) and ROW_RE.match(lines[end].strip()):
        end += 1

    cards: list[Card] = []
    for line in lines[start + 2:end]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != COLUMNS:
            raise BoardError(f"列数不对（{len(cells)}≠{COLUMNS}）：{line}")
        card_id, title, capability, command, expect, status, evidence = cells
        if status not in STATUSES:
            raise BoardError(f"{card_id} 状态非法：{status}")
        cards.append(Card(card_id, title, capability,
                          command.strip().strip("`").strip(),
                          expect, status, evidence or "—"))
    return Board(path, cards, lines[:start + 2], lines[end:])


def render(board: Board) -> str:
    rows = []
    for card in board.cards:
        values = [card.id, card.title, card.capability, f"`{card.command}`",
                  card.expect, card.status, card.evidence]
        if any("|" in str(v) or "\n" in str(v) for v in values):
            raise BoardError(f"{card.id} 含非法字符（| 或换行）")
        rows.append("| " + " | ".join(str(v) for v in values) + " |")
    body = board.before + rows + board.after
    return "\n".join(body).rstrip("\n") + "\n"


def load(path: Path = DEFAULT_PATH) -> Board:
    return parse(Path(path).read_text(encoding="utf-8"), Path(path))


def save(board: Board) -> None:
    text = render(board)
    board.path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                     dir=board.path.parent, delete=False) as f:
        f.write(text)
        tmp = Path(f.name)
    tmp.replace(board.path)


def get(board: Board, card_id: str) -> Card:
    for card in board.cards:
        if card.id == card_id:
            return card
    raise BoardError(f"没有这张卡：{card_id}")


def open_cards(board: Board) -> list[Card]:
    return [c for c in board.cards if c.status in OPEN_STATUSES]


def add(board: Board, title: str, capability: str, command: str,
        expect: str) -> Card:
    """新增一张 todo 卡。验收命令必填；未完成数不得超过上限。"""
    if not command.strip():
        raise BoardError("验收命令不能为空")
    if len(open_cards(board)) >= MAX_OPEN:
        raise BoardError(f"未完成任务已达 {MAX_OPEN} 个，先完成或删掉一张")
    next_id = f"T{max(int(c.id[1:]) for c in board.cards) + 1}"
    card = Card(next_id, title, capability, command.strip(), expect, "todo", "—")
    board.cards.append(card)
    save(board)
    return card


def start(board: Board, card_id: str) -> Card:
    """todo/blocked → doing。同时只允许一个 doing。"""
    card = get(board, card_id)
    if card.status == "done":
        raise BoardError(f"{card_id} 已完成，不能重开（要新做请加卡）")
    if any(c.status == "doing" for c in board.cards if c.id != card_id):
        raise BoardError("已有 doing 的卡，先收工再做下一张")
    card.status = "doing"
    card.evidence = "—"
    save(board)
    return card


def finish(board: Board, card_id: str, rc: int, note: str) -> Card:
    """手动收工：必须带证据（rc + 说明）。"""
    if not note.strip():
        raise BoardError("done 必须有证据说明，不能空")
    card = get(board, card_id)
    card.status = "done"
    card.evidence = f"{date.today().isoformat()} rc={rc} {note.strip()}"
    save(board)
    return card


def block(board: Board, card_id: str, note: str) -> Card:
    if not note.strip():
        raise BoardError("blocked 必须写清卡在哪")
    card = get(board, card_id)
    card.status = "blocked"
    card.evidence = f"{date.today().isoformat()} {note.strip()}"
    save(board)
    return card


def expected_rc(expect: str) -> int:
    match = re.search(r"rc=(\d+)", expect)
    return int(match.group(1)) if match else 0


def expected_text(expect: str) -> str:
    match = re.search(r"`([^`]+)`", expect)
    return match.group(1) if match else ""


def run_acceptance(card: Card, timeout: float = 300.0) -> tuple[int, str]:
    proc = subprocess.run(card.command, shell=True, cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def verify(board: Board, card_id: str) -> Card:
    """跑验收命令：rc 与期望输出都对上才 done，否则留 doing 并记证据。"""
    card = get(board, card_id)
    card.status = "doing"
    try:
        rc, output = run_acceptance(card)
    except subprocess.TimeoutExpired:
        card.status = "blocked"
        card.evidence = f"{date.today().isoformat()} 验收超时"
        save(board)
        raise BoardError(f"{card_id} 验收超时") from None
    ok = rc == expected_rc(card.expect)
    needle = expected_text(card.expect)
    if needle and needle not in output:
        ok = False
    if ok:
        card.status = "done"
        card.evidence = f"{date.today().isoformat()} rc={rc} 自动验收通过"
    else:
        card.status = "doing"
        tail = output.strip().splitlines()[-1][:80] if output.strip() else ""
        card.evidence = f"{date.today().isoformat()} rc={rc} 未过：{tail}"
    save(board)
    return card


def kanban(board: Board) -> str:
    groups = {status: [c for c in board.cards if c.status == status]
              for status in STATUSES}
    lines = []
    for status in STATUSES:
        cards = groups[status]
        lines.append(f"## {status} ({len(cards)})")
        for card in cards:
            lines.append(f"- {card.id} {card.title} [{card.capability}]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _show(board: Board) -> None:
    print(f"看板：{board.path}")
    print(kanban(board))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m mvp.board")
    parser.add_argument("--file", default=str(DEFAULT_PATH))
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list")
    p_add = sub.add_parser("add")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--capability", required=True)
    p_add.add_argument("--acceptance", required=True, dest="command")
    p_add.add_argument("--expect", required=True)
    for name in ("start", "verify"):
        p = sub.add_parser(name)
        p.add_argument("id")
    p_done = sub.add_parser("done")
    p_done.add_argument("id")
    p_done.add_argument("--rc", type=int, required=True)
    p_done.add_argument("--note", required=True)
    p_block = sub.add_parser("block")
    p_block.add_argument("id")
    p_block.add_argument("--note", required=True)

    args = parser.parse_args(argv)
    board = load(Path(args.file))
    try:
        if args.cmd in (None, "list"):
            _show(board)
        elif args.cmd == "add":
            card = add(board, args.title, args.capability, args.command, args.expect)
            print(f"新增 {card.id}：{card.title}")
        elif args.cmd == "start":
            card = start(board, args.id)
            print(f"{card.id} → doing：{card.title}")
        elif args.cmd == "verify":
            card = verify(board, args.id)
            print(f"{card.id} → {card.status}")
            print(f"  证据：{card.evidence}")
            print(f"  验收：{card.command}")
        elif args.cmd == "done":
            card = finish(board, args.id, args.rc, args.note)
            print(f"{card.id} → done")
        elif args.cmd == "block":
            card = block(board, args.id, args.note)
            print(f"{card.id} → blocked")
    except BoardError as exc:
        print(f"看板操作被拒：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())