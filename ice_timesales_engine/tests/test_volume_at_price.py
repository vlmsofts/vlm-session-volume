"""
VOLUME AT PRICE -- aggressor split bucketed by price level.

Reads ticks directly (minute_agg/bar5m carry no price column). Bucketing and
GROUP BY both happen in SQL -- these tests exist to prove that stays true
(conservation across every price level, side resolved through the ONE vendored
mapping, never a second copy) and that the honest-absence reporting (dates
with zero tick rows for the requested contract) actually fires.

Synthetic ticks only -- no production path, no real blotter, no ICE call.
"""

import pytest

from store import repository as repo

CMD = 'CT'
SESS = '2026-07-10'
ICE = 'CTZ6'
START = f'{SESS}T00:00:00'
END = f'{SESS}T23:59:59'

# Distinct prices and sizes so a wrong bucket or a wrong side shows as a
# unique, unmistakable number. Two prices, each with a buy print, a sell
# print and an unstamped (unsided) print -- enough to prove the bucket AND
# the side both resolve correctly, independently.
TICKS = [
    # price 87.90 bucket
    (f'{SESS}T09:00:00', 87.90, 100.0, 'SetByBid'),   # BUY
    (f'{SESS}T09:00:05', 87.90, 40.0, 'SetByAsk'),     # SELL
    (f'{SESS}T09:00:10', 87.90, 7.0, ''),               # UNSIDED (unstamped)
    # price 88.05 bucket -- a non-outright print here must NEVER surface a
    # side and must NEVER be counted (this function is outright-only by
    # construction, same base as session_render.aggressor_split).
    (f'{SESS}T09:05:00', 88.05, 200.0, 'SetByBid'),    # BUY
    (f'{SESS}T09:05:05', 88.05, 300.0, 'SetByAsk'),    # SELL
    (f'{SESS}T09:05:10', 88.05, 50.0, 'Leg'),           # leg, NOT outright -- excluded entirely
]

BUY_LOTS = 100.0 + 200.0    # 300
SELL_LOTS = 40.0 + 300.0    # 340
UNSIDED_LOTS = 7.0
OUTRIGHT_TOTAL = BUY_LOTS + SELL_LOTS + UNSIDED_LOTS   # 647


