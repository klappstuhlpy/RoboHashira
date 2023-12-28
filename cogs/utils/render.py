import abc
from io import BytesIO
from pathlib import Path
from typing import Dict

from PIL import Image, ImageDraw, ImageFont

PATH = str(Path(__file__).parent.parent.parent.absolute()) + '/rendering/'


class Render(abc.ABCMeta):
    """A class for rendering images."""

    temp_payload: Dict[str, float] = {}

    @classmethod
    def set_payload(cls, **params: dict | float) -> None:
        cls.temp_payload.update(params)

    @classmethod
    def generate_eq_image(cls, payload: list[float]) -> BytesIO:
        reference_image = Image.open(PATH + 'generic_eq.png')

        image = Image.new('RGB', reference_image.size, 'white')
        draw = ImageDraw.Draw(image)

        image.paste(reference_image, (0, 0))

        num_bands = len(payload)
        width = image.width
        height = image.height + 35
        band_width = (width - 130) // num_bands
        band_height = (height - 280) // 2
        top_margin = (height - (2 * band_height)) // 2

        cls.set_payload(top_margin=top_margin, band_height=band_height)

        # Draw the Dots for the Gains
        for i, gain in enumerate(payload):
            x = 90 + i * band_width
            y = cls._get_gain_y(gain)

            draw.ellipse([(x + band_width // 2 - 2, y - 2), (x + band_width // 2 + 2, y + 2)], fill='white')

        # Draw the Lines for the Gains
        for i in range(num_bands - 1):
            x1 = 90 + (i + 0.5) * band_width
            gain = payload[i]
            y1 = cls._get_gain_y(gain)

            x2 = 90 + (i + 1.5) * band_width
            future_gain = payload[i + 1]
            y2 = cls._get_gain_y(future_gain)

            draw.line([(x1, y1), (x2, y2)], fill='white', width=1, joint='curve')

        font = ImageFont.truetype(PATH + 'Ginto-Bold.otf', 28)
        x = 356 - len('EQ') * (len('EQ') // 2)
        draw.text((x, 29), 'EQ', font=font, fill='white')

        buffer = BytesIO()
        image.save(buffer, 'png')
        buffer.seek(0)

        cls.temp_payload.clear()
        return buffer

    @classmethod
    def _get_gain_y(cls, gain: float, max_gain=+1.0, min_gain=-0.25):
        gain_range = max_gain - min_gain
        band_height = cls.temp_payload['band_height']
        top_margin = cls.temp_payload['top_margin']

        if gain > 0:
            y = top_margin + int((max_gain - gain) / gain_range * band_height)
        elif gain < 0:
            y = top_margin + band_height + int(gain / min_gain * band_height)
        else:
            y = top_margin + band_height
        return y
