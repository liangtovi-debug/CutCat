"""Conservative reading warnings for the pinned Kokoro frontend; never rewrite text."""
import re

PUNCTUATION_WARNING = 'This text contains quotation marks, repeated ellipses, or full-width brackets. This voice engine may speak punctuation names or ignore pauses. Review the reading text before continuing.'
IDENTIFIER_WARNING = 'This text contains mixed letters and numbers or a dotted version identifier. This voice engine may expand numbers unexpectedly. Review how identifiers should be spoken before continuing.'

def reading_warnings(text: str) -> tuple[str, ...]:
    warnings = []
    if re.search(r'[“”‘’"＂（）［］｛｝]|…{2,}|\.{3,}', text):
        warnings.append(PUNCTUATION_WARNING)
    if re.search(r'[A-Za-z][0-9]|[0-9][A-Za-z]|(?<![0-9.])[0-9]+(?:\.[0-9]+){2,}', text):
        warnings.append(IDENTIFIER_WARNING)
    return tuple(warnings)
