"""Identify admitted request parameters without putting source rows in the catalog."""

from hashlib import sha256
import json

from .request import Submission


def request_identity(submission: Submission) -> str:
    """Fingerprint the validated, normalized parameters of a named submission."""
    replacements = [
        [replacement.district, [
            [format(row.line_number, 'x'), row.observation_id, format(row.reading, 'x')]
            for row in replacement.rows
        ]]
        for replacement in submission.replacements
    ]
    payload = json.dumps(
        ['register-v1', submission.parent_release, replacements],
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return sha256(payload).hexdigest()


def matches_request(submission: Submission, recorded_identity: str) -> bool:
    return request_identity(submission) == recorded_identity
