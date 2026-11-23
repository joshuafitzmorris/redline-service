"""Seeded synthetic contracts of any size, for the tests and the benchmark."""

import random

PARTIES = ["Larkspur Analytics", "Quillfeather Systems", "Tamberlane Freight", "Orchard Vale Labs"]
STATES = ["Delaware", "New York", "California", "Texas", "Oregon", "Vermont"]
CLAUSES = [
    "Payment. {a} shall pay each invoice from {b} within Net {net} days of receipt.",
    "Limitation of Liability. Neither {a} nor {b} shall be liable for indirect or consequential "
    "damages, and the liability of {a} shall not exceed the fees paid in the prior twelve months.",
    "Governing Law. This Agreement between {a} and {b} is governed by the laws of the State of "
    "{state}, and its courts have exclusive jurisdiction.",
    "Confidentiality. {b} shall keep the Confidential Information of {a} secret and use it only "
    "to perform this Agreement.",
    "Indemnification. {a} shall indemnify {b} against third-party claims arising from a breach "
    "of this Agreement by {a}.",
    "Termination. Either {a} or {b} may terminate this Agreement on {days} days written notice.",
    "Assignment. {b} may not assign this Agreement without the written consent of {a}.",
]


def contract(size: int, seed: int = 0) -> str:
    """A title and numbered clauses, at least `size` characters, the same for a given seed."""
    rng = random.Random(seed)
    parts = ["Master Services Agreement"]
    length = len(parts[0])
    while length < size:
        a, b = rng.sample(PARTIES, 2)
        clause = rng.choice(CLAUSES).format(
            a=a,
            b=b,
            net=rng.choice([15, 30, 45, 60]),
            days=rng.choice([30, 60, 90]),
            state=rng.choice(STATES),
        )
        parts.append(f"\n\n{len(parts)}. {clause}")
        length += len(parts[-1])
    return "".join(parts)
