"""Every CSS variable the app uses must actually be defined.

    python manage.py test competitions.test_styles

An undefined custom property fails silently: `var(--accent)` with no
fallback drops the declaration, and with a fallback it quietly hardcodes
a colour outside the palette. Either way the rule loads, the page looks
subtly wrong, and nothing reports it. Several rules shipped referencing
`--accent`, `--text-muted`, `--green` and `--warning`, none of which this
app defines.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

STATIC = Path(settings.BASE_DIR) / "static" / "css"
TEMPLATES = Path(settings.BASE_DIR) / "templates"

# The standalone public pages carry their own :root block (they are copies
# of the design-repo site), so they define what they use.
SELF_CONTAINED = {
    "website_landing_page.html",
    "prepare.html",
    "partner.html",
    "track_application_red.html",
    "track_application_blue.html",
    "track_ai_red.html",
    "track_ai_defence_blue.html",
}

USE = re.compile(r"var\(\s*--([a-z0-9-]+)")
DEFINE = re.compile(r"--([a-z0-9-]+)\s*:")


def defined_tokens():
    return set(DEFINE.findall((STATIC / "variables.css").read_text(encoding="utf-8")))


class CssVariableTests(SimpleTestCase):
    def setUp(self):
        self.defined = defined_tokens()

    def test_variables_file_defines_the_palette(self):
        """Guards the guard: a glob that matches nothing proves nothing."""
        self.assertIn("primary", self.defined)
        self.assertIn("danger", self.defined)
        self.assertGreater(len(self.defined), 20)

    def test_app_stylesheets_only_use_defined_tokens(self):
        offenders = {}
        for path in sorted(STATIC.glob("*.css")):
            if path.name == "variables.css":
                continue
            missing = sorted(USE.findall(path.read_text(encoding="utf-8")))
            missing = sorted({m for m in missing if m not in self.defined})
            if missing:
                offenders[path.name] = missing
        self.assertEqual(offenders, {}, f"undefined CSS variables: {offenders}")

    def test_app_templates_only_use_defined_tokens(self):
        offenders = {}
        for path in sorted(TEMPLATES.rglob("*.html")):
            if path.name in SELF_CONTAINED:
                continue
            missing = sorted({
                m for m in USE.findall(path.read_text(encoding="utf-8"))
                if m not in self.defined
            })
            if missing:
                offenders[str(path.relative_to(TEMPLATES))] = missing
        self.assertEqual(offenders, {}, f"undefined CSS variables: {offenders}")

    def test_every_stylesheet_has_balanced_braces(self):
        """A stray brace stops the parser, dropping every rule after it."""
        for path in sorted(STATIC.glob("*.css")):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                text.count("{"), text.count("}"), f"{path.name} has unbalanced braces"
            )
