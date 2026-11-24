"""Original, bounded templates inspired by GSM-Symbolic (not its dataset).

An instance has one baseline and five single-intervention variants. Fresh
parameters do not establish absence from a model's training distribution.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import random

VERSION = "ruleshift-templates-v1"
VARIANTS = ("baseline", "names", "numbers", "irrelevant", "rule", "boundary")
TEMPLATES = ("inventory", "tickets", "refund")


@dataclass(frozen=True)
class Case:
    case_id: str
    family: str
    template_id: str
    seed: int
    variant: str
    relation: str
    prompt: str
    expected: int | bool

    def record(self):
        return asdict(self)


def generate_cases(seeds: int = 10, start_seed: int = 1000,
                   families: tuple[str, ...] = ("math", "policy")) -> list[Case]:
    if not 1 <= seeds <= 10000 or start_seed < 0:
        raise ValueError("seeds must be 1..10000 and start_seed must be nonnegative")
    if not families or not set(families) <= {"math", "policy"}:
        raise ValueError("families must contain math and/or policy")
    cases = []
    for seed in range(start_seed, start_seed + seeds):
        for template in TEMPLATES:
            family = "policy" if template == "refund" else "math"
            if family not in families:
                continue
            # Stable per-template PRNG, independent of selection/order of suites.
            rng = random.Random(f"{VERSION}:{seed}:{template}")
            a, b, c = rng.randint(30, 70), rng.randint(3, 15), rng.randint(2, 9)
            refund_days = rng.randint(5, 40)
            unused = rng.choice((True, True, False))
            final_sale = rng.choice((True, True, False))
            for variant in VARIANTS:
                x, y, z = (a + 7, b + 2, c + 3) if variant == "numbers" else (a, b, c)
                name = "Nila" if variant == "names" else "Arun"
                relation = "invariant" if variant in ("names", "irrelevant") else "recompute"
                if variant == "baseline":
                    relation = "baseline"
                if template == "inventory":
                    if variant == "boundary":
                        y, z = x, 0
                    question = (f"{name}'s shop starts with {x} notebooks. It sells {y} notebooks "
                                f"and receives {z} new notebooks. ")
                    expected = x - y + z
                    if variant == "rule":
                        question += f"It then donates {z} notebooks from its stock. "
                        expected -= z
                    if variant == "irrelevant":
                        question += "The shop's delivery van has 4 wheels; this does not change its notebook stock. "
                    question += "How many notebooks remain in the shop?"
                elif template == "tickets":
                    if variant == "boundary":
                        z = 0
                    question = (f"{name} buys {y} tickets at {x} rupees each "
                                f"and pays a single booking fee of {z} rupees. ")
                    expected = x * y + z
                    if variant == "rule":
                        question += f"A discount of {z} rupees is then applied to the total bill. "
                        expected -= z
                    if variant == "irrelevant":
                        question += "The cinema has 6 exits; this has no effect on the bill. "
                    question += "What is the total bill in rupees?"
                else:
                    window, days = (21, refund_days) if variant == "numbers" else (30, refund_days)
                    if variant == "boundary":
                        days = window + (seed % 2)
                    question = (f"Policy: A refund is allowed when an item is unused and returned within "
                                f"{window} days, including day {window}. ")
                    if variant == "rule":
                        question += "Final-sale items are excluded even if all other conditions are satisfied. "
                    condition = "unused" if unused else "used"
                    sale = "final-sale" if final_sale else "regular-sale"
                    question += (f"Case: {name}'s item is {condition}, marked {sale}, "
                                 f"and returned on day {days}. ")
                    if variant == "irrelevant":
                        question += "The customer owns 3 cats; this is not part of the refund policy. "
                    question += "Under only the stated policy, is the refund allowed?"
                    expected = unused and days <= window and not (variant == "rule" and final_sale)
                cases.append(Case(f"{template}-{seed}-{variant}", family, template,
                                  seed, variant, relation, question, expected))
    return cases


def dataset_hash(cases: list[Case]) -> str:
    payload = json.dumps([c.record() for c in cases], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
