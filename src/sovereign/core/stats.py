"""Statistiques sans dépendance lourde : régression, tests d'hypothèse, détection de ruptures, a posteriori bayésiens.

Implémentations de référence (Numerical Recipes pour la bêta incomplète régularisée). Couvertes par des tests
contre des valeurs tabulées.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("série vide")
    return sum(values) / len(values)


def variance(values: Sequence[float], ddof: int = 1) -> float:
    n = len(values)
    if n - ddof <= 0:
        return 0.0
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (n - ddof)


def stdev(values: Sequence[float], ddof: int = 1) -> float:
    return math.sqrt(variance(values, ddof))


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("série vide")
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def percentile(values: Sequence[float], q: float) -> float:
    """Percentile par interpolation linéaire, q dans [0, 100]."""

    if not values:
        raise ValueError("série vide")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (q / 100) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def mad(values: Sequence[float]) -> float:
    med = median(values)
    return median([abs(v - med) for v in values])


def robust_z(value: float, reference: Sequence[float]) -> float:
    """Score z robuste (médiane / MAD) : résistant aux valeurs aberrantes de la référence."""

    med = median(reference)
    spread = 1.4826 * mad(reference)
    if spread == 0:
        spread = stdev(reference) if len(reference) > 1 else 0.0
    if spread == 0:
        return 0.0 if value == med else math.copysign(math.inf, value - med)
    return (value - med) / spread


@dataclass(frozen=True)
class LinearFit:
    slope: float
    intercept: float
    r2: float
    n: int

    def predict(self, x: float) -> float:
        return self.intercept + self.slope * x


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> LinearFit:
    """Moindres carrés ordinaires."""

    if len(xs) != len(ys):
        raise ValueError("xs et ys doivent avoir la même longueur")
    n = len(xs)
    if n < 2:
        raise ValueError("au moins deux points sont nécessaires")
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return LinearFit(0.0, my, 0.0, n)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return LinearFit(slope, intercept, r2, n)


def growth_rate(first: float, last: float) -> float | None:
    if first == 0:
        return None
    return (last - first) / abs(first)


def cagr(first: float, last: float, periods: float) -> float | None:
    if first <= 0 or last <= 0 or periods <= 0:
        return None
    return (last / first) ** (1 / periods) - 1


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _betacf(a: float, b: float, x: float, max_iter: int = 300, eps: float = 3e-14) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Fonction bêta incomplète régularisée I_x(a, b)."""

    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p(t: float, df: float) -> float:
    if df <= 0:
        raise ValueError("df doit être positif")
    if math.isinf(t):
        return 0.0
    return betainc(df / 2.0, 0.5, df / (df + t * t))


@dataclass(frozen=True)
class TestResult:
    statistic: float
    p_value: float
    effect: float  # différence absolue (après - avant, ou B - A)
    relative_effect: float | None

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05


def welch_t_test(before: Sequence[float], after: Sequence[float]) -> TestResult:
    """Test t de Welch (variances inégales) : la moyenne a-t-elle réellement changé ?"""

    n1, n2 = len(before), len(after)
    if n1 < 2 or n2 < 2:
        raise ValueError("au moins deux observations par groupe")
    m1, m2 = mean(before), mean(after)
    v1, v2 = variance(before), variance(after)
    se2 = v1 / n1 + v2 / n2
    effect = m2 - m1
    relative = effect / abs(m1) if m1 else None
    if se2 == 0:
        p = 1.0 if effect == 0 else 0.0
        return TestResult(0.0 if effect == 0 else math.copysign(math.inf, effect), p, effect, relative)
    t = effect / math.sqrt(se2)
    df = se2**2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return TestResult(t, student_t_two_sided_p(t, df), effect, relative)


def two_proportion_z_test(successes_a: int, total_a: int, successes_b: int, total_b: int) -> TestResult:
    """Test z de deux proportions (A/B test de taux de conversion)."""

    if total_a <= 0 or total_b <= 0:
        raise ValueError("effectifs nuls")
    pa, pb = successes_a / total_a, successes_b / total_b
    pooled = (successes_a + successes_b) / (total_a + total_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / total_a + 1 / total_b))
    effect = pb - pa
    relative = effect / pa if pa else None
    if se == 0:
        return TestResult(0.0, 1.0, effect, relative)
    z = effect / se
    p = 2 * (1 - normal_cdf(abs(z)))
    return TestResult(z, p, effect, relative)


def cusum_change_point(values: Sequence[float], baseline: int | None = None, k: float = 0.5, h: float = 4.0) -> int | None:
    """CUSUM tabulaire bilatéral. Retourne l'index de la première alarme de rupture, ou None.

    La moyenne/écart-type de référence sont estimés sur les ``baseline`` premiers points (par défaut la moitié).
    """

    n = len(values)
    if n < 6:
        return None
    base = baseline or max(3, n // 2)
    ref = values[:base]
    mu = mean(ref)
    sigma = stdev(ref) if len(ref) > 1 else 0.0
    if sigma == 0:
        sigma = abs(mu) * 0.05 or 1.0
    s_high = s_low = 0.0
    for index in range(base, n):
        z = (values[index] - mu) / sigma
        s_high = max(0.0, s_high + z - k)
        s_low = max(0.0, s_low - z - k)
        if s_high > h or s_low > h:
            return index
    return None


@dataclass(frozen=True)
class BetaPosterior:
    """A posteriori Beta-Bernoulli (a priori uniforme Beta(1, 1))."""

    successes: float
    failures: float

    @property
    def alpha(self) -> float:
        return self.successes + 1.0

    @property
    def beta(self) -> float:
        return self.failures + 1.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def stdev(self) -> float:
        a, b = self.alpha, self.beta
        return math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))

    @property
    def n(self) -> float:
        return self.successes + self.failures


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def logit(p: float) -> float:
    p = clamp(p, 1e-6, 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
