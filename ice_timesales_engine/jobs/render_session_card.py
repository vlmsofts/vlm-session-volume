"""
render_session_card.py -- the same-day session card, as a PNG.

WHAT THIS IS. A client-facing point-in-time artifact. Every number on it is
reproducible from the store, and every number carries the basis it was measured
on. Nothing here writes to the database.

THE LABELLING RULES, all of them load-bearing:

  TAPE BASIS       the header total is TAPE volume, not total exchange volume.
                   TAS and TIC are a separate ICE product and are absent from
                   the blotter by construction; uncaptured blocks may also be
                   missing. Stated on the card. NO fixed percentage is quoted:
                   it moves, and TAS is a roll instrument so it may move most
                   during the roll. Say what the figure IS.

  SESSION LABEL    trade date AND the literal window, always, in one block. A
                   trade date alone is ambiguous because the session opens the
                   prior evening. The window is read from the tape, so a Sunday
                   open or a holiday-shortened session prints its real hours.

  RENDER STAMP     a same-day artifact is a point-in-time claim in a way a T+1
                   one never was. Stamped in ET.

  COMPLETENESS     the verdict goes ON THE ARTIFACT, not only in a log. This is
                   the 2026-08-19 case: a 1,500-lot block went missing and
                   nothing in the file said so. A flag nobody reads is the same
                   as no gate, and a check must bind to the thing that ships.

  FILENAME         one session can produce two renders, provisional before
                   settle and final after. They must not sit in a folder
                   looking identical. Clients read the FILENAME to decide
                   whether to open the image, so it is a deliverable.

House style: VLM palette, Arial, never monospace, no em dashes in emitted text.
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                # noqa: E402
from store import session_render as sr                       # noqa: E402
from store.db import connect                                 # noqa: E402

# VLM master palette.
INK = '#1b1b1b'
MUTED = '#6a6a6a'
RULE = '#d8d8d8'
PAPER = '#ffffff'
GOLD = '#c9a227'
GREEN = '#2e7d51'
RED = '#b3402f'
FONT = 'Arial'


def completeness(db, commodity, session_date):
    """Tape vs settle volume, computed IN THIS REPO.

    No cross-repo read: the engine already holds the tape side, and the settle
    side is a file read it can do itself (ingest.discover + ingest.reconcile).
    Depending on another tree being present is the same objection that ruled
    out importing the analyzer.

    The tape side is CLEAN per R11 (cancelled prints never count) because that
    is the basis ICE's own volume uses. Returns a verdict dict; 'unknown' when
    no settle file exists, stated rather than left blank.
    """
    from ingest.discover import settle_path
    from ingest.reconcile import _read_settle_volumes
    from pathlib import Path

    p = settle_path(commodity, session_date)
    if p is None:
        return {'verdict': 'UNKNOWN', 'vintage': 'none', 'rows': [],
                'note': 'no settle file for this session yet'}
    settle, vintage = _read_settle_volumes(Path(p), commodity)

    from ingest.classifier import excluded_sql
    ex, exp = excluded_sql()
    tape = dict(db.q(
        'SELECT ice_code, SUM(size) FROM ticks WHERE commodity=%s '
        'AND session_date=%s' + ex + ' GROUP BY ice_code',
        [commodity.upper(), session_date] + exp))

    rows = []
    for ice in sorted(set(tape) | set(settle)):
        t = float(tape.get(ice, 0.0))
        s = settle.get(ice)
        if s is None or s <= 0:
            rows.append((ice, t, None, None))
            continue
        rows.append((ice, t, float(s), t - float(s)))

    # A pre-cutover settle file carries the PRIOR session's volume, so a
    # comparison against it is not a same-day check. Say so rather than
    # printing a verdict that looks stronger than it is.
    if vintage == 'prior_session':
        # A pre-cutover settle file carries the PRIOR session's volume, so
        # comparing against it would be comparing across sessions. Say that,
        # and say WHY the check is unavailable rather than leaving a dead end:
        # a verdict with no number tells a reader nothing they can act on.
        return {'verdict': 'NOT YET CHECKED', 'vintage': vintage,
                'rows': rows,
                'note': 'the settle file for this session predates the '
                        'CumVolume change, so its volume figure belongs to '
                        'the previous session and cannot be compared'}
    flagged = [r for r in rows if r[3] is not None and abs(r[3]) > 0.5]
    if not rows:
        return {'verdict': 'UNKNOWN', 'vintage': vintage, 'rows': rows,
                'note': 'no contracts to compare'}
    if flagged:
        return {'verdict': 'FLAG', 'vintage': vintage, 'rows': rows,
                'note': '; '.join(f'{i} {d:+,.0f} lots' for i, _t, _s, d
                                  in flagged)}
    return {'verdict': 'OK', 'vintage': vintage, 'rows': rows,
            'note': 'tape matches settle volume on every contract'}


def _fmt(n, dp=0):
    return f'{n:,.{dp}f}' if n is not None else 'n/a'


def build(commodity, session_date, ice_code, out_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    db = connect(config.DATABASE_URL, read_only=True)
    try:
        win = sr.session_window(db, commodity, session_date)
        agg = sr.aggressor_split(db, commodity, session_date, ice_code)
        nd = sr.aggressor_by_window(db, commodity, session_date, ice_code)
        types = sr.type_breakdown(db, commodity, session_date, ice_code)
        blanks = sr.blank_note(db, commodity, session_date, ice_code)
        comp = completeness(db, commodity, session_date)
    finally:
        db.close()

    tape_total = sum(v['lots'] for v in types.values())
    now = datetime.now()
    d = datetime.strptime(session_date, '%Y-%m-%d')

    fig = plt.figure(figsize=(11.0, 8.5), dpi=150, facecolor=PAPER)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)

    def txt(x, y, s, size=10, color=INK, weight='normal', ha='left'):
        ax.text(x, y, s, fontsize=size, color=color, family=FONT,
                fontweight=weight, ha=ha, va='top')

    def rule(y, x0=6, x1=94):
        ax.plot([x0, x1], [y, y], color=RULE, lw=0.8)

    # ---- header ---------------------------------------------------------
    txt(6, 96, f'{commodity.upper()} {ice_code}  SESSION VOLUME', 19, INK,
        'bold')
    txt(6, 91.5, f'SESSION {session_date} ({d.strftime("%a")})', 11.5, INK,
        'bold')
    ws, we = win['window_start'], win['window_end']
    txt(6, 88.4, f'{ws[:10]} {ws[11:16]} ET through {we[:10]} {we[11:16]} ET',
        10, MUTED)
    txt(94, 96, f'Rendered {now.strftime("%Y-%m-%d %H:%M")} ET', 9, MUTED,
        ha='right')
    stage = 'FINAL' if comp['verdict'] in ('OK', 'FLAG') else 'PROVISIONAL'
    txt(94, 92.8, stage, 10, GOLD, 'bold', ha='right')
    rule(86.5)

    # ---- headline -------------------------------------------------------
    txt(6, 84, 'TAPE VOLUME', 10, MUTED, 'bold')
    txt(6, 79.5, _fmt(tape_total), 30, INK, 'bold')
    txt(6, 70.5,
        'Tape volume from the ICE time and sales blotter. This is not total '
        'exchange volume: TAS and TIC are a separate ICE product and do not '
        'appear on this tape,', 8.6, MUTED)
    txt(6, 68.2, 'and any block not captured is also absent. Compare with an '
                 'exchange figure only on that understanding.', 8.6, MUTED)
    rule(65.5)

    # ---- aggressor ------------------------------------------------------
    txt(6, 63, 'AGGRESSOR', 10, MUTED, 'bold')
    txt(6, 59.8, f'Base {_fmt(agg["base_lots"])} lots. Aggressor tagged '
                 f'outrights only: EFS, EFP, cancelled, spread legs and '
                 f'unstamped fills are all excluded.', 8.6, MUTED)
    y = 55.5
    txt(6, y, 'Side', 9, MUTED, 'bold')
    txt(26, y, 'Lots', 9, MUTED, 'bold', ha='right')
    txt(38, y, 'Share', 9, MUTED, 'bold', ha='right')
    txt(50, y, 'Clip', 9, MUTED, 'bold', ha='right')
    y -= 3.4
    for label, key, col in (('Buy', 'buy', GREEN), ('Sell', 'sell', RED)):
        dd = agg[key]
        txt(6, y, label, 10, col, 'bold')
        txt(26, y, _fmt(dd['lots']), 10, INK, ha='right')
        txt(38, y, f'{dd["pct_of_base"]:.1f}%' if dd['pct_of_base'] is not None
            else 'n/a', 10, INK, ha='right')
        txt(50, y, f'{dd["clip"]:.2f}' if dd['clip'] else 'n/a', 10, INK,
            ha='right')
        y -= 3.4

    # night / day, the flip a daily bar hides
    txt(58, 55.5, 'By window', 9, MUTED, 'bold')
    yy = 52.1
    for w in ('night', 'day'):
        if w in nd and nd[w]['base_lots']:
            x = nd[w]
            name = 'Overnight' if w == 'night' else 'Day session'
            txt(58, yy, f'{name}: {x["buy_pct"]:.1f}% buy, '
                        f'{x["sell_pct"]:.1f}% sell', 9.5, INK)
            yy -= 3.2
    rule(44)

    # ---- trade types ----------------------------------------------------
    txt(6, 41.5, 'TRADE TYPES', 10, MUTED, 'bold')
    txt(26, 38.6, 'Lots', 9, MUTED, 'bold', ha='right')
    txt(38, 38.6, 'Share', 9, MUTED, 'bold', ha='right')
    txt(50, 38.6, 'Prints', 9, MUTED, 'bold', ha='right')
    y = 35.0
    order = sorted(types.items(), key=lambda kv: -kv[1]['lots'])
    names = {'outright': 'Outright', 'spread_leg': 'Spread legs',
             'option_hedge': 'Option hedges', 'efs': 'EFS', 'efp': 'EFP',
             'block': 'Block', 'other': 'Other'}
    for t, v in order:
        if v['lots'] <= 0:
            continue
        label = names.get(t, t.replace('_', ' ').title())
        txt(6, y, label, 9.6, INK)
        txt(26, y, _fmt(v['lots']), 9.6, INK, ha='right')
        txt(38, y, f'{100.0*v["lots"]/tape_total:.1f}%', 9.6, MUTED,
            ha='right')
        txt(50, y, _fmt(v['prints']), 9.6, MUTED, ha='right')
        y -= 3.2

    txt(58, 41.5, 'How the leg split is defined', 9, MUTED, 'bold')
    txt(58, 38.4, 'A spread leg is a leg print with a same second, same size '
                  'leg', 8.4, MUTED)
    txt(58, 36.2, 'partner on another contract in the same commodity. An '
                  'option', 8.4, MUTED)
    txt(58, 34.0, 'hedge is a leg print without one.', 8.4, MUTED)

    # ---- blanks ---------------------------------------------------------
    txt(58, 29.5, 'Unstamped fills', 9, MUTED, 'bold')
    txt(58, 26.3, f'{_fmt(blanks["lots"])} lots across '
                  f'{_fmt(blanks["prints"])} prints carry no aggressor stamp.',
        8.4, MUTED)
    txt(58, 24.1, 'The evidence is that these are implied calendar spread '
                  'fills.', 8.4, MUTED)
    txt(58, 21.9, 'They are included in the tape volume above and excluded '
                  'from', 8.4, MUTED)
    txt(58, 19.7, 'the aggressor base. No side is assigned to them.',
        8.4, MUTED)
    rule(15.5)

    # ---- completeness ---------------------------------------------------
    vcol = {'OK': GREEN, 'FLAG': RED}.get(comp['verdict'], MUTED)
    txt(6, 13, 'COMPLETENESS', 10, MUTED, 'bold')
    txt(6, 9.6, comp['verdict'], 13, vcol, 'bold')
    note = comp['note']
    if len(note) > 78:
        cut = note.rfind(' ', 0, 78)
        txt(30, 9.9, note[:cut], 8.8, MUTED)
        txt(30, 7.7, note[cut + 1:][:88], 8.8, MUTED)
    else:
        txt(30, 9.9, note, 8.8, MUTED)
    txt(6, 4.6, 'Tape total compared with the exchange settle volume per '
                'contract, cancelled prints excluded from both.', 8.2, MUTED)
    txt(94, 4.6, 'VLM Commodities', 8.6, MUTED, ha='right')

    os.makedirs(out_dir, exist_ok=True)
    fn = (f'{commodity.upper()}_{ice_code}_{session_date}_'
          f'{stage.lower()}_{now.strftime("%H%M")}ET.png')
    path = os.path.join(out_dir, fn)
    fig.savefig(path, facecolor=PAPER)
    plt.close(fig)
    return path, {'tape_total': tape_total, 'agg': agg, 'types': types,
                  'comp': comp, 'window': win, 'blanks': blanks}


def main():
    ap = argparse.ArgumentParser(description='Render a same-day session card.')
    ap.add_argument('--commodity', default='CT')
    ap.add_argument('--date', required=True)
    ap.add_argument('--ice-code', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(config.LOG_DIR, '..', 'renders')
    path, data = build(a.commodity, a.date, a.ice_code, os.path.abspath(out))
    print(f'wrote {path}')
    print(f"  tape total {data['tape_total']:,.0f}  "
          f"verdict {data['comp']['verdict']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
