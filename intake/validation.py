"""Validate the entire incoming register before either store is changed."""

from collections.abc import Mapping

from .request import DISTRICTS, ObservationRow, PartitionReplacement, Submission


class InvalidSubmission(ValueError):
    """The incoming register cannot be admitted."""


def _fields(value, model, fields, label):
    if isinstance(value, model):
        return tuple(getattr(value, field) for field in fields)
    if isinstance(value, Mapping) and set(value) == set(fields):
        return tuple(value[field] for field in fields)
    raise InvalidSubmission(f'{label} must have fields {", ".join(fields)}')


def _check_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise InvalidSubmission(f'{label} must be nonempty text without NUL')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise InvalidSubmission(f'{label} must be valid Unicode text') from error


def validate_row(row: ObservationRow | Mapping) -> ObservationRow:
    number, observation, reading = _fields(
        row, ObservationRow, ('line_number', 'observation_id', 'reading'), 'row'
    )
    if type(number) is not int or number < 1:
        raise InvalidSubmission('line_number must be a positive integer')
    _check_text(observation, 'observation_id')
    if type(reading) is not int:
        raise InvalidSubmission(f'reading for {observation!r} must be an integer')
    return ObservationRow(number, observation, reading)


def validate_submission(value: Submission | Mapping) -> Submission:
    request_id, parent, replacements = _fields(
        value, Submission, ('request_id', 'parent_release', 'replacements'), 'submission'
    )
    _check_text(request_id, 'request_id')
    _check_text(parent, 'parent_release')
    if not isinstance(replacements, (tuple, list)) or not 1 <= len(replacements) <= 2:
        raise InvalidSubmission('submit one or two district replacements')

    validated = []
    seen = set()
    for replacement in replacements:
        district, rows = _fields(
            replacement, PartitionReplacement, ('district', 'rows'), 'replacement'
        )
        if not isinstance(district, str) or district not in DISTRICTS or district in seen:
            raise InvalidSubmission(f'invalid or repeated district {district!r}')
        if not isinstance(rows, (tuple, list)):
            raise InvalidSubmission(f'{district} rows must be an ordered list or tuple')
        validated_rows = []
        for position, row in enumerate(rows, 1):
            try:
                validated_rows.append(validate_row(row))
            except InvalidSubmission as error:
                raise InvalidSubmission(f'{district} input row {position}: {error}') from error
        validated.append(PartitionReplacement(district, tuple(validated_rows)))
        seen.add(district)
    return Submission(request_id, parent, tuple(validated))
