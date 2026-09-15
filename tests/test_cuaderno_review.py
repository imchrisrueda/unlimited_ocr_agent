import json
import tempfile
import unittest
from pathlib import Path

from src.fieldnotes.review.cuaderno_campo import archive_previous_review, create_review_draft, publish_cuaderno_review


class TestCuadernoReview(unittest.TestCase):
    def _session(self, root: Path, pages=(1, 2)) -> Path:
        session = root / "session"
        (session / "review").mkdir(parents=True)
        (session / "pages").mkdir()
        source = root / "source.pdf"
        source.write_bytes(b"source-pdf")
        for page in pages:
            (session / "pages" / f"page_{page:03d}.png").write_bytes(b"png")
        (session / "document.json").write_text(json.dumps({
            "source_file": str(source),
            "pages": [{"page_number": page} for page in pages],
        }), encoding="utf-8")
        return session

    def test_create_review_draft_is_non_destructive_and_fixes_relative_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(Path(tmp), pages=(1,))
            markdown = "# Cuaderno de campo\n\n## Página 1\n\n![Página 1](pages/page_001.png)\n"
            create_review_draft(markdown, session / "review")
            draft = session / "review" / "transcripcion.md"
            self.assertIn("](../pages/", draft.read_text(encoding="utf-8"))
            draft.write_text("edición humana", encoding="utf-8")
            create_review_draft(markdown, session / "review")
            self.assertEqual(draft.read_text(encoding="utf-8"), "edición humana")

    def test_publish_creates_synchronized_reviewed_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(Path(tmp))
            reviewed = (
                "# Cuaderno de campo\n\n"
                "## Página 1\n\n### Actuaciones\n\nSe colocaron señales.\n\n"
                "#### Interpretación espacial revisada\n\n````text\nEnsayo dentro de Buffer.\n````\n\n"
                "### Imagen original de la página\n\n![Página 1](../pages/page_001.png)\n\n"
                "## Página 2\n\n### Observaciones\n\nTexto revisado.\n\n"
                "### Imagen original de la página\n\n![Página 2](../pages/page_002.png)\n"
            )
            (session / "review" / "transcripcion.md").write_text(reviewed, encoding="utf-8")
            document = publish_cuaderno_review(session, "revisor", reviewed_at="2026-09-15T10:00:00+00:00")
            self.assertEqual([p.page_number for p in document.pages], [1, 2])
            self.assertEqual(document.authority, "human_reviewed")
            self.assertTrue((session / "reviewed" / "notas.md").is_file())
            published = json.loads((session / "reviewed" / "document.json").read_text(encoding="utf-8"))
            self.assertEqual(published["reviewer"], "revisor")
            self.assertEqual(published["pages"][0]["sections"][0]["title"], "Actuaciones")
            self.assertIn("Ensayo dentro de Buffer", published["pages"][0]["spatial_interpretation"])
            self.assertNotIn("Imagen original", published["pages"][0]["spatial_interpretation"])
            self.assertEqual(published["reviewed_markdown_sha256"], document.reviewed_markdown_sha256)
            manifest = json.loads((session / "review" / "revision.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "approved")

    def test_publish_accepts_corrected_vlm_spatial_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(Path(tmp), pages=(1,))
            reviewed = (
                "# Cuaderno de campo\n\n## Página 1\n\n"
                "#### Interpretación espacial propuesta por el VLM\n\n"
                "```text\n[Ensayo] <-- Buffer\n```\n\n"
                "**Resumen espacial propuesto:** El buffer queda junto al ensayo.\n\n"
                "### Imagen original de la página\n\n![Página 1](../pages/page_001.png)\n"
            )
            (session / "review" / "transcripcion.md").write_text(reviewed, encoding="utf-8")
            document = publish_cuaderno_review(session, "revisor")
            self.assertIn("[Ensayo]", document.pages[0].spatial_interpretation or "")

    def test_publish_rejects_missing_or_reordered_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(Path(tmp))
            (session / "review" / "transcripcion.md").write_text(
                "# Cuaderno de campo\n\n## Página 2\n\n![Página 2](../pages/page_002.png)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "una vez y en orden"):
                publish_cuaderno_review(session, "revisor")

    def test_publish_rejects_missing_source_image_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(Path(tmp), pages=(1,))
            (session / "review" / "transcripcion.md").write_text(
                "# Cuaderno de campo\n\n## Página 1\n\nTexto sin imagen.\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "enlace"):
                publish_cuaderno_review(session, "revisor")

    def test_open_issues_require_documented_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(Path(tmp), pages=(1,))
            markdown = "# Cuaderno de campo\n\n## Página 1\n\n![Página 1](../pages/page_001.png)\n"
            issue = {"issue_id": "p001_text_123", "page": 1, "field": "text", "reason": "Lectura dudosa"}
            (session / "review" / "issues.json").write_text(json.dumps([issue]), encoding="utf-8")
            create_review_draft(markdown.replace("../pages/", "pages/"), session / "review")
            with self.assertRaisesRegex(ValueError, "continúa pendiente"):
                publish_cuaderno_review(session, "revisor")
            (session / "review" / "resolutions.json").write_text(json.dumps([
                {"issue_id": issue["issue_id"], "status": "accepted_uncertain", "note": "La página no permite resolverlo."}
            ]), encoding="utf-8")
            document = publish_cuaderno_review(session, "revisor")
            self.assertEqual(len(document.accepted_uncertainties), 1)

    def test_previous_human_review_is_archived_as_non_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "session"
            (canonical / "review").mkdir(parents=True)
            (canonical / "reviewed").mkdir()
            (canonical / "review" / "transcripcion.md").write_text("revisión previa", encoding="utf-8")
            (canonical / "review" / "revision.json").write_text('{"status":"approved"}', encoding="utf-8")
            (canonical / "reviewed" / "document.json").write_text('{"approved":true}', encoding="utf-8")
            staging_review = root / "staging" / "review"
            staging_review.mkdir(parents=True)
            archive_previous_review(canonical, staging_review)
            archives = list((staging_review / "history").iterdir())
            self.assertEqual(len(archives), 1)
            self.assertEqual((archives[0] / "transcripcion.md").read_text(encoding="utf-8"), "revisión previa")
            self.assertTrue((archives[0] / "reviewed" / "document.json").is_file())
            self.assertIn("no es la revisión vigente", (archives[0] / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
