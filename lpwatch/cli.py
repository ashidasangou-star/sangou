"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from . import agenda, extract, notify, remind, store
from .collect.manual import ManualCollector
from .model import TYPE_LABEL, Reception, now_jst

DEFAULT_ROOT = Path("data/lpwatch")


def _width(s: str) -> int:
    """端末上の表示幅。全角文字を2桁として数える。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _ljust(s: str, n: int) -> str:
    return s + " " * max(0, n - _width(s))


def _clip(s: str, n: int) -> str:
    out = ""
    for c in s:
        if _width(out + c) > n:
            return out + "…"
        out += c
    return out


def _sort_key(r: Reception) -> tuple[int, str]:
    """締切が近い順。締切不明は末尾に置く（推測で順位を付けない）。"""
    return (1, "") if not r.closes_at else (0, r.closes_at)


def cmd_collect(args: argparse.Namespace) -> int:
    root = Path(args.root)
    collector = ManualCollector(root / "inbox")
    pending = collector.pending()
    if not pending:
        print("未処理の告知はありません。")
        return 0

    found = collector.collect(archive=not args.keep)
    fresh = store.append_events(root, found)
    print(f"{len(pending)}件のファイルから {len(found)}件の受付を読み、{len(fresh)}件を新規記録しました。")
    for r in sorted(fresh, key=_sort_key):
        print(f"  + [{TYPE_LABEL[r.reception_type]}] {r.title or r.event_id}")
        if not r.closes_at:
            print("    締切が読み取れませんでした。LivePocketのページで確認してください。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root)
    events = store.load_events(root)
    if not events:
        print("記録がありません。")
        return 0

    marks = store.load_marks(root)
    now = now_jst()
    rows = sorted(events.values(), key=_sort_key)
    if not args.all:
        rows = [
            r
            for r in rows
            if not r.has_ended(now) and "applied" not in marks.get(r.key, set())
        ]
        if not rows:
            print("これからの受付はありません。--all で全件を表示します。")
            return 0

    for r in rows:
        got = marks.get(r.key, set())
        if "applied" in got:
            state = "申込済"
        elif "skipped" in got:
            state = "見送"
        elif r.has_ended(now):
            state = "終了"
        elif not r.is_open(now):
            state = "開始前"
        else:
            state = "受付中"
        closes = notify._jp(r.closes_at) if r.closes_at else "締切不明"
        print(
            f"{_ljust(TYPE_LABEL[r.reception_type], 8)} "
            f"{_ljust(closes, 20)} {_ljust(state, 6)} {_clip(r.title or r.event_id, 40)}"
        )
        print(f"  {r.url}")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    root = Path(args.root)
    events = store.load_events(root)
    marks = store.load_marks(root)
    now = now_jst()

    pending = remind.due(events, marks, now)
    if not pending:
        print("通知すべきものはありません。")
        return 0

    messages = [notify.Message(reception=r, reason=reason) for r, _, reason in pending]
    notify.to_stdout(messages)

    if args.dry_run:
        print(f"\n(dry-run: {len(messages)}件。送信も記録もしていません)", file=sys.stderr)
        return 0

    if args.webhook:
        err = notify.to_webhook(args.webhook, messages)
        if err:
            # 送信できなかったものに印を付けない。次回もう一度通知させる。
            print(f"通知の送信に失敗: {err}", file=sys.stderr)
            return 1

    for r, mark, _ in pending:
        store.add_mark(root, r.key, mark)
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    root = Path(args.root)
    events = store.load_events(root)
    matches = [r for r in events.values() if args.event in r.key or args.event in (r.title or "")]
    if not matches:
        print(f"該当する受付がありません: {args.event}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print("候補が複数あります。event_id で指定してください:", file=sys.stderr)
        for r in matches:
            print(f"  {r.key}  {r.title}", file=sys.stderr)
        return 1

    r = matches[0]
    store.add_mark(root, r.key, args.mark, note=args.note)
    print(f"{r.title or r.event_id} を {args.mark} にしました。")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    """告知テキストの抽出結果を確認する（正規表現の当たりを見る用）。"""
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    found = extract.extract(text, source=args.file or "stdin")
    if not found:
        print("LivePocketのイベントURLが見つかりませんでした。", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([r.to_dict() for r in found], ensure_ascii=False, indent=2))
        return 0
    for r in found:
        print(f"[{TYPE_LABEL[r.reception_type]}] {r.title or r.event_id}")
        print(f"  URL:  {r.url}")
        print(f"  開始: {notify._jp(r.opens_at) if r.opens_at else '(読めず)'}")
        print(f"  締切: {notify._jp(r.closes_at) if r.closes_at else '(読めず)'}")
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    """締切が読めていない受付を、LivePocket の公開ページで埋める。

    ログイン不要なのでこれは Actions 側で回す。X の投稿から URL だけ
    拾えた受付に、一次情報から期間を入れ直すのが主な仕事。
    """
    from .collect.livepocket import LivePocketCollector

    root = Path(args.root)
    events = store.load_events(root)
    if args.all:
        ids = [r.event_id for r in events.values()]
    else:
        ids = [r.event_id for r in events.values() if not r.closes_at]
    ids = sorted(set(ids))
    if not ids:
        print("取り直す受付はありません。")
        return 0

    found = LivePocketCollector(ids).collect()
    fresh = store.append_events(root, found)
    print(f"{len(ids)}件を照会し、{len(found)}件から情報を取得、{len(fresh)}件を更新しました。")
    for r in fresh:
        print(f"  ~ {r.title or r.event_id}: 締切 {notify._jp(r.closes_at) if r.closes_at else '不明のまま'}")
    return 0


def cmd_agenda(args: argparse.Namespace) -> int:
    """Windows 側で実行すべき申込を一覧する。"""
    root = Path(args.root)
    targets = agenda.load_targets(root)
    if not targets:
        print("自動申込の対象が設定されていません（data/lpwatch/targets.json）。")
        return 0

    jobs = agenda.pending_jobs(store.load_events(root), store.load_marks(root), targets, now_jst())
    if not jobs:
        print("いま実行すべき申込はありません。")
        return 0
    for j in jobs:
        r = j.reception
        print(f"[{TYPE_LABEL[r.reception_type]}] {r.title or r.event_id}  {j.reason}")
        print(f"  {r.url}  枚数{j.target.quantity} 上限{j.target.max_price_jpy or 'なし'}")
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    """PC を起こすべき時刻を出す。PowerShell がこれを読んでタスクを登録する。

    先着受付の開始時刻だけが対象。抽選は締切が数日先で、定期ポーリングの
    窓で拾えるため専用の起床を要さない。
    """
    root = Path(args.root)
    times = agenda.wake_times(
        store.load_events(root), store.load_marks(root), agenda.load_targets(root), now_jst()
    )
    if args.json:
        print(json.dumps(
            [{"at": t.isoformat(), "event_id": r.event_id, "title": r.title} for t, r in times],
            ensure_ascii=False, indent=2,
        ))
        return 0
    if not times:
        print("予約すべき起床時刻はありません。")
        return 0
    for t, r in times:
        print(f"{t:%Y-%m-%d %H:%M}  {r.title or r.event_id}")
    return 0


def cmd_local_login(args: argparse.Namespace) -> int:
    from .local import session

    state = session.login(args.site)
    print(state.detail)
    return 0 if state.logged_in else 1


def cmd_local_check(args: argparse.Namespace) -> int:
    from .local import session
    from .local.browser import SITES

    sites = [args.site] if args.site else list(SITES)
    ok = True
    for site in sites:
        state = session.check(site)
        print(f"{site}: {'OK' if state.logged_in else 'NG'}  {state.detail}")
        ok = ok and state.logged_in
    if not ok:
        print("\n再ログインが必要です: python -m lpwatch local login <site>", file=sys.stderr)
    return 0 if ok else 1


def cmd_local_run(args: argparse.Namespace) -> int:
    """Windows 側の本体。申込を実行する。

    実際に送信するには --execute が要る。既定が dry-run なのは、
    セレクタの較正が済む前や、タスクスケジューラの設定を誤って
    意図せず起動したときに、金が動かないようにするため。
    """
    from .local import apply as apply_mod
    from .local import session

    root = Path(args.root)
    targets = agenda.load_targets(root)
    if not targets:
        print("自動申込の対象が設定されていません。何もしません。")
        return 0

    jobs = agenda.pending_jobs(store.load_events(root), store.load_marks(root), targets, now_jst())
    if not jobs:
        print("いま実行すべき申込はありません。")
        return 0

    # 先にセッションを確かめる。切れたまま進むと、ログイン画面を
    # 申込フォームと誤認して操作しかねない。
    state = session.check("livepocket")
    if not state.logged_in:
        print(f"LivePocket にログインできていません: {state.detail}", file=sys.stderr)
        print("python -m lpwatch local login livepocket", file=sys.stderr)
        return 1

    selectors = apply_mod.load_selectors(root)
    failures = 0
    for j in jobs:
        r = j.reception
        print(f"→ {r.title or r.event_id} ({j.reason})")
        result = apply_mod.apply(j, selectors, dry_run=not args.execute)
        print(f"   {result.outcome}: {result.detail}")
        for path in result.evidence:
            print(f"   証跡: {path}")

        if result.outcome == "dry_run":
            continue  # dry-run では印を付けない。何度でもやり直せる状態を保つ
        store.add_mark(root, r.key, result.mark, note=f"{result.outcome}: {result.detail}")
        if result.outcome != "applied":
            failures += 1
            if args.webhook:
                notify.to_webhook(args.webhook, [notify.Message(reception=r, reason=f"申込に失敗: {result.detail}")])
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="lpwatch", description="LivePocketの抽選・先着受付を検知して締切前に通知する"
    )
    p.add_argument("--root", default=str(DEFAULT_ROOT), help="記録の置き場所")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_c = sub.add_parser("collect", help="inbox の告知テキストを取り込む")
    p_c.add_argument("--keep", action="store_true", help="読んだファイルを done/ に移さない")
    p_c.set_defaults(func=cmd_collect)

    p_l = sub.add_parser("list", help="受付を締切順に一覧する")
    p_l.add_argument("--all", action="store_true", help="終了・申込済みも含めて出す")
    p_l.set_defaults(func=cmd_list)

    p_n = sub.add_parser("notify", help="通知すべき受付を出す")
    p_n.add_argument("--webhook", default="", help="送信先 Webhook URL")
    p_n.add_argument("--dry-run", action="store_true", help="送信も記録もせず内容だけ見る")
    p_n.set_defaults(func=cmd_notify)

    p_m = sub.add_parser("mark", help="申込済み・見送りの印を付ける")
    p_m.add_argument("event", help="event_id かタイトルの一部")
    p_m.add_argument("mark", choices=store.MARKS)
    p_m.add_argument("--note", default="")
    p_m.set_defaults(func=cmd_mark)

    p_r = sub.add_parser("refresh", help="LivePocketの公開ページで受付情報を取り直す")
    p_r.add_argument("--all", action="store_true", help="締切が判明済みのものも取り直す")
    p_r.set_defaults(func=cmd_refresh)

    p_a = sub.add_parser("agenda", help="Windows側で実行すべき申込を一覧する")
    p_a.set_defaults(func=cmd_agenda)

    p_w = sub.add_parser("wake", help="PCを起こすべき時刻を出す")
    p_w.add_argument("--json", action="store_true")
    p_w.set_defaults(func=cmd_wake)

    p_lo = sub.add_parser("local", help="Windows側の処理（ログインが要るもの）")
    lo = p_lo.add_subparsers(dest="local_cmd", required=True)

    lo_login = lo.add_parser("login", help="ブラウザを開いて手でログインする")
    lo_login.add_argument("site", choices=["x", "livepocket"])
    lo_login.set_defaults(func=cmd_local_login)

    lo_check = lo.add_parser("check", help="ログイン状態を確認する")
    lo_check.add_argument("site", nargs="?", choices=["x", "livepocket"])
    lo_check.set_defaults(func=cmd_local_check)

    lo_run = lo.add_parser("run", help="申込を実行する（既定はdry-run）")
    lo_run.add_argument("--execute", action="store_true", help="実際に送信する")
    lo_run.add_argument("--webhook", default="", help="失敗時の通知先")
    lo_run.set_defaults(func=cmd_local_run)

    p_p = sub.add_parser("parse", help="告知テキストの抽出結果を確認する")
    p_p.add_argument("file", nargs="?", help="省略時は標準入力")
    p_p.add_argument("--json", action="store_true")
    p_p.set_defaults(func=cmd_parse)

    args = p.parse_args(argv)
    return args.func(args)
