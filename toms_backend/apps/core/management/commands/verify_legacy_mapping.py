"""Verify that the Django models match the live legacy schema.

Read-only: inspects catalog metadata and counts rows. It never writes, alters
or locks anything.

    python manage.py verify_legacy_mapping            # report
    python manage.py verify_legacy_mapping --strict   # exit 1 on any mismatch

Run it against staging first, then against production in report-only mode
before cutover (docs/MIGRATION_PLAN.md §6).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def legacy_models():
    from apps.accounts.models import User
    from apps.legacy import models as legacy

    return [
        ("users", User),
        ("units", legacy.Unit),
        ("unit_assignments", legacy.UnitAssignment),
        ("unit_logos", legacy.UnitLogo),
        ("hazard_registry", legacy.HazardRegistry),
        ("mrm_forms", legacy.MRMForm),
        ("mission_hazards", legacy.MissionHazards),
        ("mrm_personnel", legacy.MRMPersonnel),
        ("mrm_feedback", legacy.MRMFeedback),
        ("feedback_notifications", legacy.FeedbackNotification),
        ("user_activities", legacy.UserActivity),
    ]


class Command(BaseCommand):
    help = "Verify legacy table mappings (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Exit non-zero on any issue.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    f"Connected to {connection.vendor!r}; catalog checks are most "
                    "meaningful against PostgreSQL (staging/production)."
                )
            )

        existing_tables = set(connection.introspection.table_names())
        issues: list[str] = []
        self.stdout.write(self.style.MIGRATE_HEADING("Legacy table mapping verification"))
        self.stdout.write(f"Database: {connection.settings_dict['NAME']}\n")

        total_rows = 0
        for table, model in legacy_models():
            meta = model._meta
            if meta.db_table != table:
                issues.append(f"{table}: model declares db_table {meta.db_table!r}")
                continue
            if meta.managed:
                issues.append(f"{table}: model is managed=True (must be False)")

            if table not in existing_tables:
                issues.append(f"{table}: TABLE MISSING in database")
                self.stdout.write(f"  {table:24s} MISSING")
                continue

            with connection.cursor() as cursor:
                description = connection.introspection.get_table_description(cursor, table)
                db_columns = {column.name for column in description}
                nullability = {
                    column.name: bool(column.null_ok) for column in description
                }
                # Only varchar columns have a meaningful length; PostgreSQL
                # ENUM columns report their storage size (4 bytes) instead.
                sizes = {
                    column.name: column.internal_size
                    for column in description
                    if column.type_code == 1043  # varchar
                }
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                row_count = cursor.fetchone()[0]
            total_rows += row_count

            model_columns = {field.column for field in meta.fields}
            unmapped = db_columns - model_columns
            missing = model_columns - db_columns

            if unmapped:
                issues.append(f"{table}: DB columns not mapped by Django -> {sorted(unmapped)}")
            if missing:
                issues.append(
                    f"{table}: model fields with no DB column -> {sorted(missing)}"
                )

            # Nullability must match: a model that allows NULL where the DB
            # forbids it produces IntegrityErrors only at write time.
            for field in meta.fields:
                if field.column not in db_columns:
                    continue
                db_nullable = nullability.get(field.column, True)
                if field.null and not db_nullable and not field.has_default() and not field.primary_key:
                    issues.append(
                        f"{table}.{field.column}: model allows NULL but DB is NOT NULL"
                    )
                # Character length must not be shorter than the column.
                max_length = getattr(field, "max_length", None)
                db_size = sizes.get(field.column)
                if max_length and isinstance(db_size, int) and db_size > 0 and max_length < db_size:
                    issues.append(
                        f"{table}.{field.column}: model max_length {max_length} < DB {db_size}"
                    )

            pk_columns = [f.column for f in meta.fields if f.primary_key]
            if pk_columns and pk_columns[0] not in db_columns:
                issues.append(f"{table}: primary key column {pk_columns[0]!r} absent")

            status = "OK" if not (unmapped or missing) else "CHECK"
            self.stdout.write(
                f"  {table:24s} {status:5s} columns={len(db_columns):2d} rows={row_count}"
            )

        # Enum label drift (D1): every choice we might write must exist in the
        # live PostgreSQL enum, otherwise the insert fails at runtime.
        if connection.vendor == "postgresql":
            self.stdout.write("\nEnum label check")
            from apps.legacy.models import UserActivity

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT e.enumlabel
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'module_enum'
                    """
                )
                live = {row[0] for row in cursor.fetchall()}
            declared = {choice[0] for choice in UserActivity._meta.get_field("module").choices}
            unknown = declared - live
            if unknown:
                issues.append(
                    f"module_enum: choices not present in the live enum -> {sorted(unknown)}"
                )
                self.stdout.write(f"  module_enum MISMATCH {sorted(unknown)}")
            else:
                self.stdout.write(f"  module_enum OK ({len(declared)} labels)")

        self.stdout.write("\nSummary")
        self.stdout.write(f"  Legacy rows counted: {total_rows}")
        if issues:
            self.stdout.write(self.style.ERROR(f"  Issues: {len(issues)}"))
            for issue in issues:
                self.stdout.write(self.style.ERROR(f"   - {issue}"))
            if options["strict"]:
                raise CommandError("Legacy mapping verification failed.")
        else:
            self.stdout.write(self.style.SUCCESS("  All legacy mappings verified."))
