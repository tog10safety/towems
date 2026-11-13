"""Airports and air strips for the operational map.

The register is reference data: ICAO/local code, name, municipality and
province, coordinates, derived MGRS, runway dimensions and orientation. The
seed covers the CAAP international and principal domestic airports plus the
active military and community strips; coordinates are WGS84 and runway figures
are the published lengths in metres.
"""

from django.db import migrations, models


# code, name, kind, municipality, province, lat, lon, length_m, width_m, orientation
AIRFIELDS = [
    # -- International -------------------------------------------------------
    ("RPLL", "Ninoy Aquino International Airport", "airport", "Pasay / Parañaque", "Metro Manila", 14.5086, 121.0198, 3737, 60, "06/24"),
    ("RPLC", "Clark International Airport", "airport", "Mabalacat", "Pampanga", 15.1860, 120.5600, 3200, 60, "02/20"),
    ("RPVM", "Mactan-Cebu International Airport", "airport", "Lapu-Lapu City", "Cebu", 10.3075, 123.9794, 3300, 45, "04/22"),
    ("RPMD", "Francisco Bangoy International Airport", "airport", "Davao City", "Davao del Sur", 7.1255, 125.6458, 3000, 45, "05/23"),
    ("RPVK", "Kalibo International Airport", "airport", "Kalibo", "Aklan", 11.6794, 122.3756, 2500, 45, "05/23"),
    ("RPVE", "Godofredo P. Ramos Airport (Caticlan)", "airport", "Malay", "Aklan", 11.9245, 121.9539, 1800, 45, "06/24"),
    ("RPSP", "Bohol-Panglao International Airport", "airport", "Panglao", "Bohol", 9.5736, 123.7711, 2500, 45, "03/21"),
    ("RPVP", "Puerto Princesa International Airport", "airport", "Puerto Princesa", "Palawan", 9.7422, 118.7586, 2600, 45, "09/27"),
    ("RPLI", "Laoag International Airport", "airport", "Laoag", "Ilocos Norte", 18.1781, 120.5317, 2420, 45, "01/19"),
    ("RPMZ", "Zamboanga International Airport", "airport", "Zamboanga City", "Zamboanga del Sur", 6.9224, 122.0596, 2610, 45, "09/27"),
    ("RPVB", "Bacolod-Silay Airport", "airport", "Silay City", "Negros Occidental", 10.7764, 123.0147, 2000, 45, "03/21"),
    ("RPVI", "Iloilo International Airport", "airport", "Cabatuan", "Iloilo", 10.8330, 122.4931, 2500, 45, "02/20"),
    ("RPLB", "Subic Bay International Airport", "airport", "Subic", "Zambales", 14.7944, 120.2714, 2743, 45, "07/25"),
    ("RPMR", "General Santos International Airport", "airport", "General Santos City", "South Cotabato", 6.0581, 125.0961, 3227, 45, "17/35"),
    ("RPMY", "Laguindingan Airport", "airport", "Laguindingan", "Misamis Oriental", 8.6123, 124.4564, 2100, 45, "05/23"),
    ("RPLH", "Cagayan North International Airport", "airport", "Lal-lo", "Cagayan", 18.1833, 121.7500, 2100, 45, "18/36"),
    # -- Principal domestic --------------------------------------------------
    ("RPVA", "Daniel Z. Romualdez Airport", "airport", "Tacloban City", "Leyte", 11.2275, 125.0278, 2138, 45, "18/36"),
    ("RPVF", "Catarman National Airport", "airport", "Catarman", "Northern Samar", 12.5024, 124.6380, 1400, 30, "05/23"),
    ("RPVC", "Calbayog Airport", "airport", "Calbayog City", "Samar", 12.0727, 124.5450, 1600, 30, "17/35"),
    ("RPVO", "Ormoc Airport", "airport", "Ormoc City", "Leyte", 11.0581, 124.5656, 1860, 30, "16/34"),
    ("RPSM", "Maasin Airport", "airport", "Maasin City", "Southern Leyte", 10.1889, 124.7831, 1200, 30, "07/25"),
    ("RPVJ", "Masbate Airport", "airport", "Masbate City", "Masbate", 12.3694, 123.6289, 1200, 30, "04/22"),
    ("RPVW", "Borongan Airport", "airport", "Borongan", "Eastern Samar", 11.6743, 125.4789, 1500, 30, "04/22"),
    ("RPVG", "Guiuan Airport", "airport", "Guiuan", "Eastern Samar", 11.0355, 125.7422, 2130, 30, "04/22"),
    ("RPVH", "Hilongos Airport", "airstrip", "Hilongos", "Leyte", 10.3764, 124.7533, 1200, 30, "18/36"),
    ("RPSB", "Bantayan Airport", "airstrip", "Santa Fe", "Cebu", 11.1617, 123.7849, 1200, 30, "09/27"),
    ("RPVN", "Medellin Airport", "airstrip", "Medellin", "Cebu", 11.1403, 123.9736, 1200, 30, "18/36"),
    ("RPVZ", "Siquijor Airport", "airstrip", "Siquijor", "Siquijor", 9.2139, 123.4722, 1200, 30, "09/27"),
    ("RPSD", "Cesar Lim Rodriguez Airport (Taytay)", "airport", "Taytay", "Palawan", 10.8189, 119.5089, 1600, 30, "09/27"),
    ("RPSV", "San Vicente Airport", "airport", "San Vicente", "Palawan", 10.5281, 119.2739, 1500, 30, "09/27"),
    ("RPSN", "Ubay Airport", "airstrip", "Ubay", "Bohol", 10.0590, 124.4233, 1200, 30, "06/24"),
    ("RPSQ", "Sayak Airport (Siargao)", "airport", "Del Carmen", "Surigao del Norte", 9.8592, 126.0139, 1200, 30, "01/19"),
    ("RPSI", "Surigao Airport", "airport", "Surigao City", "Surigao del Norte", 9.7578, 125.4811, 1500, 30, "18/36"),
    ("RPMW", "Tandag Airport", "airport", "Tandag", "Surigao del Sur", 9.0722, 126.1712, 1400, 30, "04/22"),
    ("RPME", "Bancasi Airport", "airport", "Butuan City", "Agusan del Norte", 8.9514, 125.4786, 2100, 45, "05/23"),
    ("RPML", "Lumbia Airport (Cagayan de Oro)", "airport", "Cagayan de Oro", "Misamis Oriental", 8.4156, 124.6111, 1600, 30, "01/19"),
    ("RPMH", "Camiguin Airport", "airport", "Mambajao", "Camiguin", 9.2535, 124.7070, 1400, 30, "07/25"),
    ("RPMI", "Maria Cristina Airport (Iligan)", "airport", "Iligan City", "Lanao del Norte", 8.1964, 124.2422, 1200, 30, "04/22"),
    ("RPMO", "Labo Airport (Ozamiz)", "airport", "Ozamiz City", "Misamis Occidental", 8.1786, 123.8414, 1740, 45, "03/21"),
    ("RPMP", "Pagadian Airport", "airport", "Pagadian City", "Zamboanga del Sur", 7.8256, 123.4594, 1700, 45, "04/22"),
    ("RPMQ", "Malabang Airport", "airstrip", "Malabang", "Lanao del Sur", 7.6175, 124.0586, 1500, 30, "07/25"),
    ("RPMJ", "Jolo Airport", "airport", "Jolo", "Sulu", 6.0536, 121.0111, 1200, 30, "09/27"),
    ("RPMN", "Sanga-Sanga Airport (Bongao)", "airport", "Bongao", "Tawi-Tawi", 5.0269, 119.7428, 1500, 30, "09/27"),
    ("RPMU", "Mapun Airport", "airstrip", "Mapun", "Tawi-Tawi", 7.0139, 118.4961, 1200, 30, "04/22"),
    ("RPMC", "Awang Airport (Cotabato)", "airport", "Datu Odin Sinsuat", "Maguindanao del Norte", 7.1647, 124.2097, 1900, 45, "10/28"),
    ("RPMF", "Bislig Airport", "airport", "Bislig City", "Surigao del Sur", 8.1939, 126.3211, 1300, 30, "05/23"),
    ("RPMA", "Allah Valley Airport", "airport", "Surallah", "South Cotabato", 6.3667, 124.7500, 1200, 30, "09/27"),
    ("RPMT", "Del Monte Airfield", "airstrip", "Manolo Fortich", "Bukidnon", 8.3578, 124.9431, 1000, 25, "05/23"),
    # -- Luzon community strips ---------------------------------------------
    ("RPUB", "Loakan Airport (Baguio)", "airport", "Baguio City", "Benguet", 16.3753, 120.6197, 1290, 30, "09/27"),
    ("RPUO", "Basco Airport", "airport", "Basco", "Batanes", 20.4514, 121.9800, 1250, 30, "04/22"),
    ("RPLT", "Itbayat Airport", "airstrip", "Itbayat", "Batanes", 20.7833, 121.8333, 1000, 25, "09/27"),
    ("RPUA", "Aparri Airport", "airport", "Aparri", "Cagayan", 18.3567, 121.7292, 1500, 30, "07/25"),
    ("RPLN", "Palanan Airport", "airstrip", "Palanan", "Isabela", 17.0667, 122.4283, 1000, 25, "09/27"),
    ("RPUY", "Cauayan Airport", "airport", "Cauayan City", "Isabela", 16.9297, 121.7531, 2100, 45, "01/19"),
    ("RPUZ", "Bagabag Airport", "airstrip", "Bagabag", "Nueva Vizcaya", 16.6194, 121.2553, 1200, 30, "09/27"),
    ("RPUS", "San Fernando Airport", "airstrip", "San Fernando", "La Union", 16.5967, 120.3033, 1200, 30, "01/19"),
    ("RPUG", "Lingayen Airport", "airstrip", "Lingayen", "Pangasinan", 16.0350, 120.2411, 1000, 30, "08/26"),
    ("RPUI", "Iba Airport", "airstrip", "Iba", "Zambales", 15.3278, 119.9811, 1000, 30, "09/27"),
    ("RPUJ", "Castillejos Airport", "airstrip", "Castillejos", "Zambales", 14.9333, 120.2000, 1000, 25, "09/27"),
    ("RPLR", "Rosales Airport", "airstrip", "Rosales", "Pangasinan", 15.8836, 120.5858, 1000, 25, "09/27"),
    ("RPUC", "Cabanatuan Airport", "airstrip", "Cabanatuan City", "Nueva Ecija", 15.4994, 121.0106, 1200, 30, "09/27"),
    ("RPLV", "Fort Magsaysay Airfield", "airstrip", "Palayan City", "Nueva Ecija", 15.4333, 121.1000, 1200, 30, "09/27"),
    ("RPLQ", "Crow Valley Airfield", "airstrip", "Capas", "Tarlac", 15.3167, 120.3667, 1500, 30, "09/27"),
    ("RPUE", "Lucena Airport", "airstrip", "Lucena City", "Quezon", 13.9333, 121.6167, 1000, 25, "09/27"),
    ("RPLJ", "Jomalig Airport", "airstrip", "Jomalig", "Quezon", 14.7053, 122.3728, 1000, 25, "09/27"),
    ("RPLY", "Alabat Airport", "airstrip", "Alabat", "Quezon", 14.1138, 122.0497, 1000, 25, "09/27"),
    ("RPLX", "Corregidor Airport", "airstrip", "Cavite City", "Cavite", 14.3850, 120.5800, 1000, 25, "09/27"),
    ("RPLS", "Sangley Point Air Base", "airstrip", "Cavite City", "Cavite", 14.4953, 120.9039, 2000, 45, "07/25"),
    ("RPUF", "Basa Air Base", "airport", "Floridablanca", "Pampanga", 14.9864, 120.4897, 2500, 45, "02/20"),
    ("RPUD", "Bagasbas Airport (Daet)", "airport", "Daet", "Camarines Norte", 14.1292, 122.9803, 1200, 30, "05/23"),
    ("RPUN", "Naga Airport", "airport", "Pili", "Camarines Sur", 13.5853, 123.2700, 1400, 30, "06/24"),
    ("RPLP", "Legazpi Airport", "airport", "Legazpi City", "Albay", 13.1575, 123.7306, 2280, 45, "05/23"),
    ("RPUU", "Bulan Airport", "airstrip", "Bulan", "Sorsogon", 12.6833, 123.8769, 1200, 30, "09/27"),
    ("RPLZ", "Sorsogon Airport", "airstrip", "Sorsogon City", "Sorsogon", 12.9789, 123.9847, 1200, 30, "09/27"),
    ("RPUR", "Baler Airport", "airstrip", "Baler", "Aurora", 15.7294, 121.5000, 1200, 30, "09/27"),
    ("RPUK", "Calapan Airport", "airstrip", "Calapan", "Oriental Mindoro", 13.4231, 121.1903, 1200, 30, "09/27"),
    ("RPLA", "Pinamalayan Airport", "airstrip", "Pinamalayan", "Oriental Mindoro", 13.0167, 121.4667, 1000, 25, "09/27"),
    ("RPLG", "Wasig Airport", "airstrip", "Mansalay", "Oriental Mindoro", 12.5333, 121.4500, 1000, 25, "09/27"),
    ("RPUM", "Mamburao Airport", "airstrip", "Mamburao", "Occidental Mindoro", 13.2081, 120.6053, 1300, 30, "09/27"),
    ("RPLU", "Lubang Airport", "airstrip", "Lubang", "Occidental Mindoro", 13.8553, 120.1050, 1200, 30, "09/27"),
    ("RPUW", "Marinduque Airport", "airport", "Gasan", "Marinduque", 13.3611, 121.8250, 1200, 30, "09/27"),
    ("RPLO", "Cuyo Airport", "airport", "Cuyo", "Palawan", 10.8581, 121.0139, 1200, 30, "09/27"),
]


