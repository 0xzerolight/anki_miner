"""Reading-tab services: manga/novel source loaders, models, and splitting."""

# Transition shim (ARC-026): the reading data models moved to
# ``anki_miner.models.reading`` to kill the lone models -> services import
# edge. Re-exported here so ``anki_miner.services.reading.<Name>`` keeps
# resolving; import from ``anki_miner.models.reading`` in new code.
from anki_miner.models.reading import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)

__all__ = [
    "ImageRef",
    "ReadingUnit",
    "ReadingSourceRef",
    "ReadingDocument",
]
