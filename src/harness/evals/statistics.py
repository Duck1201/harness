import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from .models import TaskVerdict


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    estimate: float
    lower: float
    upper: float
    confidence: float = 0.95


@dataclass(frozen=True, slots=True)
class PromotionGateResult:
    promoted: bool
    reason_code: str
    non_inferiority_margin: float
    difference: ConfidenceInterval | None
    control: ConfidenceInterval | None
    candidate: ConfidenceInterval | None
    # Quantos pares saíram por inconclusivo, e a fração que isso representa. É
    # medida de record: uma promoção decidida sobre 30 de 50 pares não é a mesma
    # coisa que uma decidida sobre 50, e quem lê o resultado precisa ver isso.
    inconclusive_pairs: int = 0
    inconclusive_pair_rate: float = 0.0


def wilson_interval(successes: int, total: int) -> ConfidenceInterval:
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("successes must be between zero and total")
    if total == 0:
        return ConfidenceInterval(estimate=0.0, lower=0.0, upper=1.0)
    z = 1.959963984540054
    estimate = successes / total
    denominator = 1 + z * z / total
    center = (estimate + z * z / (2 * total)) / denominator
    radius = (
        z * math.sqrt(estimate * (1 - estimate) / total + z * z / (4 * total * total)) / denominator
    )
    return ConfidenceInterval(
        estimate=estimate,
        lower=max(0.0, center - radius),
        upper=min(1.0, center + radius),
    )


def paired_bootstrap_difference(
    candidate: Sequence[bool],
    control: Sequence[bool],
    *,
    seed: int,
    samples: int = 10_000,
) -> ConfidenceInterval:
    if len(candidate) != len(control) or not candidate:
        raise ValueError("candidate and control must contain the same non-zero pair count")
    if samples < 1:
        raise ValueError("samples must be positive")
    pair_count = len(candidate)
    differences = [int(candidate[index]) - int(control[index]) for index in range(pair_count)]
    estimate = sum(differences) / pair_count
    generator = random.Random(seed)
    draws = sorted(
        sum(differences[generator.randrange(pair_count)] for _ in range(pair_count)) / pair_count
        for _ in range(samples)
    )
    lower_index = max(0, math.floor(0.025 * (samples - 1)))
    upper_index = min(samples - 1, math.ceil(0.975 * (samples - 1)))
    return ConfidenceInterval(
        estimate=estimate,
        lower=draws[lower_index],
        upper=draws[upper_index],
    )


def evaluate_promotion_gate(
    *,
    control: Sequence[TaskVerdict],
    candidate: Sequence[TaskVerdict],
    security_violations: int,
    seed: int,
    non_inferiority_margin: float = -0.05,
    bootstrap_samples: int = 10_000,
    max_inconclusive_pair_rate: float = 0.2,
) -> PromotionGateResult:
    """Decide sobre os pares conclusivos, e diz quantos deixou de fora.

    A regra anterior reprovava qualquer execução que contivesse um inconclusivo,
    antes de olhar número nenhum. Isso não é rigor: um inconclusivo é o modelo
    travando ou o provedor caindo, não o candidato perdendo, e com um modelo
    pequeno ele aparece em toda execução — o gate ficava impossível de cumprir e
    não dizia nada sobre a hipótese. Agora o par sai da comparação, a fração sai
    no resultado, e um teto declarado separa "faltou dado nesses casos" de "esta
    execução não mediu nada".
    """
    if len(control) != len(candidate) or not control:
        raise ValueError("promotion requires paired non-empty arms")
    if security_violations < 0:
        raise ValueError("security_violations must be non-negative")
    if not 0.0 <= max_inconclusive_pair_rate <= 1.0:
        raise ValueError("max_inconclusive_pair_rate must be between zero and one")
    if security_violations:
        return PromotionGateResult(
            promoted=False,
            reason_code="security_violation",
            non_inferiority_margin=non_inferiority_margin,
            difference=None,
            control=None,
            candidate=None,
        )
    allowed = {TaskVerdict.PASS, TaskVerdict.FAIL}
    pairs = [
        (one, other)
        for one, other in zip(control, candidate, strict=True)
        if one in allowed and other in allowed
    ]
    dropped = len(control) - len(pairs)
    rate = dropped / len(control)
    if not pairs or rate > max_inconclusive_pair_rate:
        return PromotionGateResult(
            promoted=False,
            reason_code="inconclusive_above_ceiling",
            non_inferiority_margin=non_inferiority_margin,
            difference=None,
            control=None,
            candidate=None,
            inconclusive_pairs=dropped,
            inconclusive_pair_rate=rate,
        )
    control_values = tuple(verdict is TaskVerdict.PASS for verdict, _ in pairs)
    candidate_values = tuple(verdict is TaskVerdict.PASS for _, verdict in pairs)
    difference = paired_bootstrap_difference(
        candidate_values,
        control_values,
        seed=seed,
        samples=bootstrap_samples,
    )
    promoted = difference.lower >= non_inferiority_margin
    return PromotionGateResult(
        promoted=promoted,
        reason_code="promoted" if promoted else "non_inferiority_failed",
        non_inferiority_margin=non_inferiority_margin,
        difference=difference,
        control=wilson_interval(sum(control_values), len(control_values)),
        candidate=wilson_interval(sum(candidate_values), len(candidate_values)),
        inconclusive_pairs=dropped,
        inconclusive_pair_rate=rate,
    )
