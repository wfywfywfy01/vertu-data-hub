from io import BytesIO
import math
import struct
import wave

import av
from PIL import Image


def sample_video() -> bytes:
    output = BytesIO()
    with av.open(output, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=1)
        stream.width = 320
        stream.height = 240
        stream.pix_fmt = "yuv420p"
        for color in ("red", "green", "blue"):
            frame = av.VideoFrame.from_image(Image.new("RGB", (320, 240), color))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


def sample_audio() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        samples = [
            int(8000 * math.sin(2 * math.pi * 440 * index / 16_000))
            for index in range(16_000)
        ]
        stream.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return output.getvalue()
