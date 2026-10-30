"""Single source of randomness. Every generator function takes this
module's `rng` and `fk` (Faker instance). Nothing else calls random or
Faker directly, so the seed fully controls output."""

from __future__ import annotations
import numpy as np
from faker import Faker

def build_rng(seed: int) -> tuple[np.random.Generator, Faker]:
    rng = np.random.default_rng(seed)
    fk = Faker("en_IN")
    Faker.seed(seed)
    return rng, fk