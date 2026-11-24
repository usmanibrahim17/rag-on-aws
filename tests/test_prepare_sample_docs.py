import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from prepare_sample_docs import strip_html_wrappers, strip_in_place  # noqa: E402


def test_dfn_tags_are_removed_keeping_inner_text():
    line = 'Use a <dfn title="an HTTP GET method"><code>get</code> operation</dfn> here.'
    assert strip_html_wrappers(line) == "Use a get operation here."


def test_other_formatting_tags_are_removed_keeping_inner_text():
    text = "The <abbr title='x'>UIs</abbr> are <b>bold</b>, <em>soft</em> and <kbd>Ctrl</kbd>."
    assert strip_html_wrappers(text) == "The UIs are bold, soft and Ctrl."


def test_img_tags_are_dropped_entirely_and_image_only_lines_vanish():
    text = 'Before\n<img src="/img/a.png">\nAfter <img src="b.png"> end'
    assert strip_html_wrappers(text) == "Before\nAfter  end"


def test_links_tables_and_forms_are_left_alone():
    text = (
        'See <a href="https://x.dev">docs</a>.\n'
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>\n"
        '<form><input type="text"></form>'
    )
    assert strip_html_wrappers(text) == text


def test_fenced_code_and_inline_code_spans_are_untouched():
    text = (
        "Write `<dfn>x</dfn>` literally.\n"
        "```html\n"
        '<img src="a.png"><b>bold</b> <dfn title="t">y</dfn>\n'
        "```\n"
        "After <dfn>z</dfn>."
    )
    expected = text.replace("After <dfn>z</dfn>.", "After z.")
    assert strip_html_wrappers(text) == expected


def test_stripping_is_idempotent():
    once = strip_html_wrappers('A <dfn title="t">b</dfn> <img src="i.png"> c')
    assert strip_html_wrappers(once) == once


def test_strip_in_place_counts_only_changed_files(tmp_path):
    (tmp_path / "dirty.md").write_text("A <dfn>b</dfn>\n", encoding="utf-8")
    (tmp_path / "clean.md").write_text("Nothing to do\n", encoding="utf-8")

    assert strip_in_place(tmp_path) == 1
    assert (tmp_path / "dirty.md").read_text(encoding="utf-8") == "A b\n"
    assert strip_in_place(tmp_path) == 0  # second run changes nothing
