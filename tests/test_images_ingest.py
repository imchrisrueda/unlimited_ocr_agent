import os
import shutil
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import pymupdf

from src.fieldnotes.ingest.images import (
    is_image_file,
    natural_sort_key,
    get_image_files_from_directory,
    convert_images_to_pdf,
    prepare_input_source,
    IMAGE_EXTENSIONS,
)


class TestImagesIngest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_images_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_image(self, filename: str, size: tuple[int, int] = (50, 50)) -> Path:
        img_path = self.temp_dir / filename
        img = Image.new("RGB", size, color="blue")
        img.save(img_path)
        return img_path

    def test_is_image_file(self):
        img_file = self._create_dummy_image("page_1.png")
        self.assertTrue(is_image_file(img_file))

        txt_file = self.temp_dir / "notes.txt"
        txt_file.write_text("hello", encoding="utf-8")
        self.assertFalse(is_image_file(txt_file))

    def test_natural_sort_order(self):
        names = ["p_10.png", "p_1.png", "p_2.png", "p_20.png"]
        sorted_names = sorted(names, key=lambda n: natural_sort_key(Path(n)))
        self.assertEqual(
            [n.name for n in [Path(s) for s in sorted_names]],
            ["p_1.png", "p_2.png", "p_10.png", "p_20.png"],
        )

    def test_get_image_files_from_directory(self):
        self._create_dummy_image("scan_10.jpg")
        self._create_dummy_image("scan_1.jpg")
        self._create_dummy_image("scan_2.jpg")
        # Archivo no imagen
        (self.temp_dir / "ignore.csv").write_text("id,val", encoding="utf-8")

        images = get_image_files_from_directory(self.temp_dir)
        self.assertEqual(len(images), 3)
        self.assertEqual(
            [img.name for img in images],
            ["scan_1.jpg", "scan_2.jpg", "scan_10.jpg"],
        )

    def test_get_image_files_empty_directory_error(self):
        empty_dir = self.temp_dir / "empty"
        empty_dir.mkdir()
        images = get_image_files_from_directory(empty_dir)
        self.assertEqual(len(images), 0)

        with self.assertRaises(ValueError):
            prepare_input_source(empty_dir)

    def test_convert_images_to_pdf(self):
        img1 = self._create_dummy_image("img1.png", (100, 150))
        img2 = self._create_dummy_image("img2.png", (120, 180))

        pdf_out = self.temp_dir / "output.pdf"
        res = convert_images_to_pdf([img1, img2], pdf_out)
        self.assertTrue(res.is_file())

        doc = pymupdf.open(str(pdf_out))
        self.assertEqual(len(doc), 2)
        doc.close()

    def test_prepare_input_source_directory(self):
        sub_dir = self.temp_dir / "2026-09-14"
        sub_dir.mkdir()
        img1 = sub_dir / "01.png"
        Image.new("RGB", (80, 80), "red").save(img1)
        img2 = sub_dir / "02.png"
        Image.new("RGB", (80, 80), "green").save(img2)

        pdf_path, was_converted, stem = prepare_input_source(sub_dir)
        self.assertTrue(was_converted)
        self.assertEqual(stem, "2026-09-14")
        self.assertTrue(pdf_path.is_file())

        doc = pymupdf.open(str(pdf_path))
        self.assertEqual(len(doc), 2)
        doc.close()

    def test_prepare_input_source_pdf(self):
        pdf_file = self.temp_dir / "doc.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_file))
        doc.close()

        resolved, was_converted, stem = prepare_input_source(pdf_file)
        self.assertFalse(was_converted)
        self.assertEqual(stem, "doc")
        self.assertEqual(resolved, pdf_file.resolve())

    def test_prepare_input_source_image_force_conversion(self):
        img = self._create_dummy_image("single.png")
        resolved, was_converted, stem = prepare_input_source(img, force_pdf_conversion=True)
        self.assertTrue(was_converted)
        self.assertEqual(stem, "single")
        self.assertEqual(resolved.suffix.lower(), ".pdf")

    def test_default_conversion_paths_do_not_collide(self):
        img = self._create_dummy_image("same.png")
        first, _, _ = prepare_input_source(img, force_pdf_conversion=True)
        second, _, _ = prepare_input_source(img, force_pdf_conversion=True)
        try:
            self.assertNotEqual(first, second)
            self.assertEqual(first.name, "same.pdf")
            self.assertEqual(second.name, "same.pdf")
        finally:
            shutil.rmtree(first.parent)
            shutil.rmtree(second.parent)


if __name__ == "__main__":
    unittest.main()
