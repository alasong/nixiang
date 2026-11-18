"""文本看板：TASKS.md 的读写与状态机。

一个任务一行，固定 9 列；目标条目单独一张表。状态只取
todo / doing / done / blocked。这个模块负责解析、渲染、推进状态、跑验收、
检查目标覆盖。

借鉴 ralph-goal-tree 的三件事（其余不借，见 docs/board设计_2026-11.md）：
  1. 根目标拆条目、卡用 covers 认领、未认领即缺口（coverage）
  2. 开工前红检：验收已经通过就不许开工（防假任务）
  3. 失败累计：连续 3 次验收不过转 blocked（防无限重试）

不借：依赖树（排序/拓扑）、测试路径冻结、评审角色/nonce。那些是被管理成本
压垮的部分。

用法：
    python3 -m mvp.board                  # 看板视图
    python3 -m mvp.board coverage         # 目标条目有没有被认领全
    python3 -m mvp.board start T2         # todo → doing（先跑红检）
    python3 -m mvp.board verify T2        # 跑验收，过则 done
    python3 -m mvp.board add --title ... --capability ... --acceptance ... --expect ...
"""
from __future__ import annotations

import json
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
MAX_ATTEMPTS = 3
NO_COVER = "—"
CARD_COLUMNS = 9
GOAL_COLUMNS = 2
ROW_RE = re.compile(r"^\|\s*(T\d+)\s*\|")
GOAL_RE = re.compile(r"^\|\s*(g\d+)\s*\|")


class BoardError(RuntimeError):
    """看板操作被拒：状态非法、超限、验收不通过。"""


@dataclass
class Card:
    id: str
    title: str
    capability: str
    covers: str
    command: str
    expect: str
    status: str
    attempts: int
    evidence: str = NO_COVER


@dataclass
class GoalItem:
    gid: str
    text: str


@dataclass
class Board:
    path: Path
    cards: list[Card]
    goals: list[GoalItem]
    before: list[str] = field(default_factory=list)
    between: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)


def _table_span(lines: list[str], header_prefix: str) -> tuple[int, int]:
    """返回 (表头行号, 表体结束行号)。表体 = 表头 + 分隔线之后连续的行。"""
    try:
        start = next(i for i, line in enumerate(lines)
                     if line.strip().startswith(header_prefix))
    except StopIteration:
        raise BoardError(f"找不到表头 {header_prefix!r}") from None
    end = start + 2
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    return start, end


def parse(text: str, path: Path = DEFAULT_PATH) -> Board:
    lines = text.splitlines()
    card_start, card_end = _table_span(lines, "| id |")
    goal_start, goal_end = _table_span(lines, "| gid |")

    cards: list[Card] = []
    for line in lines[card_start + 2:card_end]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != CARD_COLUMNS:
            raise BoardError(f"列数不对（{len(cells)}≠{CARD_COLUMNS}）：{line}")
        cid, title, capability, covers, command, expect, status, attempts, evidence = cells
        if status not in STATUSES:
            raise BoardError(f"{cid} 状态非法：{status}")
        try:
            attempt_count = int(attempts)
        except ValueError:
            raise BoardError(f"{cid} 尝试次数不是整数：{attempts}") from None
        cards.append(Card(cid, title, capability, covers,
                          command.strip().strip("`").strip(),
                          expect, status, attempt_count, evidence or NO_COVER))

    goals: list[GoalItem] = []
    for line in lines[goal_start + 2:goal_end]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != GOAL_COLUMNS:
            raise BoardError(f"目标条目列数不对（{len(cells)}≠{GOAL_COLUMNS}）：{line}")
        goals.append(GoalItem(cells[0], cells[1]))

    return Board(path, cards, goals,
                 lines[:card_start + 2],
                 lines[card_end:goal_start + 2],
                 lines[goal_end:])


def _row(values: list[str]) -> str:
    if any("|" in str(v) or "\n" in str(v) for v in values):
        raise BoardError(f"单元格含非法字符（| 或换行）：{values[0]}")
    return "| " + " | ".join(str(v) for v in values) + " |"


def render(board: Board) -> str:
    rows = [_row([c.id, c.title, c.capability, c.covers, f"`{c.command}`",
                  c.expect, c.status, c.attempts, c.evidence])
            for c in board.cards]
    goal_rows = [_row([g.gid, g.text]) for g in board.goals]
    body = board.before + rows + board.between + goal_rows + board.after
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


def goal_ids(board: Board) -> set[str]:
    return {g.gid for g in board.goals}


def split_covers(covers: str) -> list[str]:
    return [c.strip() for c in covers.split(",")
            if c.strip() and c.strip() != NO_COVER]


