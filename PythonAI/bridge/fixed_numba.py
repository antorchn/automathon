"""
Calculs en virgule fixe (Q16.16) pour les fonctions compilées avec Numba, au bit près comme bridge/fixed.py et le
C# du jeu. Les valeurs sont des entiers bruts (raw = valeur * 65536), comme dans l'état binaire (bridge/binary.py).

Sert aux versions Numba des bots (agents/<bot>/fast.py), qui doivent produire exactement les mêmes actions que
leur agent.py. Les calculs se font sur des entiers 64 bits : valable tant que les valeurs restent celles du jeu
(positions et vitesses bien en dessous de 32 768 unités).
"""

import math

import numpy as np
from numba import njit

FRACTIONAL_BITS = 16
ONE_RAW = 1 << FRACTIONAL_BITS
HALF_RAW = ONE_RAW >> 1
RAW_MAX = 2**31 - 1
RAW_MIN = -(2**31)


@njit(cache=True)
def saturate(raw):
    """Comme Fixed.from_raw : ramène dans l'intervalle des entiers 32 bits."""
    if raw > RAW_MAX:
        return np.int64(RAW_MAX)
    if raw < RAW_MIN:
        return np.int64(RAW_MIN)
    return np.int64(raw)


@njit(cache=True)
def trunc_div(n, d):
    """Division entière tronquée vers zéro (comme C#)."""
    q = abs(n) // abs(d)
    return q if (n >= 0) == (d >= 0) else -q


@njit(cache=True)
def rounded_divide(n, d):
    """Division entière, les demis arrondis loin de zéro (Fixed.RoundedDivide)."""
    if d < 0:
        n, d = -n, -d
    half = d // 2
    return trunc_div(n + half, d) if n >= 0 else trunc_div(n - half, d)


@njit(cache=True)
def isqrt(n):
    """Racine carrée entière (partie entière), comme math.isqrt."""
    if n <= 0:
        return np.int64(0)
    r = np.int64(math.sqrt(float(n)))
    while r * r > n:
        r -= 1
    while (r + 1) * (r + 1) <= n:
        r += 1
    return r


@njit(cache=True)
def mul(a, b):
    """Fixed * Fixed."""
    return saturate((np.int64(a) * np.int64(b) + HALF_RAW) >> FRACTIONAL_BITS)


@njit(cache=True)
def div(a, b):
    """Fixed / Fixed (division par zéro : MAX, MIN ou 0 selon le signe, comme le jeu)."""
    a, b = np.int64(a), np.int64(b)
    if b == 0:
        return np.int64(RAW_MAX) if a > 0 else np.int64(RAW_MIN) if a < 0 else np.int64(0)
    return saturate(rounded_divide(a << FRACTIONAL_BITS, b))


@njit(cache=True)
def dot(ax, ay, bx, by):
    """FixedVector2.dot."""
    exact = np.int64(ax) * np.int64(bx) + np.int64(ay) * np.int64(by)
    return saturate((exact + (1 << 15)) >> 16)


@njit(cache=True)
def length(x, y):
    """FixedVector2.length."""
    n = np.int64(x) * np.int64(x) + np.int64(y) * np.int64(y)
    root = isqrt(n)
    if n - root * root > root:
        root += 1
    return saturate(root)


@njit(cache=True)
def normalized(x, y):
    """FixedVector2.normalized : vecteur unitaire de même direction (le vecteur nul reste nul)."""
    x, y = np.int64(x), np.int64(y)
    if x == 0 and y == 0:
        return np.int64(0), np.int64(0)
    max_abs = max(abs(x), abs(y))
    while max_abs < (1 << 29):
        x <<= 1
        y <<= 1
        max_abs <<= 1
    n = x * x + y * y
    norm = isqrt(n)
    if n - norm * norm > norm:
        norm += 1
    return saturate(rounded_divide(x << FRACTIONAL_BITS, norm)), saturate(rounded_divide(y << FRACTIONAL_BITS, norm))
