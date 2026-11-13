from django.db import migrations


def backfill(apps, schema_editor):
    AirMissionDetail = apps.get_model("ops", "AirMissionDetail")
    AirMissionElement = apps.get_model("ops", "AirMissionElement")
    AirMissionAircraft = apps.get_model("ops", "AirMissionAircraft")
    AirMissionCrew = apps.get_model("ops", "AirMissionCrew")
    AirMissionItinerary = apps.get_model("ops", "AirMissionItinerary")

    for detail in AirMissionDetail.objects.select_related("mission", "air_asset"):
        if AirMissionElement.objects.filter(air_detail_id=detail.pk).exists():
            continue

        operation_date = (
            detail.operation_date
            or (detail.scheduled_departure.date() if detail.scheduled_departure else None)
            or (detail.mission.start_at.date() if detail.mission.start_at else None)
        )
        if operation_date and detail.operation_date != operation_date:
            detail.operation_date = operation_date
            detail.save(update_fields=["operation_date"])

        element = AirMissionElement.objects.create(
            air_detail_id=detail.pk,
            sequence=1,
            mission="",
        )

        legacy_aircraft = list(
            detail.assigned_aircraft.all().order_by("sequence", "id")
        )
        if not legacy_aircraft:
            legacy_aircraft = [None]

        for index, old in enumerate(legacy_aircraft, start=1):
            if old is None:
                aircraft_type = detail.aircraft_type
                tail_number = detail.aircraft_tail_number
                callsign = detail.flight_identification
            else:
                aircraft_type = old.aircraft_model or detail.aircraft_type
                tail_number = old.tail_number or detail.aircraft_tail_number
                callsign = old.callsign or detail.flight_identification

            aircraft = AirMissionAircraft.objects.create(
                element_id=element.pk,
                sequence=index,
                aircraft_type=aircraft_type or "",
                tail_number=tail_number or "",
                callsign=callsign or "",
                is_lead=index == 1,
            )

            if old is not None:
                crew_sequence = 1
                if old.pilot_in_command:
                    AirMissionCrew.objects.create(
                        aircraft_id=aircraft.pk,
                        sequence=crew_sequence,
                        role="PIC",
                        name=old.pilot_in_command,
                    )
                    crew_sequence += 1
                if old.co_pilot:
                    AirMissionCrew.objects.create(
                        aircraft_id=aircraft.pk,
                        sequence=crew_sequence,
                        role="CP",
                        name=old.co_pilot,
                    )

            AirMissionItinerary.objects.create(
                aircraft_id=aircraft.pk,
                sequence=1,
                etd=detail.scheduled_departure,
                eta=detail.scheduled_arrival,
                origin=detail.departure_location or "",
                destination=detail.destination_location or "",
                route=detail.route or "",
                mission="",
                status="planned",
            )


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0032_air_flight_operations"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
