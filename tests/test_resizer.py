import io
import time

import pytest
from fakes import png_bytes
from PIL import Image

from winnower.resizer import (
    _contained,
    _cover_region,
    encode,
    extension,
    parse_color,
    render,
    resize_image,
)

RED, BLUE = (220, 30, 30, 255), (30, 30, 220, 255)


def halves(size=(200, 100)):
    """Left half red, right half blue: shows what a crop kept and where a pad put things."""
    image = Image.new("RGBA", size, RED)
    image.paste(Image.new("RGBA", (size[0] // 2, size[1]), BLUE), (size[0] // 2, 0))
    return image


def reopen(data):
    return Image.open(io.BytesIO(data))


class TestCrop:
    @pytest.mark.parametrize("source", [(200, 100), (100, 200), (64, 64), (10, 10), (3000, 2000)])
    def test_always_exactly_the_target_size_whatever_the_source(self, source):
        assert resize_image(Image.new("RGB", source), (64, 48), "crop").size == (64, 48)

    def test_cuts_evenly_from_both_sides_and_keeps_the_middle(self):
        # 200x100 into a square: scale 1, keep the middle 100 columns, which are half
        # red and half blue; a crop from one edge would be a single colour.
        out = resize_image(halves(), (100, 100), "crop")
        assert out.getpixel((10, 50)) == RED and out.getpixel((90, 50)) == BLUE

    def test_it_cuts_rather_than_squashes(self):
        # Three 100 px columns, red | blue | green. A centre crop to a square is the
        # blue one and nothing else; a resize that squashed the picture would show all three.
        source = Image.new("RGBA", (300, 100), RED)
        source.paste(Image.new("RGBA", (100, 100), BLUE), (100, 0))
        source.paste(Image.new("RGBA", (100, 100), (30, 220, 30, 255)), (200, 0))
        out = resize_image(source, (100, 100), "crop")
        assert {out.getpixel((x, 50)) for x in (0, 25, 50, 75, 99)} == {BLUE}

    def test_scales_up_small_images(self):
        assert resize_image(Image.new("RGB", (30, 20), "red"), (512, 512), "crop").size == (
            512,
            512,
        )


class TestCropOnRealisticSizes:
    """The first live run failed on these: 'box offset can't be negative'."""

    # Sizes Pixabay serves (largeImageURL is 1280 px on the long side) and common targets.
    SOURCES = [(1280, 853), (1280, 992), (1280, 960), (1280, 720), (853, 1280), (1920, 1280),
               (4000, 3000), (3000, 4000), (1000, 667), (640, 427), (1280, 1280)]  # fmt: skip
    TARGETS = [(256, 256), (512, 512), (640, 480), (300, 200), (100, 37), (1, 1), (33, 1000)]

    @pytest.mark.parametrize("source", SOURCES)
    def test_every_common_source_crops_to_every_common_target(self, source):
        image = Image.new("RGB", source, "red")
        for target in self.TARGETS:
            assert resize_image(image, target, "crop").size == target, (source, target)

    def test_the_regions_stay_inside_the_image_for_a_wide_sweep_of_shapes(self):
        for sw in range(1, 60, 7):
            for sh in range(1, 60, 5):
                for bw in (1, 3, 16, 100, 511):
                    for bh in (1, 7, 16, 100, 513):
                        left, top, right, bottom = _cover_region((sw, sh), (bw, bh))
                        assert 0 <= left < right <= sw and 0 <= top < bottom <= sh
                        assert (right - left) * bh == pytest.approx((bottom - top) * bw)

    def test_the_kept_axis_is_exactly_whole(self):
        left, top, right, bottom = _cover_region((1280, 853), (256, 256))
        assert (top, bottom) == (0.0, 853.0)  # not -5.7e-14 and 853.0000000000001

    def test_the_centre_is_still_the_centre(self):
        left, _, right, _ = _cover_region((300, 100), (100, 100))
        assert (left, right) == (100.0, 200.0)


class TestPad:
    def test_always_exactly_the_target_size(self):
        assert resize_image(Image.new("RGB", (200, 100)), (100, 100), "pad").size == (100, 100)

    def test_nothing_is_cut_and_it_sits_in_the_middle(self):
        out = resize_image(halves(), (100, 100), "pad", "#00ff00")
        # scaled to 100x50, centred: rows 25-74 are the picture, the rest is the fill
        assert out.getpixel((10, 50)) == RED and out.getpixel((90, 50)) == BLUE
        assert out.getpixel((50, 5)) == (0, 255, 0, 255)
        assert out.getpixel((50, 95)) == (0, 255, 0, 255)

    def test_without_a_background_the_padding_is_transparent(self):
        out = resize_image(halves(), (100, 100), "pad")
        assert out.getpixel((50, 5))[3] == 0 and out.getpixel((50, 50))[3] == 255

    def test_a_transparent_picture_lets_the_background_show_through(self):
        clear = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        assert resize_image(clear, (50, 50), "pad", "#ff0000").getpixel((25, 25)) == (
            255,
            0,
            0,
            255,
        )


class TestFit:
    def test_one_side_is_the_target_and_the_other_no_larger(self):
        assert resize_image(Image.new("RGB", (200, 100)), (100, 100), "fit").size == (100, 50)
        assert resize_image(Image.new("RGB", (100, 200)), (100, 100), "fit").size == (50, 100)

    def test_scales_up_to_the_box_too(self):
        assert resize_image(Image.new("RGB", (30, 20)), (300, 300), "fit").size == (300, 200)

    def test_an_exact_match_is_left_alone(self):
        assert resize_image(Image.new("RGB", (64, 64)), (64, 64), "fit").size == (64, 64)


class TestExtremeShapes:
    """`ImageOps.contain` raises on these in Pillow 12.3.0; see resizer.py."""

    @pytest.mark.parametrize("mode", ["crop", "pad", "fit"])
    @pytest.mark.parametrize("source", [(10000, 1), (1, 10000), (1, 1)])
    def test_none_of_them_ends_a_run(self, mode, source):
        out = resize_image(Image.new("RGB", source, "red"), (512, 512), mode)
        assert out.width >= 1 and out.height >= 1
        if mode != "fit":
            assert out.size == (512, 512)

    @pytest.mark.parametrize(("source", "box"), [((200, 100), (512, 512)), ((7, 13), (100, 100))])
    def test_rounding_never_overshoots_the_box_when_fitting(self, source, box):
        width, height = _contained(source, box)
        assert 1 <= width <= box[0] and 1 <= height <= box[1]

    def test_a_panorama_does_not_build_a_gigapixel_intermediate(self):
        # The first crop scaled the whole picture to cover the box: 5,120,000 x 512 for
        # this input, 97 seconds and gigabytes. Now it is one small resize.
        start = time.perf_counter()
        resize_image(Image.new("RGB", (10000, 1), "red"), (512, 512), "crop")
        assert time.perf_counter() - start < 2


class TestOrientation:
    def test_exif_rotation_is_applied_before_sizing(self):
        buffer = io.BytesIO()
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways; displays rotated 90 degrees clockwise
        Image.new("RGB", (200, 100), "red").save(buffer, "JPEG", exif=exif)
        buffer.seek(0)
        with Image.open(buffer) as image:
            assert resize_image(image, (500, 500), "fit").size == (250, 500)  # 100x200 shown


class TestSourceModes:
    def test_palette_png_with_transparency(self):
        source = Image.new("P", (20, 20), 0)
        source.info["transparency"] = 0
        out = resize_image(source, (10, 10), "crop")
        assert out.getpixel((5, 5))[3] == 0

    def test_grayscale_and_grayscale_alpha(self):
        assert resize_image(Image.new("L", (8, 8), 128), (4, 4)).getpixel((0, 0))[:3] == (
            128,
            128,
            128,
        )
        assert resize_image(Image.new("LA", (8, 8), (128, 0)), (4, 4)).getpixel((0, 0))[3] == 0

    def test_cmyk_becomes_rgb(self):
        assert resize_image(Image.new("CMYK", (8, 8)), (4, 4)).mode == "RGBA"

    def test_an_animation_is_reduced_to_its_first_frame(self):
        buffer = io.BytesIO()
        first, second = Image.new("RGB", (10, 10), "red"), Image.new("RGB", (10, 10), "blue")
        first.save(buffer, "GIF", save_all=True, append_images=[second], duration=50, loop=0)
        buffer.seek(0)
        with Image.open(buffer) as image:
            out = resize_image(image, (10, 10))
        assert out.getpixel((5, 5))[0] > 200  # red, not blue


class TestEncode:
    def test_png_is_the_default_and_keeps_transparency(self):
        data = encode(resize_image(halves(), (100, 100), "pad"), "png")
        image = reopen(data)
        assert image.format == "PNG" and image.mode == "RGBA"
        assert image.getpixel((50, 5))[3] == 0

    def test_an_opaque_result_is_saved_without_an_alpha_channel(self):
        for fmt, name in [("png", "PNG"), ("webp", "WEBP")]:
            image = reopen(encode(resize_image(halves(), (100, 50), "crop"), fmt))
            assert (image.format, image.mode) == (name, "RGB")

    def test_jpeg_flattens_transparency_onto_white_by_default(self):
        data = encode(resize_image(halves(), (100, 100), "pad"), "jpeg")
        image = reopen(data)
        assert image.format == "JPEG" and image.mode == "RGB"
        assert all(c > 240 for c in image.getpixel((50, 5)))

    def test_jpeg_flattens_onto_the_given_background(self):
        data = encode(resize_image(halves(), (100, 100), "pad"), "jpeg", "#000000")
        assert all(c < 15 for c in reopen(data).getpixel((50, 5)))

    def test_webp_keeps_transparency(self):
        image = reopen(encode(resize_image(halves(), (100, 100), "pad"), "webp"))
        assert image.format == "WEBP" and image.getpixel((50, 5))[3] == 0

    def test_the_same_input_gives_the_same_bytes(self):
        one = encode(resize_image(halves(), (64, 64), "crop"), "png")
        two = encode(resize_image(halves(), (64, 64), "crop"), "png")
        assert one == two

    @pytest.mark.parametrize(("fmt", "ext"), [("png", "png"), ("jpeg", "jpg"), ("webp", "webp")])
    def test_extension(self, fmt, ext):
        assert extension(fmt) == ext


class TestRender:
    def test_from_a_file_on_disk_to_bytes(self, tmp_path):
        path = tmp_path / "in.png"
        path.write_bytes(png_bytes((40, 30)))
        image = reopen(render(path, (16, 16), "pad", "png"))
        assert image.size == (16, 16)

    def test_the_source_file_is_not_modified(self, tmp_path):
        path = tmp_path / "in.png"
        path.write_bytes(png_bytes((40, 30)))
        before = path.read_bytes()
        render(path, (16, 16), "crop", "jpeg")
        assert path.read_bytes() == before

    def test_an_unknown_mode_is_a_programming_error(self):
        with pytest.raises(ValueError, match="unknown resize mode"):
            resize_image(Image.new("RGB", (4, 4)), (4, 4), "stretch")


def test_parse_color():
    assert parse_color("#FFaa00") == (255, 170, 0)
