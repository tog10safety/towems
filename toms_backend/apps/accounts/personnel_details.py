"""Personnel detail choices and helpers.

The legacy ``users`` table is unmanaged and must not be altered.  Details that
the original schema never carried live in the additive ``UserProfile`` side
table instead.  Keeping the choice lists and the serialisation helper in one
place means the API, the Excel template and the import all agree.
"""

from __future__ import annotations

SEX_CHOICES = [
    ("Male", "Male"),
    ("Female", "Female"),
]

BLOOD_TYPE_CHOICES = [
    ("A+", "A+"),
    ("A-", "A-"),
    ("B+", "B+"),
    ("B-", "B-"),
    ("AB+", "AB+"),
    ("AB-", "AB-"),
    ("O+", "O+"),
    ("O-", "O-"),
]

CIVIL_STATUS_CHOICES = [
    ("Single", "Single"),
    ("Married", "Married"),
    ("Widowed", "Widowed"),
    ("Separated", "Separated"),
    ("Annulled", "Annulled"),
]

HIGHEST_EDUCATION_CHOICES = [
    ("Elementary", "Elementary"),
    ("High School", "High School"),
    ("Senior High School", "Senior High School"),
    ("Vocational / Technical", "Vocational / Technical"),
    ("Associate Degree", "Associate Degree"),
    ("Bachelor's Degree", "Bachelor's Degree"),
    ("Master's Degree", "Master's Degree"),
    ("Doctorate", "Doctorate"),
]

#: Highest first - matches the personnel pickers in the SPA.
RANK_OPTIONS = [
    "LTGEN",
    "MGEN",
    "BGEN",
    "COL",
    "LTC",
    "MAJ",
    "CPT",
    "1LT",
    "2LT",
    "CMSg",
    "SMS",
    "MSg",
    "TSg",
    "SSg",
    "Sgt",
    # The airman ranks are listed individually, in this order, so the picker
    # reflects the unit's classification rather than a combined "A1C or AW1C".
    "A1C",
    "AW1C",
    "A2C",
    "AW2C",
    "AM",
    "AW",
]

#: Suggested designations.  ``users.designation`` remains free text, so these
#: are seeded into the Excel dropdown but are not the only accepted values.
DESIGNATION_OPTIONS = [
    "Commander",
    "Deputy Commander",
    "Director for Operations",
    "Operations Officer",
    "Safety Officer",
    "Logistics Officer",
    "Logistic NCOIC",
    "Admin Officer",
    "Personnel",
]

#: Designations retired from the picker. Existing records keep the value, but
#: neither the add/edit form nor the Excel template offers it any more. "Other"
#: is the old name of "Personnel"; the data migration accounts/0007 renames the
#: records that still carry it.
RETIRED_DESIGNATIONS = (
    "Unit Safety Officer",
    "Evaluator",
    "Creator",
    "Operations Center",
    "Dashboard Debugging",
    "Administrator",
    "Other",
)

BRANCH_OPTIONS = ["PAF"]

#: Profile columns that are part of a personnel record.  ``onboarding`` fields
#: are deliberately not here: they are workflow flags, not personnel details.
PERSONNEL_DETAIL_FIELDS = (
    "email",
    "sex",
    "date_of_birth",
    "blood_type",
    "home_address",
    "emergency_contact_name",
    "emergency_contact_number",
    "date_enlisted",
    "date_commissioned",
    "afsc_primary",
    "afsc_secondary",
    "afsc_tertiary",
    "highest_education",
    "civil_status",
)

DATE_DETAIL_FIELDS = ("date_of_birth", "date_enlisted", "date_commissioned")

#: Administrators may maintain these; the account owner may not.
ADMIN_ONLY_DETAIL_FIELDS = ("date_enlisted", "date_commissioned")

#: Fields the account owner may edit through ``/auth/profile/``.
SELF_EDITABLE_DETAIL_FIELDS = tuple(
    field for field in PERSONNEL_DETAIL_FIELDS if field not in ADMIN_ONLY_DETAIL_FIELDS
)


def blank_profile_details() -> dict:
    """The shape of a missing profile, so serializers never emit ``null``."""
    data: dict = {}
    for field in PERSONNEL_DETAIL_FIELDS:
        data[field] = None if field in DATE_DETAIL_FIELDS else ""
    data["onboarding_required"] = False
    data["onboarding_completed_at"] = None
    return data


def profile_dict(profile) -> dict:
    """Flat representation of a ``UserProfile`` (or empty defaults)."""
    data = blank_profile_details()
    if profile is None:
        return data

    for field in PERSONNEL_DETAIL_FIELDS:
        value = getattr(profile, field, None)
        if field in DATE_DETAIL_FIELDS:
            data[field] = value.isoformat() if value else None
        else:
            data[field] = value or ""

    data["onboarding_required"] = bool(
        profile.onboarding_required and not profile.onboarding_completed_at
    )
    completed = profile.onboarding_completed_at
    data["onboarding_completed_at"] = completed.isoformat() if completed else None
    return data


def choices_values(choices) -> list[str]:
    return [value for value, _label in choices]
