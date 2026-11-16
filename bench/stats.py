import math


def percentile(values, q):
    """Linear-interpolated percentile (q in 0..100)."""
    s = sorted(values)
    k = (len(s) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": sum(values) / len(values), "p50": percentile(values, 50),
            "p90": percentile(values, 90), "p95": percentile(values, 95), "max": max(values)}
