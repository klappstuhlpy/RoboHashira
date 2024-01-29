import abc
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFont

PATH = str(Path(__file__).parent.parent.parent.absolute() / 'assets')

GINTO_BOLD_28 = ImageFont.truetype(PATH + '/GintoBold.otf', 28)


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

    @staticmethod
    def get_text_dimensions(text_string: str, font: ImageFont) -> tuple[int, int]:
        # https://stackoverflow.com/a/46220683/9263761
        ascent, descent = font.getmetrics()

        text_width = font.getmask(text_string).getbbox()[2]
        text_height = font.getmask(text_string).getbbox()[3] + descent

        return text_width, text_height

    @classmethod
    def generate_bar_chart(cls, data: dict[str, int], title: Optional[str] = None) -> list[bytes]:
        """Generate a bar chart image from a dictionary of data.

        Parameters
        ----------
        data : dict
            A dictionary of data to generate the bar chart from.
            Data must follow the format of {str: int}.
        title : Optional[str], optional
            The title of the bar chart, by default None
        """
        BAR_HEIGHT = 25
        BAR_COLOR = (227, 38, 54)
        LABEL_FONT_SIZE = 18
        LABEL_PADDING = 10
        CHART_MARGIN = 20
        MAX_WIDTH = 1360
        MAX_HEIGHT = 675

        num_bars = len(data)
        max_keys_per_chart = int(MAX_HEIGHT / (BAR_HEIGHT + LABEL_PADDING)) - 2

        chart_width = max(min(max(data.values()), MAX_WIDTH) + LABEL_PADDING * 2, MAX_WIDTH)
        chart_height = (num_bars + 1) * (BAR_HEIGHT + LABEL_PADDING) + CHART_MARGIN * 2

        scale_factor = min(MAX_WIDTH / chart_width, MAX_HEIGHT / chart_height)
        chart_width *= scale_factor
        chart_height *= scale_factor

        image_count = len(data) // max_keys_per_chart + 1 if len(data) % max_keys_per_chart != 0 else len(
            data) // max_keys_per_chart

        images = []
        for i in range(image_count):
            start_index = i * max_keys_per_chart
            end_index = start_index + max_keys_per_chart
            subset_data = dict(list(data.items())[start_index:end_index])

            image = Image.new('RGB', (int(chart_width), int(chart_height)), color=0x1A1A1A)
            draw = ImageDraw.Draw(image)

            font = ImageFont.truetype(PATH + '/GintoBold.otf', int(LABEL_FONT_SIZE * scale_factor))
            max_label_width = max([cls.get_text_dimensions(label, font=font)[0] for label in subset_data.keys()])
            max_value_width = max([cls.get_text_dimensions(str(value), font=font)[0] for value in subset_data.values()])

            if title:
                title_font = ImageFont.truetype(PATH + '/GintoBold.otf', int(LABEL_FONT_SIZE * scale_factor * 1.5))
                title_bbox = draw.textbbox((0, 0), title, font=title_font)
                title_width = title_bbox[2] - title_bbox[0]
                title_height = title_bbox[3] - title_bbox[1]
                title_position = ((chart_width - title_width) // 2, CHART_MARGIN)
                draw.text(
                    title_position,
                    title,
                    font=title_font,
                    fill=(255, 255, 255)
                )

                y = CHART_MARGIN + (title_height + 5) + LABEL_PADDING * 2
            else:
                y = CHART_MARGIN

            for label, value in subset_data.items():
                _, label_height = cls.get_text_dimensions(label, font=font)
                value_width, value_height = cls.get_text_dimensions(str(value), font=font)

                # the label is aligned to the left of the image
                label_position = (LABEL_PADDING, y + (BAR_HEIGHT - label_height) // 2)
                draw.text(
                    label_position,
                    label,
                    font=font,
                    color=(255, 255, 255),
                    LANCZOS=True
                )

                bar_width = chart_width - max_label_width - max_value_width - LABEL_PADDING * 4
                # Calculate the length of the bar by dividing the value
                # by the max value and multiplying it by the max ar width
                bar_width = int(value / max(data.values()) * bar_width)

                # value is the count how often the command was invoked; it's displayed right on the bar
                value_position = (LABEL_PADDING * 3 + bar_width + max_label_width, y + (BAR_HEIGHT - value_height) // 2)
                draw.text(
                    value_position,
                    str(value),
                    font=font,
                    color=(255, 255, 255),
                    LANCZOS=True
                )

                # bar starts after the label and has a width of max_bar_width
                draw.rounded_rectangle(
                    (
                        LABEL_PADDING * 2 + max_label_width,
                        y,
                        LABEL_PADDING * 2 + max_label_width + bar_width,
                        y + BAR_HEIGHT
                    ),
                    10,
                    outline=BAR_COLOR,
                    width=40,
                    fill=BAR_COLOR
                )

                y += BAR_HEIGHT + LABEL_PADDING

            buffer = BytesIO()
            image.save(buffer, 'png')
            buffer.seek(0)
            images.append(buffer.read())

        return images
