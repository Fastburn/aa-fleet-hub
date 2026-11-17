"""
Run with: python manage.py shell < load_types.py
Synchronously fetches EVE type names for all skill/ship IDs used in seed data.
"""

from eveuniverse.models import EveType

type_ids = [
    # Skills
    3300,
    3301,
    3416,
    3417,
    3418,
    3419,
    3420,
    3421,
    3422,
    3423,
    3424,
    3425,
    3426,
    3427,
    3432,
    3436,
    11083,
    22043,
    3327,
    3413,
    # Ships
    16227,
    620,
    11985,
]

for type_id in type_ids:
    obj, created = EveType.objects.update_or_create_esi(id=type_id)
    print(f"{'Created' if created else 'Updated'}: {obj.name} ({type_id})")

print("Done.")
