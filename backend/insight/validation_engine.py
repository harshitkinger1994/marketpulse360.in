def suppress_if_conflicting(*signals):
    """
    If signals disagree strongly, downgrade confidence.
    """
    uniq = set(s for s in signals if s)
    if len(uniq) > 2:
        return {"confidence": "LOW", "suppressed": True}
    return {"confidence": "MEDIUM", "suppressed": False}
