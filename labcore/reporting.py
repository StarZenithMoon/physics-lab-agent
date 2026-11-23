"""Decimal formatting for a reported estimate and its uncertainty."""
from decimal import Decimal, ROUND_HALF_EVEN, localcontext


def rounded_standard(value, u, *, digits=None, recorded_value=None):
    """Return display strings; an optional recorded estimate supplies its last place."""
    estimate, uncertainty = Decimal(str(value)), Decimal(str(u))
    if not estimate.is_finite() or not uncertainty.is_finite() or uncertainty <= 0:
        raise ValueError("rounding requires a finite estimate and positive finite uncertainty")
    if digits is not None and (type(digits) is not int or digits < 1):
        raise ValueError("digits must be a positive integer")
    if recorded_value is not None:
        if not isinstance(recorded_value, str):
            raise ValueError("recorded_value must be decimal text retaining its original last digit")
        recorded = Decimal(recorded_value)
        if not recorded.is_finite() or recorded != estimate:
            raise ValueError("recorded_value must represent the same estimate in the same unit")
    if digits is None:
        digits = 2
        if recorded_value is not None and recorded.as_tuple().exponent >= uncertainty.adjusted():
            digits = 1

    exponent = uncertainty.adjusted() - digits + 1
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, digits + 1, estimate.adjusted() - exponent + 2)
        quantum = Decimal((0, (1,), exponent))
        shown_u = uncertainty.quantize(quantum, rounding=ROUND_HALF_EVEN)
        # A carry can change the leading place, e.g. 0.0999 -> 0.10 (two digits).
        if shown_u.adjusted() > uncertainty.adjusted():
            quantum = Decimal((0, (1,), exponent + 1))
            shown_u = uncertainty.quantize(quantum, rounding=ROUND_HALF_EVEN)
        shown_value = estimate.quantize(quantum, rounding=ROUND_HALF_EVEN)

    if shown_value.is_zero():
        shown_value = shown_value.copy_abs()
    if quantum.as_tuple().exponent > 0 or (quantum == 1 and shown_u >= 10 and shown_u.as_tuple().digits[-1] == 0):
        return format(shown_value, "E"), format(shown_u, "E")
    return format(shown_value, "f"), format(shown_u, "f")
