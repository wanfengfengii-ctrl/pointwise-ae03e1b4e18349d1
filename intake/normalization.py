"""Give equivalent station registers the same canonical order."""

from .request import DISTRICTS, PartitionReplacement, Submission, row_order_key


def normalize_submission(submission: Submission) -> Submission:
    by_district = {replacement.district: replacement for replacement in submission.replacements}
    ordered = tuple(
        PartitionReplacement(district, tuple(sorted(by_district[district].rows, key=row_order_key)))
        for district in DISTRICTS
        if district in by_district
    )
    return Submission(submission.request_id, submission.parent_release, ordered)