def seed(apps, schema_editor):
    Airfield = apps.get_model("ops", "Airfield")
    from apps.core.mgrs import MGRSError, to_mgrs

    for code, name, kind, municipality, province, lat, lon, length, width, orientation in AIRFIELDS:
        try:
            grid = to_mgrs(lat, lon, 5)
        except MGRSError:  # pragma: no cover - coordinates are in range
            grid = ""
        Airfield.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "kind": kind,
                "municipality": municipality,
                "province": province,
                "latitude": lat,
                "longitude": lon,
                "mgrs": grid,
                "runway_length_m": length,
                "runway_width_m": width,
                "runway_orientation": orientation,
            },
        )


def unseed(apps, schema_editor):
    Airfield = apps.get_model("ops", "Airfield")
    Airfield.objects.filter(code__in=[row[0] for row in AIRFIELDS]).delete()


class Migration(migrations.Migration):
    dependencies = [("ops", "0017_mission_mission_order_assigned_at_and_more")]

    operations = [
        migrations.CreateModel(
            name="Airfield",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(help_text="ICAO or local code, e.g. RPMY.", max_length=10, unique=True)),
                ("name", models.CharField(max_length=160)),
                ("kind", models.CharField(choices=[("airport", "Airport"), ("airstrip", "Air strip")], default="airport", max_length=10)),
                ("municipality", models.CharField(blank=True, default="", max_length=120)),
                ("province", models.CharField(blank=True, default="", max_length=120)),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
                ("mgrs", models.CharField(blank=True, default="", max_length=32)),
                ("runway_length_m", models.PositiveIntegerField(blank=True, null=True)),
                ("runway_width_m", models.PositiveIntegerField(blank=True, null=True)),
                ("runway_orientation", models.CharField(blank=True, default="", max_length=20)),
                ("elevation_m", models.IntegerField(blank=True, null=True)),
                ("surface", models.CharField(blank=True, default="", max_length=40)),
                ("operator", models.CharField(blank=True, default="", max_length=120)),
                ("notes", models.CharField(blank=True, default="", max_length=255)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddIndex(
            model_name="airfield",
            index=models.Index(fields=["kind"], name="ops_airfield_kind_idx"),
        ),
        migrations.RunPython(seed, unseed),
    ]
