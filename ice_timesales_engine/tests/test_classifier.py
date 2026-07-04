"""Classifier tests -- compound conditions, precedence ladder (edge 10, 11)."""

from ingest.classifier import primary_type, tag_flags, tokenize


class TestTokenize:
    def test_simple(self):
        assert tokenize('SetByAsk') == frozenset({'SetByAsk'})

    def test_compound(self):
        assert tokenize('EFS, Delete') == frozenset({'EFS', 'Delete'})
        assert tokenize('BlockTrde, Leg') == frozenset({'BlockTrde', 'Leg'})

    def test_blank(self):
        assert tokenize('') == frozenset()
        assert tokenize('  ') == frozenset()


class TestPrimaryType:
    def test_outrights(self):
        assert primary_type(tokenize('SetByAsk')) == 'outright'
        assert primary_type(tokenize('SetByBid')) == 'outright'

    def test_leg(self):
        assert primary_type(tokenize('Leg')) == 'leg'

    def test_efs_and_delete(self):
        assert primary_type(tokenize('EFS')) == 'efs'
        assert primary_type(tokenize('EFS, Delete')) == 'efs_delete'

    def test_efp(self):
        assert primary_type(tokenize('EFP')) == 'efp'

    def test_block_beats_leg(self):
        # 'BlockTrde, Leg' is both a block and a leg; precedence -> block.
        assert primary_type(tokenize('BlockTrde, Leg')) == 'block'

    def test_blank_is_outright(self):
        # A blank-condition print is a real outright fill (no aggressor stamp).
        assert primary_type(tokenize('')) == 'outright'

    def test_unknown_is_other(self):
        assert primary_type(tokenize('SomethingNew')) == 'other'


class TestTagFlags:
    def test_multi_tag_membership(self):
        f = tag_flags(tokenize('BlockTrde, Leg'))
        assert f['is_block'] and f['is_leg']
        assert not f['is_outright'] and not f['is_efs']

    def test_delete_flag(self):
        assert tag_flags(tokenize('EFS, Delete'))['is_delete'] is True


class TestOutrightSide:
    def test_ask_bid_unstamped(self):
        from ingest.classifier import outright_side
        assert outright_side(tokenize('SetByAsk')) == 'outright_ask'
        assert outright_side(tokenize('SetByBid')) == 'outright_bid'
        assert outright_side(tokenize('')) == 'outright_unstamped'
