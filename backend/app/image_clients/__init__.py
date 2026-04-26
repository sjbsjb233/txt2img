"""Image-generation clients for the bltcy.ai relay.

Two thin async clients, one per upstream API shape:

- ``bltcy_nano_banana``: Gemini-native ``generateContent`` for the Nano Banana
  family (``gemini-2.5-flash-image``, ``gemini-3-pro-image-preview``,
  ``gemini-3.1-flash-image-preview``).
- ``bltcy_gpt_image_2``: OpenAI-native ``images.generations`` and
  ``images.edits`` for the ``gpt-image-2`` model.
"""

from .bltcy_nano_banana import BltcyNanoBananaClient, NanoBananaResult
from .bltcy_gpt_image_2 import BltcyGptImage2Client, GptImageResult

__all__ = [
    "BltcyNanoBananaClient",
    "NanoBananaResult",
    "BltcyGptImage2Client",
    "GptImageResult",
]
