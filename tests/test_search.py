"""Full-text search, including the CJK behaviour that drove the index design."""

from __future__ import annotations

from numis.search import build_query, contains_cjk, segment_cjk


def test_cjk_is_detected():
    assert contains_cjk("乾隆通寶")
    assert contains_cjk("明治三年 一圓")
    assert not contains_cjk("Maria Theresia")


def test_segmentation_splits_ideographs_but_leaves_other_scripts_alone():
    assert segment_cjk("乾隆通寶 寶泉 Qianlong") == "乾 隆 通 寶 寶 泉 Qianlong"
    assert segment_cjk("Maria Theresia") == "Maria Theresia"


def test_cjk_terms_become_character_phrases():
    assert build_query("通寶") == 'cjk_blob : "通 寶"'
    assert build_query("寶") == 'cjk_blob : "寶"'


def test_latin_terms_keep_prefix_matching():
    query = build_query("Theres")
    assert "cjk_blob" not in query
    assert '"Theres"*' in query


class TestSearching:
    def _coin(self, svc, sub, legend: str, name: str = ""):
        """Create a coin whose only searchable text is its legend.

        ``display_name`` is left empty on purpose: if it repeated the legend, a test could
        pass through the title index while the field index was broken.
        """
        coin = svc.add_specimen(sub, display_name=name, values={"legend": legend})
        svc.reindex(coin)
        return coin

    def test_two_character_cjk_terms_are_found_inside_a_longer_legend(self, svc, modern):
        """The failure that changed the design: 通寶 inside 乾隆通寶 matched nothing before."""
        svc.create_field("legend", "Legend", "text")
        qianlong = self._coin(svc, modern, "乾隆通寶 寶泉")
        xianfeng = self._coin(svc, modern, "咸豐重寶 當十")

        assert [c.id for c in svc.search("通寶")] == [qianlong.id]
        assert [c.id for c in svc.search("重寶")] == [xianfeng.id]
        assert [c.id for c in svc.search("當十")] == [xianfeng.id]

    def test_single_characters_are_searchable(self, svc, modern):
        svc.create_field("legend", "Legend", "text")
        first = self._coin(svc, modern, "乾隆通寶 寶泉")
        second = self._coin(svc, modern, "咸豐重寶 當十")
        assert {c.id for c in svc.search("寶")} == {first.id, second.id}

    def test_whole_legends_are_searchable(self, svc, modern):
        svc.create_field("legend", "Legend", "text")
        coin = self._coin(svc, modern, "乾隆通寶 寶泉")
        assert [c.id for c in svc.search("乾隆通寶")] == [coin.id]

    def test_latin_search_still_folds_diacritics(self, svc, modern):
        """The reason trigram was rejected: it stopped Gunzburg matching Günzburg."""
        svc.create_field("legend", "Legend", "text")
        coin = self._coin(svc, modern, "Maria Theresia Restrike Günzburg")
        assert [c.id for c in svc.search("Gunzburg")] == [coin.id]
        assert [c.id for c in svc.search("Günzburg")] == [coin.id]

    def test_prefix_search_works_for_latin(self, svc, modern):
        svc.create_field("legend", "Legend", "text")
        coin = self._coin(svc, modern, "Maria Theresia")
        assert [c.id for c in svc.search("Theres")] == [coin.id]

    def test_catalogue_numbers_are_searchable(self, svc, modern):
        km = svc.create_catalog("KM", "Krause")
        coin = svc.add_specimen(modern, display_name="Thaler")
        svc.add_reference(coin, km, "2073")
        svc.reindex(coin)
        assert [c.id for c in svc.search("2073")] == [coin.id]

    def test_reindexing_reflects_an_edit(self, svc, modern):
        """The FTS triggers must keep the index in step, or searches go stale."""
        svc.create_field("legend", "Legend", "text")
        coin = self._coin(svc, modern, "Maria Theresia")
        assert svc.search("Theresia")

        svc.set_value(coin, "legend", "Franz Joseph")
        svc.reindex(coin)
        assert svc.search("Theresia") == []
        assert [c.id for c in svc.search("Joseph")] == [coin.id]

    def test_deleted_coins_are_excluded_from_results(self, svc, modern):
        svc.create_field("legend", "Legend", "text")
        coin = self._coin(svc, modern, "Maria Theresia")
        svc.soft_delete(coin)
        assert svc.search("Theresia") == []

    def test_search_can_be_limited_to_one_subcollection(self, svc, modern, ancients):
        svc.create_field("legend", "Legend", "text")
        self._coin(svc, modern, "Silver Thaler")
        self._coin(svc, ancients, "Silver Denarius")
        assert len(svc.search("Silver")) == 2
        assert len(svc.search("Silver", subcollection=modern)) == 1

    def test_an_empty_term_returns_nothing(self, svc, modern):
        assert svc.search("") == []
        assert svc.search("   ") == []
