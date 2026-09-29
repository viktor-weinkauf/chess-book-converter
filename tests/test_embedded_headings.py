import pytest
import page_layout as layout


BEFORE = 'Белые могли продолжать 30. №128 . cer 087\nПримечание\nСпасский Штейн\nМосква, 1971\n1. d2-d4 Kg8-f6'
AFTER = 'Белые могли продолжать 30.\nПримечание\n№128\nСпасский Штейн\nМосква, 1971\n1. d2-d4 Kg8-f6'


def test_embedded_heading_requests_geometry_retry_and_must_survive():
    assert layout.embedded_headings(BEFORE) == {'128'}
    assert layout.needs_retry(BEFORE)
    assert layout.improved(BEFORE, AFTER)
    assert not layout.improved(BEFORE, AFTER.replace('№128\n', ''))
    assert not layout.improved(AFTER, BEFORE)


@pytest.mark.parametrize('text', [
    'См. №128 в другой главе',
    BEFORE.replace('Спасский Штейн', 'Обычный комментарий к партии'),
    BEFORE.replace('1. d2-d4 Kg8-f6', '30. d2-d4 Kg8-f6'),
    BEFORE.replace('Примечание\n', 'Примечание\n'*25),
    AFTER,
])
def test_reference_or_missing_nearby_names_and_opening_is_not_a_merged_heading(text):
    assert not layout.embedded_headings(text)