def add(board: Board, title: str, capability: str, command: str,
        expect: str, covers: str = NO_COVER) -> Card:
    if not command.strip():
        raise BoardError("验收命令不能为空")
    unknown = sorted(set(split_covers(covers)) - goal_ids(board))
    if unknown:
        raise BoardError(f"覆盖了未声明的目标条目：{unknown}")
    if len(open_cards(board)) >= MAX_OPEN:
        raise BoardError(f"未完成任务已达 {MAX_OPEN} 个，先完成或删掉一张")
    next_id = f"T{max(int(c.id[1:]) for c in board.cards) + 1}"
    card = Card(next_id, title, capability, covers, command.strip(),
                expect, "todo", 0, NO_COVER)
    board.cards.append(card)
    save(board)
    return card


def start(board: Board, card_id: str) -> Card:
    """todo/blocked → doing。开工前红检：验收已通过的不许开工。"""
    card = get(board, card_id)
    if card.status == "done":
        raise BoardError(f"{card_id} 已完成，不能重开（要新做请加卡）")
    if any(c.status == "doing" for c in board.cards if c.id != card_id):
        raise BoardError("已有 doing 的卡，先收工再做下一张")
    if acceptance_passes(card):
        raise BoardError(
            f"{card_id} 验收已经通过，没有可做的活——先改验收或删卡")
    card.status = "doing"
    card.evidence = NO_COVER
    save(board)
    return card


def finish(board: Board, card_id: str, rc: int, note: str) -> Card:
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


def acceptance_passes(card: Card, timeout: float = 60.0) -> bool:
    try:
        rc, output = run_acceptance(card, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    if rc != expected_rc(card.expect):
        return False
    needle = expected_text(card.expect)
    return not needle or needle in output


def verify(board: Board, card_id: str) -> Card:
    """跑验收：过则 done；不过留 doing 并累计尝试，满 3 次转 blocked。"""
    card = get(board, card_id)
    card.status = "doing"
    try:
        rc, output = run_acceptance(card)
    except subprocess.TimeoutExpired:
        card.attempts += 1
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
        card.attempts += 1
        tail = output.strip().splitlines()[-1][:80] if output.strip() else ""
        if card.attempts >= MAX_ATTEMPTS:
            card.status = "blocked"
            card.evidence = (f"{date.today().isoformat()} rc={rc} "
                             f"连续 {card.attempts} 次未过：{tail}")
        else:
            card.evidence = (f"{date.today().isoformat()} rc={rc} "
                             f"第 {card.attempts} 次未过：{tail}")
    save(board)
    return card


def coverage(board: Board) -> dict:
    """目标条目有没有被认领；全部认领且全部卡片 done 才算 goal_done。"""
    claimed: dict[str, list[str]] = {g.gid: [] for g in board.goals}
    for card in board.cards:
        for gid in split_covers(card.covers):
            if gid in claimed:
                claimed[gid].append(card.id)
    gaps = [gid for gid, ids in claimed.items() if not ids]
    items_done = [gid for gid, ids in claimed.items()
                  if ids and all(get(board, cid).status == "done" for cid in ids)]
    return {
        "items": claimed,
        "gaps": gaps,
        "ok": not gaps,
        "items_done": items_done,
        "goal_done": not gaps and len(items_done) == len(claimed) and bool(claimed),
    }


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


def _show_coverage(board: Board, as_json: bool) -> int:
    result = coverage(board)
    if as_json:
        print(json.dumps({"gaps": result["gaps"], "ok": result["ok"],
                          "items_done": result["items_done"],
                          "goal_done": result["goal_done"],
                          "items": result["items"]}, ensure_ascii=False))
        return 0 if result["ok"] else 1
    print(f"目标条目 {len(board.goals)} 个，缺口 {len(result['gaps'])} 个，"
          f"已完成 {len(result['items_done'])} 个，"
          f"根目标{'完成' if result['goal_done'] else '未完成'}")
    for goal in board.goals:
        cards = result["items"][goal.gid]
        mark = "✓" if goal.gid in result["items_done"] else ("·" if cards else "✗")
        print(f"  {mark} {goal.gid} {goal.text}  ← {', '.join(cards) or '无人认领'}")
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m mvp.board")
    parser.add_argument("--file", default=str(DEFAULT_PATH))
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list")
    p_cov = sub.add_parser("coverage")
    p_cov.add_argument("--json", action="store_true")
    p_add = sub.add_parser("add")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--capability", required=True)
    p_add.add_argument("--acceptance", required=True, dest="command")
    p_add.add_argument("--expect", required=True)
    p_add.add_argument("--covers", default=NO_COVER)
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
        elif args.cmd == "coverage":
            return _show_coverage(board, args.json)
        elif args.cmd == "add":
            card = add(board, args.title, args.capability, args.command,
                       args.expect, covers=args.covers)
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