"""Detect known field-label mistakes; this is not a general answer judge."""
import re


def field_label_errors(answer):
    errors = []
    for line in (answer or '').splitlines():
        # Inspect individual table cells or short clauses, not an entire paragraph.
        for clause in re.split(r'[|；;。]', line):
            if re.search(r'\b(?:total_)?amount\b', clause) and '金额' in clause:
                # Mixed quantity/money explanations require human review instead.
                if not re.search(r'\b(?:total_)?sum\b', clause):
                    errors.append(clause.strip())
    return errors
