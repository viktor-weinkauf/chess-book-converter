import figurine_ocr as f
from test_variation_tree import extract


def test_known_glyph_with_speck_preserves_capture_and_square():
    assert f.replace_prefix("18...£.:f3,","♗") == "18...♗:f3,"
    assert f.replace_prefix("18...£.d3","♗") is None


def test_enumerated_sibling_can_resume_from_a_known_anchor_after_damage():
    finder = extract("1. e4 e5 2. Nf3 ( 1) 2. Nc3 Nc6 3. Qz9 unread "
                     "2) 2. Bc4 Nf6 3. d3) Nc6 3. Bb5 a6 1-0",True)
    after_e5 = finder.games[0].variations[0].variations[0]
    assert [n.san() for n in after_e5.variations] == ["Nf3","Nc3","Bc4"]
    assert after_e5.variations[2].variations[0].san() == "Nf6"
    assert "Qz9" in after_e5.variations[1].variations[0].comment