def _load(db, ticks=TICKS, ice=ICE):
    from ingest.classifier import primary_type, tokenize
    rows = [(CMD, SESS, ice, 'CTDEC1', ts, price, size,
             primary_type(tokenize(cond)), cond, 2000 + i, 'day',
             f'{SESS}T00:00:00')
            for i, (ts, price, size, cond) in enumerate(ticks)]
    db.execmany(
        'INSERT INTO ticks (commodity, session_date, ice_code, generic_code,'
        ' exchange_time, price, size, primary_type, conditions_raw, seq_num,'
        ' window_preset, ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        rows)
    db.commit()
    return db


@pytest.fixture
def db(tmp_db):
    return _load(tmp_db)


class TestVolumeAtPrice:
    def test_totals_match_the_outright_base(self, db):
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        assert out['buy']['lots'] == BUY_LOTS
        assert out['sell']['lots'] == SELL_LOTS
        assert out['unsided']['lots'] == UNSIDED_LOTS
        assert out['outright_total'] == OUTRIGHT_TOTAL

    def test_leg_print_never_reaches_a_price_level(self, db):
        """The 50-lot Leg print at 88.05 is not an outright -- it must not
        appear in any level's totals, sided or unsided."""
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        total_all_sides = sum(
            lv['buy']['lots'] + lv['sell']['lots'] + lv['unsided']['lots']
            for lv in out['levels'])
        assert total_all_sides == OUTRIGHT_TOTAL   # not +50

    def test_price_levels_split_correctly(self, db):
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        by_price = {lv['price']: lv for lv in out['levels']}
        assert by_price[87.90]['buy']['lots'] == 100.0
        assert by_price[87.90]['sell']['lots'] == 40.0
        assert by_price[87.90]['unsided']['lots'] == 7.0
        assert by_price[88.05]['buy']['lots'] == 200.0
        assert by_price[88.05]['sell']['lots'] == 300.0
        assert by_price[88.05]['unsided']['lots'] == 0.0

    def test_conservation_across_every_price_level(self, db):
        """Summed across every price bucket, buy + sell + unsided equals the
        outright total for the same selection -- same standard as
        session_render.aggressor_split / repo.side_profile."""
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        summed = sum(
            lv['buy']['lots'] + lv['sell']['lots'] + lv['unsided']['lots']
            for lv in out['levels'])
        assert abs(summed - out['outright_total']) < 0.0001

    def test_per_level_carries_lots_only_not_clip_or_prints(self, db):
        """RULING (Lou, 2026-08-24): clip restates lots on liquid buckets
        (20/20 divergences >=1.5x agree with the lots-heavier side) and is
        noise below that threshold -- dropped from the per-level shape. See
        the CLIP AT THE PRICE LEVEL note in repo.volume_at_price()."""
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        by_price = {lv['price']: lv for lv in out['levels']}
        assert set(by_price[87.90]['buy'].keys()) == {'lots'}
        assert set(by_price[88.05]['sell'].keys()) == {'lots'}

    def test_session_totals_still_carry_clip(self, db):
        """Clip stays at session level -- a character summary, a different
        job from a per-level signal, and the original observation that
        started this workstream."""
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        assert out['buy']['clip'] == BUY_LOTS / 2      # 2 buy prints total
        assert out['sell']['clip'] == SELL_LOTS / 2    # 2 sell prints total

    def test_vwap_is_size_weighted_across_outrights_only(self, db):
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        # (87.90*100 + 87.90*40 + 87.90*7 + 88.05*200 + 88.05*300) / 647
        expected = (87.90*100 + 87.90*40 + 87.90*7 + 88.05*200 + 88.05*300) / OUTRIGHT_TOTAL
        assert abs(out['vwap'] - expected) < 0.0001

    def test_coarser_interval_folds_levels_together(self, db):
        """At interval=1.00, 87.90 and 88.05 both fold to the same bucket --
        totals must still conserve, just at fewer, wider levels."""
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=1.00)
        assert len(out['levels']) == 1
        lv = out['levels'][0]
        assert lv['buy']['lots'] == BUY_LOTS
        assert lv['sell']['lots'] == SELL_LOTS
        assert lv['unsided']['lots'] == UNSIDED_LOTS

    def test_unsided_pct_of_base_concept_does_not_apply_here(self, db):
        """volume_at_price returns lots/prints/clip per side, no pct_of_base
        field (unlike side_profile) -- ICE's header shows lots and trade
        count per side, not a base percentage, per the ordered layout."""
        out = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        assert set(out['buy'].keys()) == {'lots', 'prints', 'clip'}

    def test_night_day_preset_narrows_via_window_preset_column(self, db):
        """Both synthetic ticks are stamped window_preset='day' -- a night
        filter must return an empty, honestly-zero result, not an error."""
        day = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01,
                                   preset='day')
        night = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01,
                                     preset='night')
        assert day['outright_total'] == OUTRIGHT_TOTAL
        assert night['outright_total'] == 0.0
        assert night['levels'] == []

    def test_empty_range_returns_honest_zeros(self, db):
        out = repo.volume_at_price(db, CMD, ICE, '1999-01-01', '1999-01-01',
                                   interval=0.01)
        assert out['levels'] == []
        assert out['outright_total'] == 0.0
        assert out['vwap'] is None
        assert out['buy'] == {'lots': 0.0, 'prints': 0, 'clip': None}


class TestNoSecondSideMapping:
    """Same guard as tests.test_aggressor_side.TestOneRulingOneMechanism --
    volume_at_price resolves side through ingest.aggressor.side_for_conditions,
    never by writing SetByBid/SetByAsk -> buy/sell in repository.py's own SQL
    or Python. That whole-tree test already covers this file; this is a
    narrower, file-local sanity check that inverting the vendored map still
    turns volume_at_price's numbers, proving it does not carry its own copy."""

    def test_inverting_the_vendored_map_changes_volume_at_price_too(self, db, monkeypatch):
        import ingest.aggressor as agg
        out_before = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        assert out_before['buy']['lots'] == BUY_LOTS

        monkeypatch.setitem(agg._TAG_TO_SIDE, 'outright_bid', agg.SELL)
        monkeypatch.setitem(agg._TAG_TO_SIDE, 'outright_ask', agg.BUY)
        out_after = repo.volume_at_price(db, CMD, ICE, SESS, SESS, interval=0.01)
        assert out_after['buy']['lots'] == SELL_LOTS   # flipped, as it should
        assert out_after['sell']['lots'] == BUY_LOTS
