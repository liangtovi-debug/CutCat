"""Historical schema fixture conversion, not production migration."""
from copy import deepcopy
_FIELDS = tuple(f'{channel}_softness_{edge}' for channel in ('saturation', 'luma') for edge in ('low', 'high'))
def strip_asymmetric_fields(record):
    for name in (*_FIELDS, 'hue_enabled', 'saturation_enabled', 'luma_enabled'): record.pop(name, None)
    for key in ('inclusion_ranges', 'exclusion_ranges'):
        for entry in record.get(key, []): strip_asymmetric_fields(entry)
    return record

def inherited_asymmetric_fields(record):
    record = deepcopy(record)
    for name in ('hue_enabled', 'saturation_enabled', 'luma_enabled'): record.setdefault(name, True)
    for name in _FIELDS: record.setdefault(name, None)
    for key in ('inclusion_ranges', 'exclusion_ranges'):
        if key in record:
            record[key] = [inherited_asymmetric_fields(entry) for entry in record[key]]
    return record
