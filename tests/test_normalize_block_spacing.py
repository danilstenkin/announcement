import re

from pipeline.steps.ai_analysis import _normalize_block_spacing


def _p_margins(html: str) -> list[str]:
    """Return the margin declared on every <p> tag (or '<none>')."""
    margins = []
    for tag in re.findall(r"<p\b[^>]*>", html):
        m = re.search(r"margin\s*:\s*([^;\"']+)", tag)
        margins.append(m.group(1).strip() if m else "<none>")
    return margins


def test_paragraphs_get_zero_margin():
    """Lines inside a block must sit tight — no browser-default <p> gaps."""
    html = "<div><p>· пункт 1</p><p>· пункт 2</p></div>"
    out = _normalize_block_spacing(html)
    assert _p_margins(out) == ["0", "0"]


def test_existing_p_margin_is_overridden():
    html = '<div><p style="margin: 16px 0;">· пункт</p></div>'
    out = _normalize_block_spacing(html)
    assert _p_margins(out) == ["0"]


def test_p_style_without_margin_gets_margin():
    html = '<div><p style="color: red;">текст</p></div>'
    out = _normalize_block_spacing(html)
    assert _p_margins(out) == ["0"]
    assert "color: red" in out


def test_empty_paragraphs_are_removed():
    html = "<div><p>текст</p><p></p><p>&nbsp;</p><p>   </p></div>"
    out = _normalize_block_spacing(html)
    assert out.count("<p") == 1


def test_consecutive_br_collapsed():
    html = "<div><p>строка 1<br><br><br>строка 2</p></div>"
    out = _normalize_block_spacing(html)
    assert "<br><br>" not in out.replace(" ", "")


def test_div_block_margin_still_normalized():
    """Existing behaviour: top-level div blocks keep the 4px rhythm."""
    html = (
        '<div style="background: #F0F4FF; margin: 20px 0 0 0;"><p>a</p></div>'
        '<div style="background: #FFF8E1; margin: 20px 0 0 0;"><p>b</p></div>'
    )
    out = _normalize_block_spacing(html)
    assert "margin: 4px 0 0 0" in out
    assert "margin: 20px" not in out
